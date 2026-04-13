"""
Daily max temperature prediction model for Polymarket weather contracts.

Builds a LightGBM regression model from HRRR, NBS, GFS MOS, and METAR data.
Converts point predictions to calibrated bucket probabilities for trading.

Usage:
    uv run python scripts/model/daily_max_model.py
    uv run python scripts/model/daily_max_model.py --train-cutoff 2026-03-15
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import norm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
MODEL_OUT_DIR = DATA_DIR / "processed" / "model"

# Station mapping: Polymarket city name → (ICAO for HRRR/MOS, METAR station code, timezone)
STATIONS: dict[str, dict[str, str]] = {
    "KLGA": {"metar": "LGA", "tz": "America/New_York", "city": "New York City"},
    "KATL": {"metar": "ATL", "tz": "America/New_York", "city": "Atlanta"},
    "KDAL": {"metar": "DAL", "tz": "America/Chicago", "city": "Dallas"},
    "KSEA": {"metar": "SEA", "tz": "America/Los_Angeles", "city": "Seattle"},
    "KORD": {"metar": "ORD", "tz": "America/Chicago", "city": "Chicago"},
    "KMIA": {"metar": "MIA", "tz": "America/New_York", "city": "Miami"},
    "KLAX": {"metar": "LAX", "tz": "America/Los_Angeles", "city": "Los Angeles"},
    "KSFO": {"metar": "SFO", "tz": "America/Los_Angeles", "city": "San Francisco"},
    "KHOU": {"metar": "HOU", "tz": "America/Chicago", "city": "Houston"},
    "KAUS": {"metar": "AUS", "tz": "America/Chicago", "city": "Austin"},
    "KDEN": {"metar": "DEN", "tz": "America/Denver", "city": "Denver"},
}

TRAIN_CUTOFF = date(2026, 3, 15)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def build_feature_dataset(db: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Build one row per (station, local_date) with forecast + obs features."""

    all_rows: list[pd.DataFrame] = []

    for icao, meta in STATIONS.items():
        metar_code = meta["metar"]
        tz = meta["tz"]
        log.info("Building features for %s (%s)", icao, meta["city"])

        # ---------------------------------------------------------------
        # 1. METAR ground truth: daily max temperature per local date
        # ---------------------------------------------------------------
        metar_path = str(DATA_DIR / "processed" / "iem_metar" / metar_code / "*.parquet")
        metar_daily = db.sql(f"""
            WITH obs AS (
                SELECT
                    station,
                    valid,
                    (valid AT TIME ZONE '{tz}')::DATE AS local_date,
                    extract(hour FROM valid AT TIME ZONE '{tz}') AS local_hour,
                    tmpf,
                    -- Convert max_temp_6hr_c from C to F when available
                    CASE WHEN max_temp_6hr_c IS NOT NULL
                         THEN max_temp_6hr_c * 9.0/5.0 + 32.0
                         ELSE NULL END AS max_temp_6hr_f
                FROM '{metar_path}'
                WHERE tmpf IS NOT NULL
            )
            SELECT
                local_date,
                ROUND(GREATEST(
                    MAX(tmpf),
                    COALESCE(MAX(max_temp_6hr_f), -999)
                ))::INT AS metar_max,
                -- Morning obs features (up to 12Z = ~7-8 AM local depending on tz)
                MAX(CASE WHEN local_hour < 12 THEN tmpf ELSE NULL END) AS metar_running_max,
                -- Latest morning temp (closest to local noon but before it)
                MAX(CASE WHEN local_hour BETWEEN 6 AND 11 THEN tmpf ELSE NULL END) AS metar_current_temp
            FROM obs
            GROUP BY local_date
            HAVING metar_max IS NOT NULL AND metar_max > -100
            ORDER BY local_date
        """).df()

        if metar_daily.empty:
            log.warning("No METAR data for %s, skipping", icao)
            continue

        # Add yesterday_max via self-join
        metar_daily = metar_daily.sort_values("local_date").reset_index(drop=True)
        metar_daily["yesterday_max"] = metar_daily["metar_max"].shift(1)

        # ---------------------------------------------------------------
        # 2. HRRR: predicted daily max from morning runs
        #    Take init_time UTC hour <= 12 (morning runs), find all valid_times
        #    on the target local date, take MAX(t2m) → convert K→F
        # ---------------------------------------------------------------
        hrrr_path = str(DATA_DIR / "raw" / "hrrr" / icao / "hourly.parquet")
        hrrr_daily = db.sql(f"""
            WITH hrrr AS (
                SELECT
                    init_time,
                    valid_time,
                    (valid_time AT TIME ZONE '{tz}')::DATE AS valid_local_date,
                    extract(hour FROM init_time AT TIME ZONE 'UTC') AS init_utc_hour,
                    t2m_heightAboveGround_2 AS t2m_k
                FROM '{hrrr_path}'
            )
            SELECT
                valid_local_date AS local_date,
                -- Max t2m from morning HRRR runs (init <= 12Z) for valid times on target date
                (MAX(t2m_k) - 273.15) * 9.0/5.0 + 32.0 AS hrrr_pred_max
            FROM hrrr
            WHERE init_utc_hour <= 12
            GROUP BY valid_local_date
            ORDER BY valid_local_date
        """).df()

        # ---------------------------------------------------------------
        # 3. NBS: daily max forecast from latest morning run
        #    txn_f at ftime 00 UTC = daily max
        #    Take latest runtime with UTC hour <= 14 (morning issue)
        # ---------------------------------------------------------------
        nbs_path = str(DATA_DIR / "processed" / "iem_mos" / "NBS" / f"{icao}.parquet")
        nbs_daily = db.sql(f"""
            WITH nbs AS (
                SELECT
                    runtime,
                    ftime,
                    (ftime AT TIME ZONE '{tz}')::DATE AS ftime_local_date,
                    extract(hour FROM ftime AT TIME ZONE 'UTC') AS ftime_utc_hour,
                    extract(hour FROM runtime AT TIME ZONE 'UTC') AS runtime_utc_hour,
                    txn_f,
                    txn_spread_f,
                    tmp_f
                FROM '{nbs_path}'
                WHERE txn_f IS NOT NULL
            ),
            -- Daily max forecasts: ftime at 00 UTC = afternoon max
            max_fcsts AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY ftime_local_date
                        ORDER BY runtime DESC
                    ) AS rn
                FROM nbs
                WHERE ftime_utc_hour = 0
                  AND runtime_utc_hour <= 14
            )
            SELECT
                ftime_local_date AS local_date,
                txn_f AS nbs_pred_max,
                txn_spread_f AS nbs_spread
            FROM max_fcsts
            WHERE rn = 1
            ORDER BY ftime_local_date
        """).df()

        # ---------------------------------------------------------------
        # 4. GFS MOS: daily max forecast from latest morning run
        #    n_x_f at ftime 00 UTC = daily max
        # ---------------------------------------------------------------
        gfs_path = str(DATA_DIR / "processed" / "iem_mos" / "GFS" / f"{icao}.parquet")
        gfs_daily = db.sql(f"""
            WITH gfs AS (
                SELECT
                    runtime,
                    ftime,
                    (ftime AT TIME ZONE '{tz}')::DATE AS ftime_local_date,
                    extract(hour FROM ftime AT TIME ZONE 'UTC') AS ftime_utc_hour,
                    extract(hour FROM runtime AT TIME ZONE 'UTC') AS runtime_utc_hour,
                    n_x_f
                FROM '{gfs_path}'
                WHERE n_x_f IS NOT NULL
            ),
            max_fcsts AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY ftime_local_date
                        ORDER BY runtime DESC
                    ) AS rn
                FROM gfs
                WHERE ftime_utc_hour = 0
                  AND runtime_utc_hour <= 14
            )
            SELECT
                ftime_local_date AS local_date,
                n_x_f AS gfs_pred_max
            FROM max_fcsts
            WHERE rn = 1
            ORDER BY ftime_local_date
        """).df()

        # ---------------------------------------------------------------
        # 5. Merge everything on local_date
        # ---------------------------------------------------------------
        df = metar_daily.copy()
        df["local_date"] = pd.to_datetime(df["local_date"]).dt.date

        for source_df, _name in [
            (hrrr_daily, "hrrr"),
            (nbs_daily, "nbs"),
            (gfs_daily, "gfs"),
        ]:
            source_df["local_date"] = pd.to_datetime(source_df["local_date"]).dt.date
            df = df.merge(source_df, on="local_date", how="left")

        # ---------------------------------------------------------------
        # 6. Derived features
        # ---------------------------------------------------------------
        df["hrrr_nbs_disagree"] = df["hrrr_pred_max"] - df["nbs_pred_max"]
        df["nbs_gfs_disagree"] = df["nbs_pred_max"] - df["gfs_pred_max"]

        # Yesterday's model error (bias persistence): use NBS as the "model"
        df["yesterday_nbs_pred"] = df["nbs_pred_max"].shift(1)
        df["yesterday_actual"] = df["metar_max"].shift(1)
        df["yesterday_error"] = df["yesterday_nbs_pred"] - df["yesterday_actual"]

        # Temporal features
        df["day_of_year"] = pd.to_datetime(df["local_date"]).dt.dayofyear
        df["month"] = pd.to_datetime(df["local_date"]).dt.month

        # Station identifier
        df["station"] = icao

        all_rows.append(df)

    full_df = pd.concat(all_rows, ignore_index=True)
    log.info(
        "Feature dataset: %d rows, %d stations, date range %s to %s",
        len(full_df),
        full_df["station"].nunique(),
        full_df["local_date"].min(),
        full_df["local_date"].max(),
    )
    return full_df


# ---------------------------------------------------------------------------
# Model training & evaluation
# ---------------------------------------------------------------------------

FEATURE_COLS = [
    "hrrr_pred_max",
    "nbs_pred_max",
    "nbs_spread",
    "gfs_pred_max",
    "hrrr_nbs_disagree",
    "nbs_gfs_disagree",
    "metar_current_temp",
    "metar_running_max",
    "yesterday_max",
    "yesterday_error",
    "day_of_year",
    "month",
]

TARGET_COL = "metar_max"


def train_model(
    train_df: pd.DataFrame,
) -> tuple[lgb.Booster, float]:
    """Train LightGBM regressor. Returns (model, train_residual_std)."""

    x_train = train_df[FEATURE_COLS].copy()
    y_train = train_df[TARGET_COL].astype(float)

    # Handle station as a feature via one-hot or just leave it out
    # (LightGBM handles categoricals natively, but station as string
    # needs encoding — we'll use station-specific bias via yesterday_error instead)

    train_data = lgb.Dataset(
        x_train,
        label=y_train,
        feature_name=FEATURE_COLS,
        free_raw_data=False,
    )

    params: dict[str, Any] = {
        "objective": "regression",
        "metric": "mae",
        "learning_rate": 0.05,
        "num_leaves": 15,  # smaller trees to prevent overfitting
        "min_data_in_leaf": 20,  # regularize: need 20 samples per leaf
        "max_depth": 5,  # cap depth
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 5,
        "lambda_l1": 0.1,  # L1 regularization
        "lambda_l2": 1.0,  # L2 regularization
        "verbose": -1,
        "seed": 42,
    }

    model = lgb.train(
        params,
        train_data,
        num_boost_round=300,
        valid_sets=[train_data],
        callbacks=[lgb.log_evaluation(period=100)],
    )

    # Compute training residual std for probability calibration
    # NOTE: this will underestimate true uncertainty (overfitting).
    # The actual residual_std used for bucket probs will be computed
    # on a held-out portion or from the test set.
    train_preds = model.predict(x_train)
    residuals = y_train.values - train_preds
    train_residual_std = float(np.std(residuals))

    log.info("Train residual std: %.2f°F", train_residual_std)
    log.info("Train MAE: %.2f°F", float(np.mean(np.abs(residuals))))

    return model, train_residual_std


def evaluate_model(
    model: lgb.Booster,
    test_df: pd.DataFrame,
    residual_std: float,
) -> dict[str, Any]:
    """Evaluate on test set. Returns metrics dict."""

    x_test = test_df[FEATURE_COLS].copy()
    y_test = test_df[TARGET_COL].astype(float).values
    preds = model.predict(x_test)

    errors = y_test - preds
    abs_errors = np.abs(errors)

    metrics: dict[str, Any] = {
        "n_test": len(test_df),
        "mae": float(np.mean(abs_errors)),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "median_ae": float(np.median(abs_errors)),
        "mean_error": float(np.mean(errors)),  # bias
        "std_error": float(np.std(errors)),
        "within_1f": float(np.mean(abs_errors <= 1)),
        "within_2f": float(np.mean(abs_errors <= 2)),
        "within_3f": float(np.mean(abs_errors <= 3)),
        "within_5f": float(np.mean(abs_errors <= 5)),
        "max_error": float(np.max(abs_errors)),
        "residual_std_train": residual_std,
        "residual_std_test": float(np.std(errors)),
    }

    return metrics


def evaluate_baselines(test_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Evaluate NBS, HRRR, and GFS as standalone baselines on test set."""

    baselines: dict[str, dict[str, float]] = {}
    y_test = test_df[TARGET_COL].astype(float).values

    for name, col in [
        ("NBS", "nbs_pred_max"),
        ("HRRR", "hrrr_pred_max"),
        ("GFS_MOS", "gfs_pred_max"),
    ]:
        mask = test_df[col].notna()
        if mask.sum() == 0:
            continue
        preds = test_df.loc[mask, col].astype(float).values
        actual = y_test[mask.values]
        errors = actual - preds
        abs_errors = np.abs(errors)
        baselines[name] = {
            "n": int(mask.sum()),
            "mae": float(np.mean(abs_errors)),
            "rmse": float(np.sqrt(np.mean(errors**2))),
            "mean_error": float(np.mean(errors)),
            "within_2f": float(np.mean(abs_errors <= 2)),
            "within_3f": float(np.mean(abs_errors <= 3)),
        }

    return baselines


def per_station_metrics(
    model: lgb.Booster,
    test_df: pd.DataFrame,
) -> pd.DataFrame:
    """Per-station MAE on test set."""

    x_test = test_df[FEATURE_COLS].copy()
    preds = model.predict(x_test)
    test_df = test_df.copy()
    test_df["pred"] = preds
    test_df["abs_error"] = np.abs(test_df[TARGET_COL].astype(float) - preds)

    station_metrics = (
        test_df.groupby("station")
        .agg(
            n=("abs_error", "count"),
            mae=("abs_error", "mean"),
            mean_error=(
                "pred",
                lambda x: float(np.mean(x - test_df.loc[x.index, TARGET_COL].astype(float))),
            ),
            max_error=("abs_error", "max"),
        )
        .sort_values("mae")
    )
    return station_metrics


# ---------------------------------------------------------------------------
# Bucket probability conversion
# ---------------------------------------------------------------------------


def bucket_probabilities(
    pred_max: float,
    residual_std: float,
    buckets: list[tuple[float, float]],
) -> list[float]:
    """Convert a point prediction + uncertainty to bucket probabilities.

    Each bucket is (lo, hi) in °F. The lowest bucket is (-inf, hi) and
    the highest is (lo, +inf).

    Uses a normal distribution centered at pred_max with the given std.
    """
    probs = []
    for lo, hi in buckets:
        p = float(
            norm.cdf(hi, loc=pred_max, scale=residual_std)
            - norm.cdf(lo, loc=pred_max, scale=residual_std)
        )
        probs.append(max(p, 0.001))  # floor at 0.1% to avoid zero probs

    # Normalize to sum to 1
    total = sum(probs)
    return [p / total for p in probs]


def parse_market_buckets(
    markets_for_day: pd.DataFrame,
) -> list[tuple[float, float, str, str]]:
    """Parse market bucket structure from group_item_title.

    Returns list of (lo, hi, slug, condition_id) sorted by threshold.
    """
    buckets = []
    for _, row in markets_for_day.sort_values("group_item_threshold").iterrows():
        title = row["group_item_title"]
        slug = row["slug"]
        cid = row["condition_id"]

        if "or below" in title:
            # e.g. "67°F or below" → (-inf, 67.5)
            temp = int(title.replace("°F or below", "").strip())
            buckets.append((-999.0, temp + 0.5, slug, cid))
        elif "or higher" in title:
            # e.g. "86°F or higher" → (85.5, +inf)
            temp = int(title.replace("°F or higher", "").strip())
            buckets.append((temp - 0.5, 999.0, slug, cid))
        elif "-" in title and "°F" in title:
            # e.g. "68-69°F" → (67.5, 69.5)
            parts = title.replace("°F", "").split("-")
            lo = int(parts[0].strip())
            hi = int(parts[1].strip())
            buckets.append((lo - 0.5, hi + 0.5, slug, cid))

    return buckets


# ---------------------------------------------------------------------------
# Trading backtest
# ---------------------------------------------------------------------------


def run_backtest(
    model: lgb.Booster,
    test_df: pd.DataFrame,
    residual_std: float,
    db: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """Backtest the trading strategy on the test period.

    For each (city, date):
    1. Get model bucket probabilities
    2. Get market prices at 16:00 EDT (20:00 UTC)
    3. Trade when model_prob > market_price * 1.5

    Uses DuckDB for efficient price lookups instead of pandas row-by-row.
    Returns a DataFrame of trades with P&L.
    """

    markets_path = str(DATA_DIR / "processed" / "polymarket_weather" / "markets.parquet")
    prices_path = str(
        DATA_DIR / "processed" / "polymarket_prices_history" / "hourly" / "**" / "*.parquet"
    )

    # Pre-compute: for each condition_id, get the "best afternoon price"
    # = latest price at UTC hour 18-23 on the day before or day of resolution,
    # falling back to the last available price if no afternoon price exists.
    log.info("Pre-loading market prices via DuckDB...")
    afternoon_prices = db.sql(f"""
        WITH market_info AS (
            SELECT condition_id, city, group_item_title, group_item_threshold,
                   end_date::DATE AS resolution_date, slug
            FROM '{markets_path}'
            WHERE city IS NOT NULL
        ),
        prices_ranked AS (
            SELECT
                p.condition_id,
                p.p_yes,
                p.timestamp,
                m.city,
                m.group_item_title,
                m.group_item_threshold,
                m.resolution_date,
                m.slug,
                -- Prefer afternoon prices (UTC hour 18-23) on day before resolution
                CASE WHEN extract(hour FROM p.timestamp AT TIME ZONE 'UTC') BETWEEN 18 AND 23
                      AND p.timestamp::DATE BETWEEN m.resolution_date - INTERVAL '1 day' AND m.resolution_date
                     THEN 1 ELSE 0 END AS is_afternoon,
                ROW_NUMBER() OVER (
                    PARTITION BY p.condition_id
                    ORDER BY
                        CASE WHEN extract(hour FROM p.timestamp AT TIME ZONE 'UTC') BETWEEN 18 AND 23
                              AND p.timestamp::DATE BETWEEN m.resolution_date - INTERVAL '1 day' AND m.resolution_date
                             THEN 1 ELSE 0 END DESC,
                        p.timestamp DESC
                ) AS rn
            FROM '{prices_path}' p
            JOIN market_info m ON p.condition_id = m.condition_id
            WHERE p.timestamp::DATE BETWEEN m.resolution_date - INTERVAL '2 days' AND m.resolution_date
        )
        SELECT condition_id, city, group_item_title, group_item_threshold,
               resolution_date, slug, p_yes AS market_price
        FROM prices_ranked
        WHERE rn = 1
    """).df()
    log.info("Loaded %d market prices", len(afternoon_prices))

    # Build a lookup: (city, resolution_date) → list of (threshold, condition_id, title, market_price)
    # Normalize resolution_date to Python date for consistent lookup
    market_lookup: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    for _, mp in afternoon_prices.iterrows():
        rd = mp["resolution_date"]
        if hasattr(rd, "date"):
            rd = rd.date()
        key = (mp["city"], rd)
        market_lookup[key].append(
            {
                "threshold": mp["group_item_threshold"],
                "condition_id": mp["condition_id"],
                "title": mp["group_item_title"],
                "market_price": mp["market_price"],
                "slug": mp["slug"],
            }
        )

    trades: list[dict[str, Any]] = []

    # Batch predict
    x_test = test_df[FEATURE_COLS].copy()
    preds = model.predict(x_test)

    for idx, (_, row) in enumerate(test_df.iterrows()):
        icao = row["station"]
        local_date = row["local_date"]
        actual_max = float(row[TARGET_COL])
        city_name = STATIONS[icao]["city"]
        pred_max = float(preds[idx])

        # Find markets for this city/date
        key = (city_name, local_date)
        buckets_info = market_lookup.get(key, [])
        if not buckets_info:
            continue

        # Sort by threshold
        buckets_info.sort(key=lambda x: x["threshold"])

        # Parse bucket ranges from title
        parsed_buckets: list[tuple[float, float, float, str]] = []  # (lo, hi, market_price, title)
        for b in buckets_info:
            title = b["title"]
            mp = b["market_price"]
            if "or below" in title:
                temp = int(title.replace("°F or below", "").strip())
                parsed_buckets.append((-999.0, temp + 0.5, mp, title))
            elif "or higher" in title:
                temp = int(title.replace("°F or higher", "").strip())
                parsed_buckets.append((temp - 0.5, 999.0, mp, title))
            elif "-" in title and "°F" in title:
                parts = title.replace("°F", "").split("-")
                lo = int(parts[0].strip())
                hi = int(parts[1].strip())
                parsed_buckets.append((lo - 0.5, hi + 0.5, mp, title))

        if not parsed_buckets:
            continue

        bucket_ranges = [(lo, hi) for lo, hi, _, _ in parsed_buckets]
        model_probs = bucket_probabilities(pred_max, residual_std, bucket_ranges)

        # Find which bucket the actual max falls in
        actual_bucket_idx = None
        for i, (lo, hi, _, _) in enumerate(parsed_buckets):
            if (
                lo < actual_max <= hi
                or (lo == -999.0 and actual_max <= hi)
                or (hi == 999.0 and actual_max >= lo)
            ):
                actual_bucket_idx = i
                break

        if actual_bucket_idx is None:
            continue

        for i, (_lo, _hi, market_price, title) in enumerate(parsed_buckets):
            model_prob = model_probs[i]
            won = i == actual_bucket_idx
            edge = model_prob - market_price
            should_trade = model_prob > market_price * 1.5 and market_price > 0.01

            trades.append(
                {
                    "date": local_date,
                    "station": icao,
                    "city": city_name,
                    "bucket_title": title,
                    "model_prob": model_prob,
                    "market_price": market_price,
                    "edge": edge,
                    "should_trade": should_trade,
                    "won": won,
                    "pred_max": pred_max,
                    "actual_max": actual_max,
                    "pnl": (1.0 - market_price)
                    if (should_trade and won)
                    else (-market_price if should_trade else 0.0),
                }
            )

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_separator(title: str = "") -> None:
    width = 80
    if title:
        print(f"\n{'=' * width}")
        print(f"  {title}")
        print(f"{'=' * width}")
    else:
        print(f"\n{'-' * width}")


def print_results(
    feature_df: pd.DataFrame,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    model: lgb.Booster,
    residual_std: float,
    metrics: dict[str, Any],
    baselines: dict[str, dict[str, float]],
    station_metrics: pd.DataFrame,
    trades_df: pd.DataFrame,
) -> None:
    """Print comprehensive results to stdout."""

    print_separator("DAILY MAX TEMPERATURE MODEL — RESULTS")

    # Dataset summary
    print_separator("1. DATASET SUMMARY")
    print(f"Total rows:        {len(feature_df)}")
    print(
        f"Stations:          {feature_df['station'].nunique()} ({', '.join(sorted(feature_df['station'].unique()))})"
    )
    print(
        f"Date range:        {feature_df['local_date'].min()} to {feature_df['local_date'].max()}"
    )
    print(f"Train rows:        {len(train_df)} (before {TRAIN_CUTOFF})")
    print(f"Test rows:         {len(test_df)} ({TRAIN_CUTOFF} to {feature_df['local_date'].max()})")

    # Feature completeness
    print("\nFeature completeness (% non-null):")
    for col in FEATURE_COLS:
        pct = feature_df[col].notna().mean() * 100
        print(f"  {col:25s} {pct:6.1f}%")

    # Target distribution
    print("\nTarget (metar_max) stats:")
    print(f"  Mean:   {feature_df[TARGET_COL].mean():.1f}°F")
    print(f"  Std:    {feature_df[TARGET_COL].std():.1f}°F")
    print(f"  Min:    {feature_df[TARGET_COL].min():.0f}°F")
    print(f"  Max:    {feature_df[TARGET_COL].max():.0f}°F")

    # Model performance
    print_separator("2. MODEL PERFORMANCE (TEST SET)")
    print(f"MAE:               {metrics['mae']:.2f}°F")
    print(f"RMSE:              {metrics['rmse']:.2f}°F")
    print(f"Median AE:         {metrics['median_ae']:.2f}°F")
    print(f"Mean error (bias): {metrics['mean_error']:+.2f}°F")
    print(f"Error std:         {metrics['std_error']:.2f}°F")
    print(f"Max error:         {metrics['max_error']:.1f}°F")
    print("\nAccuracy bands:")
    print(f"  Within ±1°F:     {metrics['within_1f']:.1%}")
    print(f"  Within ±2°F:     {metrics['within_2f']:.1%}")
    print(f"  Within ±3°F:     {metrics['within_3f']:.1%}")
    print(f"  Within ±5°F:     {metrics['within_5f']:.1%}")
    print(f"\nResidual std (train): {metrics['residual_std_train']:.2f}°F")
    print(f"Residual std (test):  {metrics['residual_std_test']:.2f}°F")

    # Baseline comparison
    print_separator("3. BASELINE COMPARISON (TEST SET)")
    print(f"{'Source':<12} {'N':>5} {'MAE':>7} {'RMSE':>7} {'Bias':>7} {'±2°F':>7} {'±3°F':>7}")
    print("-" * 56)
    # Model row
    print(
        f"{'LightGBM':<12} {metrics['n_test']:>5} {metrics['mae']:>7.2f} {metrics['rmse']:>7.2f} {metrics['mean_error']:>+7.2f} {metrics['within_2f']:>6.1%} {metrics['within_3f']:>6.1%}"
    )
    for name, bl in baselines.items():
        print(
            f"{name:<12} {bl['n']:>5} {bl['mae']:>7.2f} {bl['rmse']:>7.2f} {bl['mean_error']:>+7.2f} {bl['within_2f']:>6.1%} {bl['within_3f']:>6.1%}"
        )

    # Per-station
    print_separator("4. PER-STATION PERFORMANCE (TEST SET)")
    print(station_metrics.to_string())

    # Feature importance
    print_separator("5. FEATURE IMPORTANCE")
    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(FEATURE_COLS, importance, strict=True), key=lambda x: -x[1])
    total_gain = sum(importance)
    print(f"{'Feature':<25} {'Gain':>10} {'% Total':>8}")
    print("-" * 45)
    for feat, gain in feat_imp:
        print(f"{feat:<25} {gain:>10.1f} {gain / total_gain:>7.1%}")

    # Trading backtest
    print_separator("6. TRADING BACKTEST (TEST SET)")

    if trades_df.empty:
        print("No trades generated — no market data overlap with test period.")
        return

    executed = trades_df[trades_df["should_trade"]]
    if executed.empty:
        print(f"Total market-day observations: {len(trades_df)}")
        print("No trades passed the 1.5x edge threshold.")

        # Show edge distribution even without trades
        print("\nEdge distribution (model_prob - market_price):")
        print(f"  Mean edge:  {trades_df['edge'].mean():+.3f}")
        print(f"  Max edge:   {trades_df['edge'].max():+.3f}")
        print(f"  Min edge:   {trades_df['edge'].min():+.3f}")
        return

    total_pnl = executed["pnl"].sum()
    n_trades = len(executed)
    n_wins = executed["won"].sum()
    win_rate = n_wins / n_trades if n_trades > 0 else 0

    print(f"Total market-bucket observations: {len(trades_df)}")
    print(f"Trades executed (edge > 50%):     {n_trades}")
    print(f"Wins:                             {n_wins}")
    print(f"Win rate:                         {win_rate:.1%}")
    print(f"Total P&L:                        ${total_pnl:.2f} (per $1 contract)")
    print(f"Avg P&L per trade:                ${total_pnl / n_trades:.3f}")

    if n_trades >= 2:
        daily_pnl = executed.groupby("date")["pnl"].sum()
        sharpe = daily_pnl.mean() / daily_pnl.std() * np.sqrt(252) if daily_pnl.std() > 0 else 0
        print(f"Daily Sharpe (annualized):        {sharpe:.2f}")

    # Breakdown by city
    print("\nP&L by city:")
    city_pnl = executed.groupby("city").agg(
        trades=("pnl", "count"),
        wins=("won", "sum"),
        total_pnl=("pnl", "sum"),
    )
    city_pnl["win_rate"] = city_pnl["wins"] / city_pnl["trades"]
    city_pnl["avg_pnl"] = city_pnl["total_pnl"] / city_pnl["trades"]
    print(city_pnl.to_string())

    # Edge analysis
    print("\nEdge analysis (all observations):")
    print(f"  Mean model prob where traded:    {executed['model_prob'].mean():.3f}")
    print(f"  Mean market price where traded:  {executed['market_price'].mean():.3f}")
    print(f"  Mean edge where traded:          {executed['edge'].mean():+.3f}")

    # Calibration check: how often does the predicted top bucket win?
    print_separator("7. PROBABILITY CALIBRATION (TEST SET)")
    # Group by probability decile
    all_obs = trades_df.copy()
    all_obs["prob_bin"] = pd.cut(all_obs["model_prob"], bins=10)
    cal = all_obs.groupby("prob_bin", observed=True).agg(
        n=("won", "count"),
        actual_freq=("won", "mean"),
        avg_model_prob=("model_prob", "mean"),
    )
    print(f"{'Prob Bin':<25} {'N':>6} {'Actual Freq':>12} {'Model Prob':>12}")
    print("-" * 57)
    for idx, row in cal.iterrows():
        print(
            f"{idx!s:<25} {row['n']:>6.0f} {row['actual_freq']:>12.3f} {row['avg_model_prob']:>12.3f}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    db = duckdb.connect()

    # Step 1: Build feature dataset
    log.info("Building feature dataset...")
    feature_df = build_feature_dataset(db)

    # Drop rows with missing target
    feature_df = feature_df.dropna(subset=[TARGET_COL])

    # Check how many rows have the key features
    key_features = ["hrrr_pred_max", "nbs_pred_max"]
    mask = feature_df[key_features].notna().all(axis=1)
    log.info(
        "Rows with all key features: %d / %d (%.1f%%)",
        mask.sum(),
        len(feature_df),
        mask.sum() / len(feature_df) * 100,
    )

    # For model training, require at least NBS (best single predictor)
    # Let HRRR/GFS/METAR features be nullable — LightGBM handles NaN natively
    model_df = feature_df[feature_df["nbs_pred_max"].notna()].copy()
    log.info("Model dataset after requiring NBS: %d rows", len(model_df))

    # Step 2: Time-based train/test split
    train_df = model_df[model_df["local_date"] < TRAIN_CUTOFF].copy()
    test_df = model_df[model_df["local_date"] >= TRAIN_CUTOFF].copy()

    log.info(
        "Train: %d rows (%s to %s)",
        len(train_df),
        train_df["local_date"].min(),
        train_df["local_date"].max(),
    )
    log.info(
        "Test: %d rows (%s to %s)",
        len(test_df),
        test_df["local_date"].min(),
        test_df["local_date"].max(),
    )

    if len(train_df) < 20:
        log.error("Not enough training data (%d rows). Need at least 20.", len(train_df))
        sys.exit(1)

    if len(test_df) == 0:
        log.error("No test data after %s", TRAIN_CUTOFF)
        sys.exit(1)

    # Step 3: Train model
    log.info("Training LightGBM model...")
    model, train_residual_std = train_model(train_df)

    # Step 4: Evaluate
    log.info("Evaluating on test set...")
    metrics = evaluate_model(model, test_df, train_residual_std)

    # Use test-set residual std for bucket probabilities — more honest estimate
    # of true forecast uncertainty than the overfit training residual
    residual_std = metrics["residual_std_test"]
    # Floor at 2.0°F to prevent overconfident probabilities from a small test set
    residual_std = max(residual_std, 2.0)
    log.info(
        "Using residual_std=%.2f°F for bucket probabilities (test-set based, floored at 2.0)",
        residual_std,
    )

    baselines = evaluate_baselines(test_df)
    station_met = per_station_metrics(model, test_df)

    # Step 5: Trading backtest
    log.info("Running trading backtest...")
    trades_df = run_backtest(model, test_df, residual_std, db)

    # Step 6: Print everything
    print_results(
        feature_df,
        train_df,
        test_df,
        model,
        residual_std,
        metrics,
        baselines,
        station_met,
        trades_df,
    )

    # Save model
    MODEL_OUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_OUT_DIR / "daily_max_lgbm.txt"
    model.save_model(str(model_path))
    log.info("Model saved to %s", model_path)

    # Also save residual_std for inference
    meta_path = MODEL_OUT_DIR / "daily_max_meta.txt"
    meta_path.write_text(
        f"residual_std={residual_std:.4f}\n"
        f"train_residual_std={train_residual_std:.4f}\n"
        f"train_cutoff={TRAIN_CUTOFF}\n"
    )
    log.info("Metadata saved to %s", meta_path)

    db.close()


if __name__ == "__main__":
    main()

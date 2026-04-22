"""Build unified (station, local_date) feature matrix for Dec 1 - Apr 11.

Target: `actual_max_f` — the actual daily max temperature at station in
local calendar day (from METAR).

Features: tier-by-tier ensemble of forecasts available "as of morning
of the market day" (i.e., forecasts issued before 14 UTC of the
target day — 10 EDT — giving ~6h before afternoon peak).

- NBS forecast max (latest runtime ≤ 14 UTC that day, txn_f)
- NBS forecast spread (uncertainty)
- GFS MOS forecast max
- HRRR forecast max (over afternoon hours)
- METAR-based: yesterday's actual max, temp at 12 UTC today,
  day-of-year seasonal anchor, error of NBS from yesterday
- HRRR vs NBS disagreement
- GFS vs NBS disagreement

Output: data/processed/backtest_v3/features.parquet
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
OUT_DIR = REPO / "data" / "processed" / "backtest_v3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Station → IANA tz
TZ = {
    "LGA": "America/New_York", "NYC": "America/New_York",
    "ATL": "America/New_York", "MIA": "America/New_York",
    "ORD": "America/Chicago", "DAL": "America/Chicago",
    "HOU": "America/Chicago", "AUS": "America/Chicago",
    "DEN": "America/Denver",
    "SEA": "America/Los_Angeles", "LAX": "America/Los_Angeles",
    "SFO": "America/Los_Angeles",
}
STATIONS = list(TZ.keys())

# IS / OOS boundaries (LOCKED before peeking at model performance)
IS_END = date(2026, 2, 28)
START_DATE = date(2025, 12, 1)

# END_DATE grows with the clock so live-trading features cover today +
# forecast-horizon days. NBS and GFS publish ~72h of ahead-forecast; the
# strategy's discover step queries a few days forward for rollover.
# Override with the BUILD_FEATURES_END_DATE env var for reproducible
# historical rebuilds.
import os as _os
_env_end = _os.environ.get("BUILD_FEATURES_END_DATE")
if _env_end:
    END_DATE = date.fromisoformat(_env_end)
else:
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    END_DATE = _dt.now(_UTC).date() + _td(days=3)


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def metar_daily_max() -> pd.DataFrame:
    """Daily max per (station, local_date) from METAR."""
    con = _con()
    rows = []
    for st in STATIONS:
        tz = TZ[st]
        q = f"""
            WITH obs AS (
              SELECT valid, tmpf
              FROM read_parquet('{REPO}/data/processed/iem_metar/{st}/*.parquet')
              WHERE tmpf IS NOT NULL
            )
            SELECT DATE(valid AT TIME ZONE '{tz}') AS local_date,
                   MAX(tmpf) AS actual_max_f,
                   COUNT(*) AS n_obs,
                   AVG(CASE WHEN EXTRACT(HOUR FROM valid AT TIME ZONE '{tz}') BETWEEN 11 AND 13 THEN tmpf END) AS tmp_noon_f,
                   AVG(CASE WHEN EXTRACT(HOUR FROM valid AT TIME ZONE '{tz}') BETWEEN 6 AND 8 THEN tmpf END) AS tmp_morning_f,
                   AVG(CASE WHEN EXTRACT(HOUR FROM valid AT TIME ZONE '{tz}') BETWEEN 3 AND 5 THEN tmpf END) AS tmp_dawn_f
            FROM obs
            GROUP BY local_date
        """
        try:
            df = con.execute(q).fetch_df()
            df["station"] = st
            rows.append(df)
        except Exception as e:
            print(f"  METAR {st}: {e}")
    out = pd.concat(rows, ignore_index=True)
    out["local_date"] = pd.to_datetime(out["local_date"])
    return out


def load_mos_forecasts(model: str) -> pd.DataFrame:
    """Load NBS or GFS MOS. Returns DataFrame with runtime, station, ftime,
    tmp_f, n_x_f (daily max/min), and (for NBS) txn_spread_f.
    """
    con = _con()
    path_glob = f"{REPO}/data/processed/iem_mos/{model}/*.parquet"
    if model == "NBS":
        q = f"""
            SELECT runtime, station, ftime, tmp_f, txn_f, txn_spread_f, lead_hours
            FROM read_parquet('{path_glob}')
        """
        df = con.execute(q).fetch_df()
        df = df.rename(columns={"txn_f": "n_x_f"})
        # spread only non-null on txn rows
    else:
        q = f"""
            SELECT runtime, station, ftime, tmp_f, n_x_f, lead_hours
            FROM read_parquet('{path_glob}')
        """
        df = con.execute(q).fetch_df()
        df["txn_spread_f"] = None
    df["runtime"] = pd.to_datetime(df["runtime"], utc=True)
    df["ftime"] = pd.to_datetime(df["ftime"], utc=True)
    return df


def extract_mos_pred_max(df: pd.DataFrame, station: str, target_local_date: date,
                         tz: str, cutoff_utc: datetime) -> tuple[float | None, float | None]:
    """For `target_local_date` at `station`, find the most recent MOS run
    issued before `cutoff_utc` whose `n_x_f` is the afternoon max forecast
    for that day. Return (pred_max, spread).
    """
    # Target afternoon window in UTC: convert (local_date, 12-22 local) to UTC
    # Approximation via pytz offsets
    local_afternoon_start = datetime.combine(target_local_date, datetime.min.time())
    # pandas Timestamp with tz then convert — easier
    start = pd.Timestamp(local_afternoon_start).tz_localize(tz).tz_convert("UTC")
    start_afternoon = start + pd.Timedelta(hours=10)  # 10 local ≈ morning ramp
    end_afternoon = start + pd.Timedelta(hours=22)    # 22 local ≈ end of day

    sub = df[
        (df.station == station)
        & (df.runtime <= pd.Timestamp(cutoff_utc))
        & (df.runtime >= pd.Timestamp(cutoff_utc) - pd.Timedelta(days=2))
        & (df.ftime >= start_afternoon)
        & (df.ftime <= end_afternoon)
        & (df.n_x_f.notna())
    ]
    if sub.empty:
        # Fallback: use tmp_f at ftime closest to afternoon peak (20 UTC EDT)
        fallback_peak = pd.Timestamp(datetime.combine(target_local_date, datetime.min.time())).tz_localize(tz).tz_convert("UTC") + pd.Timedelta(hours=15)
        sub2 = df[
            (df.station == station)
            & (df.runtime <= pd.Timestamp(cutoff_utc))
            & (df.runtime >= pd.Timestamp(cutoff_utc) - pd.Timedelta(days=2))
            & (df.ftime >= start_afternoon)
            & (df.ftime <= end_afternoon)
            & (df.tmp_f.notna())
        ]
        if sub2.empty:
            return (None, None)
        idx_max = sub2.loc[sub2.runtime == sub2.runtime.max(), "tmp_f"].idxmax()
        return (float(sub2.loc[idx_max, "tmp_f"]), None)

    latest_rt = sub.runtime.max()
    cand = sub[sub.runtime == latest_rt]
    idx = cand.n_x_f.idxmax()
    pred = float(cand.loc[idx, "n_x_f"])
    sp = cand.loc[idx, "txn_spread_f"]
    sp = float(sp) if pd.notna(sp) else None
    return (pred, sp)


def load_hrrr_pred_max() -> pd.DataFrame:
    """Time-resolved HRRR peak-window max per (station, local_date).

    Uses the canonical compute from `lib.weather.hrrr.hrrr_peak_max_f`
    with cutoff = now (so "freshest available as of this rebuild"):

    - Only init_times <= datetime.now(UTC)
    - valid_times must fall in the station's local 12:00-22:00 window
    - Require >= 6 distinct peak-hours covered, else None
    - Most recent init_time per valid_time

    Returns a DataFrame with (station, local_date, hrrr_max_t_f).
    `hrrr_max_t_f` is None on any (station, local_date) where the
    coverage requirement isn't met — typical for today's date before
    the afternoon HRRR cycles have published, and always for future
    dates (HRRR is fxx=6, only 6h ahead).
    """
    from datetime import UTC, datetime

    from lib.weather.hrrr import hrrr_peak_max_f_batch

    cutoff = datetime.now(UTC)
    dates = [d for d in pd.date_range(START_DATE, END_DATE, freq="D").date]
    rows = []
    for st in STATIONS:
        # One file read per station, iterate dates in-Python.
        results = hrrr_peak_max_f_batch(st, dates, cutoff_utc=cutoff)
        for d, val in results.items():
            rows.append({"station": st, "local_date": pd.Timestamp(d),
                         "hrrr_max_t_f": val})
    return pd.DataFrame(rows)


def main():
    print("=" * 60)
    print("BUILDING UNIFIED FEATURE MATRIX (v3)")
    print("=" * 60)

    print("\n[1/4] METAR daily features...")
    metar = metar_daily_max()
    print(f"  rows: {len(metar)}, stations: {metar.station.nunique()}")

    print("\n[2/4] Loading MOS forecasts...")
    nbs = load_mos_forecasts("NBS")
    print(f"  NBS rows: {len(nbs)}")
    gfs = load_mos_forecasts("GFS")
    print(f"  GFS rows: {len(gfs)}")

    # Build rows per (station, local_date)
    print("\n[3/4] Joining forecasts to daily rows...")
    # Generate all (station, local_date) pairs in window
    dates = pd.date_range(START_DATE, END_DATE, freq="D")
    grid = pd.DataFrame(
        [(st, d) for st in STATIONS for d in dates],
        columns=["station", "local_date"]
    )
    grid["local_date"] = pd.to_datetime(grid["local_date"])

    # Left-join METAR
    grid = grid.merge(metar, on=["station", "local_date"], how="left")

    # For each row, compute MOS predictions
    nbs_preds = []
    gfs_preds = []
    for _, row in grid.iterrows():
        st = row["station"]
        d = row["local_date"].date()
        tz = TZ[st]
        # Cutoff: entry-time (14 UTC ≈ 10 local morning) — i.e., use forecasts
        # available 6+h before expected afternoon peak
        cutoff = pd.Timestamp(datetime.combine(d, datetime.min.time())).tz_localize(tz).tz_convert("UTC") + pd.Timedelta(hours=10)
        nbs_key = "K" + st
        nbs_pred, nbs_spread = extract_mos_pred_max(nbs, nbs_key, d, tz, cutoff)
        gfs_pred, _ = extract_mos_pred_max(gfs, nbs_key, d, tz, cutoff)
        nbs_preds.append((nbs_pred, nbs_spread))
        gfs_preds.append((gfs_pred, None))
    grid["nbs_pred_max_f"] = [p[0] for p in nbs_preds]
    grid["nbs_spread_f"] = [p[1] for p in nbs_preds]
    grid["gfs_pred_max_f"] = [p[0] for p in gfs_preds]

    print(f"  NBS filled: {grid.nbs_pred_max_f.notna().sum()}/{len(grid)}")
    print(f"  GFS filled: {grid.gfs_pred_max_f.notna().sum()}/{len(grid)}")

    print("\n[4/4] HRRR forecasts (canonical time-resolved peak max)...")
    hrrr_df = load_hrrr_pred_max()
    if len(hrrr_df) > 0:
        print(f"  HRRR daily rows: {len(hrrr_df)}")
        grid = grid.merge(hrrr_df, on=["station", "local_date"], how="left")
        print(f"  HRRR filled: {grid.hrrr_max_t_f.notna().sum()}/{len(grid)}")
    else:
        grid["hrrr_max_t_f"] = None

    # Add lag features: yesterday's max, NBS error yesterday
    grid = grid.sort_values(["station", "local_date"]).reset_index(drop=True)
    grid["yesterday_max_f"] = grid.groupby("station")["actual_max_f"].shift(1)
    grid["yesterday_nbs_err_f"] = grid["yesterday_max_f"] - grid.groupby("station")["nbs_pred_max_f"].shift(1)
    grid["day_of_year"] = grid["local_date"].dt.dayofyear
    grid["month"] = grid["local_date"].dt.month

    # Fold assignment
    grid["fold"] = grid["local_date"].dt.date.apply(
        lambda d: "IS" if d <= IS_END else "OOS"
    )

    # Disagreement signals
    grid["nbs_minus_gfs"] = grid["nbs_pred_max_f"] - grid["gfs_pred_max_f"]
    grid["hrrr_minus_nbs"] = grid["hrrr_max_t_f"] - grid["nbs_pred_max_f"]

    # Save
    out = OUT_DIR / "features.parquet"
    grid.to_parquet(out, index=False)
    print(f"\nWrote {out}")
    print(f"\nFold counts:\n{grid.fold.value_counts()}")
    print(f"\nComplete rows (all features non-null + actual_max_f known):")
    complete = grid.dropna(subset=["actual_max_f", "nbs_pred_max_f", "gfs_pred_max_f"])
    print(f"  total: {len(complete)}")
    print(f"  IS: {(complete.fold=='IS').sum()}")
    print(f"  OOS: {(complete.fold=='OOS').sum()}")
    print(f"  per station IS: {complete[complete.fold=='IS'].station.value_counts().to_dict()}")


if __name__ == "__main__":
    main()

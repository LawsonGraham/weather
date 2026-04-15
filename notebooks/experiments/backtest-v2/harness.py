"""Unified backtest harness for v2 IS/OOS work.

Single-purpose module that builds the "master trade table" joining:
- Polymarket daily-temp markets (slug, bucket thresholds)
- Hourly midpoint prices at entry hour
- NBS forecast (predicted max + uncertainty) at entry hour
- Ground truth daily max from METAR
- Winning bucket

And provides a `run_strategy(strategy_fn, fold)` API that applies a
strategy to the IS or OOS fold.

No strategy logic lives here — strategies are separate functions
passed in. This is pure plumbing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")

# --- Fold boundaries (PRE-REGISTERED in PRE_REGISTRATION.md §1) ---- #

IS_START = date(2026, 3, 11)
IS_END = date(2026, 3, 31)
OOS_START = date(2026, 4, 1)
OOS_END = date(2026, 4, 10)

ENTRY_HOUR_UTC = 20  # 16 EDT, pre-registered

# --- city → station mapping ----------------------------------------- #

CITY_TO_STATION = {
    "New York City": "LGA",  # polymarket resolves NYC to KLGA per vault
    "Atlanta": "ATL",
    "Dallas": "DAL",
    "Seattle": "SEA",
    "Chicago": "ORD",
    "Miami": "MIA",
    "Austin": "AUS",
    "Houston": "HOU",
    "Denver": "DEN",
    "Los Angeles": "LAX",
    "San Francisco": "SFO",
}

# --- Bucket parsing ------------------------------------------------- #

_RE_RANGE = re.compile(r"^(\d+)-(\d+)°F$")
_RE_BELOW = re.compile(r"^(\d+)°F or below$")
_RE_ABOVE = re.compile(r"^(\d+)°F or higher$")


def parse_bucket(title: str) -> tuple[float, float, float]:
    """Return (low, high, center) for a bucket title.

    Tails use a virtual 2°F span so centers are consistent:
    - "69°F or below" → low=-inf (effective 68), high=69, center=68
    - "88°F or higher" → low=88, high=+inf (effective 89), center=89
    """
    m = _RE_RANGE.match(title)
    if m:
        lo, hi = int(m[1]), int(m[2])
        return (float(lo), float(hi), (lo + hi) / 2.0)
    m = _RE_BELOW.match(title)
    if m:
        hi = int(m[1])
        return (float("-inf"), float(hi), float(hi - 1))
    m = _RE_ABOVE.match(title)
    if m:
        lo = int(m[1])
        return (float(lo), float("inf"), float(lo + 1))
    raise ValueError(f"can't parse bucket title: {title}")


def extract_market_date(slug: str) -> date | None:
    """Extract market resolution date from slug like ...-on-april-1-2026-..."""
    m = re.search(r"-on-([a-z]+)-(\d+)-(\d+)(?:-|$)", slug)
    if not m:
        return None
    month_str, day_str, year_str = m[1], int(m[2]), int(m[3])
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    if month_str not in month_map or year_str < 2000:
        return None
    try:
        return date(year_str, month_map[month_str], day_str)
    except ValueError:
        return None


# --- Connection ----------------------------------------------------- #

def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


# --- Loaders -------------------------------------------------------- #


def load_markets() -> pd.DataFrame:
    """Load resolved daily-temp markets in Mar 11 → Apr 10 window.

    One row per (slug). Adds columns: bucket_low, bucket_high,
    bucket_center, market_date, won_yes (0/1 from outcome_prices).
    """
    con = _con()
    df = con.execute(f"""
        SELECT slug, condition_id, yes_token_id, no_token_id,
               city, question, group_item_title, group_item_threshold,
               closed, end_date, closed_time, outcome_prices,
               liquidity_num, volume_num
        FROM '{REPO}/data/processed/polymarket_weather/markets.parquet'
        WHERE weather_tags ILIKE '%Daily Temperature%'
          AND closed = true
    """).fetch_df()

    df["market_date"] = df["slug"].apply(extract_market_date)
    df = df.dropna(subset=["market_date"]).copy()
    df["market_date"] = pd.to_datetime(df["market_date"])

    # Filter to our window
    df = df[
        (df["market_date"] >= pd.Timestamp(IS_START))
        & (df["market_date"] <= pd.Timestamp(OOS_END))
    ].copy()

    # Parse buckets
    parsed = df["group_item_title"].apply(parse_bucket)
    df["bucket_low"] = parsed.apply(lambda t: t[0])
    df["bucket_high"] = parsed.apply(lambda t: t[1])
    df["bucket_center"] = parsed.apply(lambda t: t[2])
    df["bucket_idx"] = df["group_item_threshold"].astype(int)

    # Outcome: outcomes=['Yes','No'] so outcome_prices[0] is YES resolution price.
    # After resolution: [1.0, 0.0] = YES won, [0.0, 1.0] = NO won.
    df["won_yes"] = df["outcome_prices"].apply(
        lambda lst: int(lst[0] == 1.0) if lst is not None and len(lst) == 2 else -1
    )

    # Fold assignment
    def _fold(d):
        if IS_START <= d.date() <= IS_END:
            return "IS"
        if OOS_START <= d.date() <= OOS_END:
            return "OOS"
        return "OOB"

    df["fold"] = df["market_date"].apply(_fold)
    df = df[df["fold"].isin(["IS", "OOS"])].copy()

    return df.reset_index(drop=True)


def load_hourly_prices() -> pd.DataFrame:
    """Load hourly midpoint prices at entry_hour for all tokens in window."""
    con = _con()
    hour_start_is = datetime.combine(IS_START, time(ENTRY_HOUR_UTC, 0), UTC) - timedelta(days=1)
    hour_end_oos = datetime.combine(OOS_END, time(ENTRY_HOUR_UTC, 59), UTC) + timedelta(days=1)

    q = f"""
        SELECT yes_token_id, timestamp, p_yes
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/**/*.parquet')
        WHERE timestamp >= TIMESTAMP '{hour_start_is}'
          AND timestamp <= TIMESTAMP '{hour_end_oos}'
    """
    df = con.execute(q).fetch_df()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date
    df["hour_utc"] = df["timestamp"].dt.hour
    return df


def load_metar_daily_max() -> pd.DataFrame:
    """Compute daily max temperature from METAR per (station, date_local).

    Uses local calendar day (based on station's timezone — approximated
    by station-specific UTC offset map below). Polymarket daily-temp
    markets resolve on local calendar day.
    """
    # Station → IANA timezone (handles DST correctly)
    TZ = {
        "LGA": "America/New_York", "NYC": "America/New_York",
        "ATL": "America/New_York", "MIA": "America/New_York",
        "ORD": "America/Chicago", "DAL": "America/Chicago",
        "HOU": "America/Chicago", "AUS": "America/Chicago",
        "DEN": "America/Denver",
        "SEA": "America/Los_Angeles", "LAX": "America/Los_Angeles",
        "SFO": "America/Los_Angeles",
    }
    con = _con()
    rows = []
    for station, tz in TZ.items():
        q = f"""
            WITH obs AS (
                SELECT valid, tmpf
                FROM read_parquet('{REPO}/data/processed/iem_metar/{station}/*.parquet')
                WHERE tmpf IS NOT NULL
            )
            SELECT
                DATE(valid AT TIME ZONE '{tz}') AS local_date,
                MAX(tmpf) AS daily_max_f
            FROM obs
            GROUP BY local_date
        """
        try:
            df = con.execute(q).fetch_df()
            df["station"] = station
            rows.append(df)
        except Exception as e:
            print(f"  METAR {station}: {e}")
    out = pd.concat(rows, ignore_index=True)
    out["local_date"] = pd.to_datetime(out["local_date"])
    return out


def load_nbs_forecast() -> pd.DataFrame:
    """For each (station, market_date), find the most recent NBS forecast
    available at entry time that predicts that day's max temp.

    NBS output columns (from iem_mos transform):
    - runtime (TIMESTAMP) — when the forecast was issued
    - station (4-char)
    - ftime (TIMESTAMP) — the time the forecast is valid for
    - tmp_f, n_x_f, txn_f, txn_spread_f — temperature fields
    - lead_hours — hours from runtime to ftime
    """
    con = _con()
    df = con.execute(f"""
        SELECT runtime, station, ftime,
               tmp_f, txn_f, txn_spread_f, lead_hours
        FROM read_parquet('{REPO}/data/processed/iem_mos/NBS/*.parquet')
    """).fetch_df()
    df["runtime"] = pd.to_datetime(df["runtime"], utc=True)
    df["ftime"] = pd.to_datetime(df["ftime"], utc=True)
    return df


# --- Master table --------------------------------------------------- #


@dataclass
class TradeRow:
    """One row = one (slug, market_date) candidate trade."""
    slug: str
    city: str
    market_date: date
    fold: str
    bucket_idx: int
    bucket_low: float
    bucket_high: float
    bucket_center: float
    entry_price: float  # midpoint at entry hour
    entry_ts: datetime
    won_yes: int  # 1 if this bucket won, 0 if lost, -1 if unknown
    actual_max_f: float | None
    nbs_pred_max_f: float | None  # NBS predicted max for this market date
    nbs_spread_f: float | None  # NBS uncertainty
    # derived: market-implied prob of this bucket = entry_price
    # fee = 1 share × 0.05 × p × (1-p)


def build_trade_table() -> pd.DataFrame:
    """Join markets × prices × NBS × METAR into one master dataframe.

    One row per (slug). Used as the canonical IS+OOS dataset.
    """
    print("  loading markets...")
    mkt = load_markets()
    print(f"    {len(mkt)} resolved market-slugs in window ({mkt['fold'].value_counts().to_dict()})")

    print("  loading hourly prices...")
    px = load_hourly_prices()
    print(f"    {len(px)} price rows, {px['yes_token_id'].nunique()} tokens")

    print("  loading METAR daily max...")
    metar = load_metar_daily_max()
    print(f"    {len(metar)} (station, local_date) groups")

    print("  loading NBS forecasts...")
    nbs = load_nbs_forecast()
    print(f"    {len(nbs)} NBS rows")

    # Entry timestamp per (market_date) — 20:00 UTC on the market_date itself
    # (since market resolution date = local calendar day, and 20:00 UTC ≈ 16 EDT ≈ 13 PDT,
    # entry is SAME DAY as resolution for east-coast cities, may be previous day's evening
    # for west-coast in calendar-UTC terms.)
    mkt["entry_ts"] = mkt["market_date"].apply(
        lambda d: d.replace(hour=ENTRY_HOUR_UTC, minute=0, tzinfo=UTC)
    )

    # Match price at entry hour — use LATEST price at or before 20:59 UTC on
    # market_date (forward-fill for tail buckets that stopped being priced).
    px_window = px[px["hour_utc"] <= ENTRY_HOUR_UTC].copy()
    px_window["date_ts"] = pd.to_datetime(px_window["date"])
    # Take the latest row per (token, date)
    px_window = px_window.sort_values("timestamp").groupby(
        ["yes_token_id", "date_ts"], as_index=False
    ).tail(1)[["yes_token_id", "date_ts", "p_yes"]]
    mkt = mkt.merge(
        px_window,
        left_on=["yes_token_id", "market_date"],
        right_on=["yes_token_id", "date_ts"],
        how="left",
    )
    mkt = mkt.rename(columns={"p_yes": "entry_price"})
    mkt = mkt.drop(columns=["date_ts"])

    # Match METAR daily max
    mkt["station"] = mkt["city"].map(CITY_TO_STATION)
    metar = metar.rename(columns={"local_date": "market_date", "daily_max_f": "actual_max_f"})
    mkt = mkt.merge(
        metar[["station", "market_date", "actual_max_f"]],
        on=["station", "market_date"],
        how="left",
    )

    # Match NBS forecast: for each market_date, find most recent runtime <= 19:00 UTC
    # on market_date, targeting max temp for market_date LOCAL day.
    # Use n_x_f (max) forecast for the ftime that falls in market_date local max window
    # (typically 18-22 UTC).
    # Simple approach: take the NBS forecast whose runtime is latest-before-entry and
    # whose ftime is within 24 hours of entry, and pick the row with the MAX n_x_f in
    # that window (since n_x_f is accumulated max forecast for the daily cycle).
    def _nbs_for(row):
        st = "K" + row["station"]  # NBS uses K-prefix
        entry = row["entry_ts"]
        # Target: the "today afternoon max" txn forecast.
        # Window: any NBS run issued within 24h before entry AND with a txn_f
        # whose ftime is within 12h after entry (= today afternoon-into-next-morning).
        mask_txn = (
            (nbs["station"] == st)
            & (nbs["runtime"] <= entry)
            & (nbs["runtime"] >= entry - timedelta(hours=24))
            & (nbs["ftime"] >= entry)
            & (nbs["ftime"] <= entry + timedelta(hours=12))
            & (nbs["txn_f"].notna())
        )
        sub_txn = nbs[mask_txn]
        if not sub_txn.empty:
            # Most recent runtime that still has a forward txn_f
            latest_rt = sub_txn["runtime"].max()
            cand = sub_txn[sub_txn["runtime"] == latest_rt]
            # Pick the max txn (afternoon max, not overnight min)
            idx = cand["txn_f"].idxmax()
            return (float(cand.loc[idx, "txn_f"]),
                    float(cand.loc[idx, "txn_spread_f"])
                    if pd.notna(cand.loc[idx, "txn_spread_f"]) else None)
        # Fallback: use tmp_f from latest runtime at ftime closest to entry+4h
        mask = (
            (nbs["station"] == st)
            & (nbs["runtime"] <= entry)
            & (nbs["runtime"] >= entry - timedelta(hours=24))
            & (nbs["ftime"] >= entry - timedelta(hours=2))
            & (nbs["ftime"] <= entry + timedelta(hours=12))
        )
        sub = nbs[mask]
        if sub.empty:
            return (None, None)
        latest_rt = sub["runtime"].max()
        sub = sub[sub["runtime"] == latest_rt]
        if sub["tmp_f"].notna().any():
            return (float(sub["tmp_f"].max()), None)
        return (None, None)

    # Slow loop — optimize later. ~2500 markets.
    nbs_results = mkt.apply(_nbs_for, axis=1)
    mkt["nbs_pred_max_f"] = nbs_results.apply(lambda t: t[0])
    mkt["nbs_spread_f"] = nbs_results.apply(lambda t: t[1])

    return mkt


# --- PnL calculation ------------------------------------------------ #

FEE_RATE = 0.05  # Polymarket weather fee


def fee_per_share(p: float) -> float:
    """Fee in USDC per share at price p."""
    return FEE_RATE * p * (1.0 - p)


def trade_pnl(entry_price: float, won: int, shares: int = 1) -> float:
    """Net PnL for a single-slug buy trade.

    - entry_price: cost per share
    - won: 1 if bucket won (pays $1), else 0
    - shares: share count (default 1)
    - fees: computed via fee_per_share(entry_price) per share
    """
    payout = float(won) * shares
    cost = entry_price * shares
    fee = fee_per_share(entry_price) * shares
    return payout - cost - fee


# --- Strategy runner ------------------------------------------------ #


def run_strategy(
    table: pd.DataFrame,
    selector_fn,
    fold: str,
    strategy_name: str,
) -> pd.DataFrame:
    """Apply a strategy to a fold.

    `selector_fn(day_df)` receives a DataFrame of ALL buckets for one
    (city, market_date) and returns a list of bucket_idx values to
    buy (one share each).

    Returns a DataFrame of trades with columns:
    - strategy, city, market_date, bucket_idx, entry_price, won_yes, pnl
    """
    assert fold in ("IS", "OOS"), f"bad fold: {fold}"
    sub = table[table["fold"] == fold].copy()
    trades = []

    for (city, md), group in sub.groupby(["city", "market_date"]):
        # Require: entry prices on all buckets, NBS forecast, outcome resolved.
        # (actual_max_f is a diagnostic — resolution comes from won_yes.)
        day = group.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any():
            continue
        if day["nbs_pred_max_f"].isna().any():
            continue
        if (day["won_yes"] < 0).any():
            continue
        # Sanity: should be exactly one winner across the 11 buckets
        if day["won_yes"].sum() != 1:
            continue

        selected = selector_fn(day)
        for b_idx in selected:
            row = day[day["bucket_idx"] == b_idx]
            if row.empty:
                continue
            r = row.iloc[0]
            pnl = trade_pnl(r["entry_price"], int(r["won_yes"]))
            trades.append({
                "strategy": strategy_name,
                "fold": fold,
                "city": city,
                "market_date": md,
                "bucket_idx": int(b_idx),
                "bucket_title": r["group_item_title"],
                "entry_price": float(r["entry_price"]),
                "nbs_pred_max_f": float(r["nbs_pred_max_f"]),
                "nbs_spread_f": r["nbs_spread_f"],
                "actual_max_f": float(r["actual_max_f"]),
                "won_yes": int(r["won_yes"]),
                "fee": fee_per_share(r["entry_price"]),
                "pnl": pnl,
            })

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame) -> dict:
    """Standard summary stats."""
    if len(trades) == 0:
        return {"n": 0, "hit_rate": 0, "total_pnl": 0, "per_trade": 0, "mean_entry": 0}
    return {
        "n": len(trades),
        "hit_rate": trades["won_yes"].mean(),
        "total_pnl": trades["pnl"].sum(),
        "per_trade": trades["pnl"].mean(),
        "mean_entry": trades["entry_price"].mean(),
        "std_pnl": trades["pnl"].std(),
    }


if __name__ == "__main__":
    print("=" * 60)
    print("BUILDING MASTER TRADE TABLE")
    print("=" * 60)
    tbl = build_trade_table()
    print()
    print(f"Total rows: {len(tbl)}")
    print(f"By fold: {tbl['fold'].value_counts().to_dict()}")
    print()
    print("Rows with full data (price + NBS + outcome):")
    complete = tbl.dropna(subset=["entry_price", "nbs_pred_max_f", "actual_max_f"])
    complete = complete[complete["won_yes"] >= 0]
    print(f"  IS: {(complete['fold']=='IS').sum()}")
    print(f"  OOS: {(complete['fold']=='OOS').sum()}")
    print()
    out = REPO / "data" / "processed" / "backtest_v2" / "trade_table.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    tbl.to_parquet(out, index=False)
    print(f"Wrote {out}")

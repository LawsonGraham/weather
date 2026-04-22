"""Consensus-Fade +1 backtest, but entry = first hour all 3 sources agree.

Instead of fixing entry at 20 UTC (current STRATEGY.md baseline), walk
hour-by-hour and enter at the earliest hour where:

    - NBS latest run has a max forecast for target_date's peak window
    - GFS latest run has a max forecast for target_date's peak window
    - HRRR (fxx=6) has ≥1 init_time ≤ H with valid_time in peak window
    - max(NBS, GFS, HRRR) - min(...) ≤ consensus_max (default 3.0°F)

Entry price = Polymarket hourly YES price at that hour, +1 bucket NO side.
Exit = hold to resolution (existing `won_yes` column).

Scope: same universe and dates as src/consensus_fade_plus1/backtest.py
(Mar 11 - Apr 10 2026, 11 US cities, consensus ≤ 3°F, +1 offset, NO side).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")

TZ = {
    "LGA": "America/New_York", "ATL": "America/New_York", "MIA": "America/New_York",
    "ORD": "America/Chicago", "DAL": "America/Chicago", "HOU": "America/Chicago",
    "AUS": "America/Chicago", "DEN": "America/Denver",
    "SEA": "America/Los_Angeles", "LAX": "America/Los_Angeles", "SFO": "America/Los_Angeles",
}
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL",
    "Seattle": "SEA", "Chicago": "ORD", "Miami": "MIA",
    "Austin": "AUS", "Houston": "HOU", "Denver": "DEN",
    "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE_RATE = 0.05
CONSENSUS_MAX_F = 3.0
OFFSET = 1
YES_PRICE_MIN = 0.005
YES_PRICE_MAX = 0.5

IS_START = date(2026, 3, 11)
IS_END = date(2026, 3, 25)
OOS_START = date(2026, 3, 26)
OOS_END = date(2026, 4, 10)


def peak_window_utc(target: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Local 12:00-22:00 of target_date → UTC."""
    start_local = pd.Timestamp(target).tz_localize(tz)
    return (
        (start_local + pd.Timedelta(hours=12)).tz_convert("UTC"),
        (start_local + pd.Timedelta(hours=22)).tz_convert("UTC"),
    )


def entry_window_utc(target: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Hourly scan window: local 05:00 target_date → 00:00 next day (UTC)."""
    start_local = pd.Timestamp(target).tz_localize(tz)
    return (
        (start_local + pd.Timedelta(hours=5)).tz_convert("UTC"),
        (start_local + pd.Timedelta(hours=24)).tz_convert("UTC"),
    )


def load_mos_long(model: str) -> pd.DataFrame:
    """All MOS issuances. Columns: station, runtime, ftime, n_x_f."""
    con = duckdb.connect()
    if model == "NBS":
        col = "txn_f"
    else:
        col = "n_x_f"
    df = con.execute(f"""
        SELECT station, runtime, ftime, {col} AS n_x_f
        FROM read_parquet('{REPO}/data/processed/iem_mos/{model}/*.parquet')
        WHERE {col} IS NOT NULL
    """).df()
    df["runtime"] = pd.to_datetime(df["runtime"], utc=True)
    df["ftime"] = pd.to_datetime(df["ftime"], utc=True)
    df["station"] = df["station"].str.removeprefix("K")
    return df


def load_hrrr_long() -> pd.DataFrame:
    """All HRRR forecasts. Columns: station, init_time, valid_time, t_f."""
    con = duckdb.connect()
    rows = []
    for st in TZ:
        try:
            sub = con.execute(f"""
                SELECT init_time, valid_time, t2m_heightAboveGround_2 AS t_k
                FROM read_parquet('{REPO}/data/raw/hrrr/K{st}/hourly.parquet')
                WHERE t2m_heightAboveGround_2 IS NOT NULL
            """).df()
        except Exception:
            continue
        sub["station"] = st
        sub["init_time"] = pd.to_datetime(sub["init_time"], utc=True)
        sub["valid_time"] = pd.to_datetime(sub["valid_time"], utc=True)
        sub["t_f"] = (sub["t_k"] - 273.15) * 9 / 5 + 32
        rows.append(sub[["station", "init_time", "valid_time", "t_f"]])
    return pd.concat(rows, ignore_index=True)


def load_polymarket_hourly() -> pd.DataFrame:
    """All hourly Polymarket prices. Columns: slug, timestamp (UTC), p_yes."""
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT slug, timestamp, p_yes
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/year=2026/month=*/data_0.parquet')
    """).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def nbs_gfs_max_as_of(sub: pd.DataFrame, as_of: pd.Timestamp,
                      peak_start: pd.Timestamp, peak_end: pd.Timestamp,
                      max_stale_hours: int = 24) -> float | None:
    """Latest MOS max-forecast for peak window as of `as_of`.

    Requires runtime in [as_of - max_stale_hours, as_of] and ftime inside peak.
    """
    s = sub[(sub.runtime <= as_of)
            & (sub.runtime >= as_of - pd.Timedelta(hours=max_stale_hours))
            & (sub.ftime >= peak_start) & (sub.ftime <= peak_end)]
    if s.empty:
        return None
    latest = s.loc[s.runtime == s.runtime.max()]
    return float(latest.n_x_f.max())


def hrrr_max_as_of(sub: pd.DataFrame, as_of: pd.Timestamp,
                   peak_start: pd.Timestamp, peak_end: pd.Timestamp,
                   min_coverage_hours: int = 6) -> float | None:
    """HRRR max forecast for peak window as of `as_of`.

    Use all init_times ≤ as_of whose valid_time falls in peak window. For
    each valid_time take the most recent init, then max over valid_times.
    Requires ≥ `min_coverage_hours` distinct covered valid-hours in the
    peak window (rejects early-morning partial coverage that biases max low).
    """
    s = sub[(sub.init_time <= as_of)
            & (sub.valid_time >= peak_start) & (sub.valid_time <= peak_end)]
    if s.empty:
        return None
    latest = s.sort_values("init_time").groupby("valid_time").tail(1)
    if latest.valid_time.dt.hour.nunique() < min_coverage_hours:
        return None
    return float(latest.t_f.max())


def find_consensus_hour(station: str, target: date,
                        nbs: pd.DataFrame, gfs: pd.DataFrame, hrrr: pd.DataFrame,
                        consensus_max: float) -> tuple[pd.Timestamp, float, float, float] | None:
    """Return (entry_ts_utc, nbs, gfs, hrrr) at first consensus hour, or None."""
    tz = TZ[station]
    peak_start, peak_end = peak_window_utc(target, tz)
    scan_start, scan_end = entry_window_utc(target, tz)
    nbs_st = nbs[nbs.station == station]
    gfs_st = gfs[gfs.station == station]
    hrrr_st = hrrr[hrrr.station == station]
    t = scan_start.floor("h")
    while t <= scan_end:
        n = nbs_gfs_max_as_of(nbs_st, t, peak_start, peak_end)
        g = nbs_gfs_max_as_of(gfs_st, t, peak_start, peak_end)
        h = hrrr_max_as_of(hrrr_st, t, peak_start, peak_end)
        if n is not None and g is not None and h is not None:
            spread = max(n, g, h) - min(n, g, h)
            if spread <= consensus_max:
                return (t, n, g, h)
        t = t + pd.Timedelta(hours=1)
    return None


def pick_hourly_price(prices: pd.DataFrame, slug: str, at: pd.Timestamp,
                      max_wait_hours: int = 3) -> tuple[pd.Timestamp, float] | None:
    """First available hourly price for slug at/after `at`."""
    s = prices[(prices.slug == slug) & (prices.timestamp >= at)
               & (prices.timestamp <= at + pd.Timedelta(hours=max_wait_hours))]
    if s.empty:
        return None
    r = s.sort_values("timestamp").iloc[0]
    return (pd.Timestamp(r.timestamp), float(r.p_yes))


def main() -> None:
    print("Loading trade table...")
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl[(tbl.date >= IS_START) & (tbl.date <= OOS_END)].copy()
    tbl = tbl.dropna(subset=["nbs_pred_max_f"])
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)

    print("Loading NBS/GFS/HRRR raw forecasts...")
    nbs = load_mos_long("NBS")
    gfs = load_mos_long("GFS")
    hrrr = load_hrrr_long()
    print(f"  NBS rows={len(nbs):,}  GFS rows={len(gfs):,}  HRRR rows={len(hrrr):,}")

    print("Loading Polymarket hourly prices...")
    prices = load_polymarket_hourly()
    print(f"  prices rows={len(prices):,}  unique slugs={prices.slug.nunique()}")

    # Per (city, date): find +1 bucket slug
    trades_out = []
    skipped = {"no_consensus": 0, "no_plus1": 0, "price_oob": 0, "no_price": 0}
    groups = list(tbl.groupby(["city", "market_date"]))
    print(f"Processing {len(groups)} (city,date) groups...")
    for (city, md), grp in groups:
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        station = day.station.iloc[0]
        target = md.date()
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == fav_idx + OFFSET]
        if row.empty:
            skipped["no_plus1"] += 1
            continue
        r = row.iloc[0]

        found = find_consensus_hour(station, target, nbs, gfs, hrrr, CONSENSUS_MAX_F)
        if found is None:
            skipped["no_consensus"] += 1
            continue
        entry_ts, n, g, h = found

        px = pick_hourly_price(prices, r["slug"], entry_ts)
        if px is None:
            skipped["no_price"] += 1
            continue
        px_ts, yes_p = px
        if yes_p < YES_PRICE_MIN or yes_p > YES_PRICE_MAX:
            skipped["price_oob"] += 1
            continue

        price = 1 - yes_p
        won_no = 1 - int(r["won_yes"])
        fee = FEE_RATE * price * (1 - price)
        pnl = float(won_no) - price - fee
        cs = max(n, g, h) - min(n, g, h)
        trades_out.append({
            "city": city, "date": target, "station": station,
            "entry_ts_consensus": entry_ts, "entry_ts_fill": px_ts,
            "hours_after_midnight_local": (entry_ts - pd.Timestamp(target).tz_localize(TZ[station]).tz_convert("UTC")).total_seconds() / 3600,
            "nbs": n, "gfs": g, "hrrr": h, "consensus_spread": cs,
            "bucket_idx": int(r["bucket_idx"]),
            "bucket_title": r["group_item_title"],
            "yes_price": yes_p, "price_paid": price,
            "won_no": won_no, "fee": fee, "pnl": pnl,
        })

    out = pd.DataFrame(trades_out)
    print(f"\nSkipped reasons: {skipped}")
    print(f"Trades produced: {len(out)}")
    if out.empty:
        return

    def stats(df: pd.DataFrame, label: str) -> None:
        n = len(df)
        if n == 0:
            print(f"  {label:<30}  n=0")
            return
        hit = df.won_no.mean()
        per = df.pnl.mean()
        tot = df.pnl.sum()
        sd = df.pnl.std(ddof=1) if n > 1 else 0.0
        t = per / (sd / n ** 0.5) if sd > 0 else 0.0
        print(f"  {label:<30}  n={n:>3}  hit={hit*100:>5.1f}%  per=${per:>+.4f}  tot=${tot:>+.2f}  t={t:>+.2f}")

    print("\n=== Primary (consensus-form entry, ≤3°F, +1 NO) ===")
    stats(out, "FULL")
    stats(out[out.date <= IS_END], f"IS {IS_START}..{IS_END}")
    stats(out[out.date >= OOS_START], f"OOS {OOS_START}..{OOS_END}")

    print("\n=== Per-city ===")
    for c, g in out.sort_values("city").groupby("city"):
        stats(g, c)

    print("\n=== Entry timing (UTC hour of first consensus) ===")
    out["entry_utc_hour"] = out.entry_ts_consensus.dt.hour
    by_hour = out.groupby("entry_utc_hour").agg(n=("pnl", "count"),
                                                hit=("won_no", "mean"),
                                                per=("pnl", "mean")).reset_index()
    print(by_hour.to_string(index=False))

    print("\n=== Entry timing (local hour after midnight) ===")
    out["local_bucket"] = pd.cut(
        out.hours_after_midnight_local,
        bins=[0, 6, 9, 12, 15, 18, 21, 30],
        labels=["0-6", "6-9", "9-12", "12-15", "15-18", "18-21", "21+"],
    )
    by_lb = out.groupby("local_bucket", observed=True).agg(
        n=("pnl", "count"), hit=("won_no", "mean"), per=("pnl", "mean")
    ).reset_index()
    print(by_lb.to_string(index=False))

    print("\n=== Yes-price distribution at fill ===")
    print(out.yes_price.describe().to_string())

    print("\n=== Daily aggregate ===")
    daily = out.groupby("date").agg(n=("pnl", "count"), pnl=("pnl", "sum"),
                                    cap=("price_paid", "sum")).reset_index()
    n_days = len(daily)
    pos = (daily.pnl > 0).sum()
    sharpe = daily.pnl.mean() / daily.pnl.std() if daily.pnl.std() > 0 else 0
    print(f"  days={n_days}  positive={pos}/{n_days} ({pos/n_days*100:.0f}%)")
    print(f"  avg trades/day={daily.n.mean():.2f}  avg day pnl=${daily.pnl.mean():+.4f}")
    print(f"  daily Sharpe={sharpe:.3f}  annualized={sharpe*np.sqrt(252):.2f}")
    print(f"  total PnL=${daily.pnl.sum():+.2f}  gross cap=${daily.cap.sum():.2f}")

    out_path = REPO / "data/processed/backtest_v3/consensus_form_trades.parquet"
    out.to_parquet(out_path, index=False)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()

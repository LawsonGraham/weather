"""Consensus-Fade +1 Offset — Canonical backtest reproducer.

Runs the canonical rule from STRATEGY.md §3:
    - All three forecasts present (NBS + GFS MOS + HRRR)
    - HRRR fxx=6 covers ≥ 6 of the 11 hours in local 12-22 peak window
    - consensus_spread ≤ 3.0°F
    - Entry time ≥ 15:00 city-local
    - 0.07 ≤ yes_ask ≤ 0.50   (7c edge floor + market-wisdom cap)

Expected output: n=31, 29 wins, 2 losses, 93.5% hit, +$0.126 per trade,
t=+2.96 (IS +7.03, OOS +0.88). Two losses visible: Atlanta 2026-03-28
(yes=0.30) and Chicago 2026-03-17 (yes=0.34).

Also prints a reference row with tight cap (yes_ask ≤ 0.22) showing
the cosmetic-100% alternative (n=20, 0 losses, t=+16.24) and a wide
cap (yes_ask ≤ 0.75) showing what happens when we over-relax (n=37,
86.5% hit, t=+2.50 — hit-rate erosion).

Data requirements:
    data/processed/backtest_v2/trade_table.parquet
    data/processed/iem_mos/{NBS,GFS}/*.parquet
    data/raw/hrrr/K<station>/hourly.parquet
    data/processed/polymarket_prices_history/hourly/year=2026/month=*/data_0.parquet

Usage:
    uv run python src/consensus_fade_plus1/backtest.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]

TZ = {
    "LGA": "America/New_York", "ATL": "America/New_York", "MIA": "America/New_York",
    "ORD": "America/Chicago", "DAL": "America/Chicago", "HOU": "America/Chicago",
    "AUS": "America/Chicago", "DEN": "America/Denver",
    "SEA": "America/Los_Angeles", "LAX": "America/Los_Angeles", "SFO": "America/Los_Angeles",
}
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE_RATE = 0.05

# Canonical parameters (§3 of STRATEGY.md)
CONSENSUS_MAX_F = 3.0
OFFSET = 1
YES_PRICE_MIN = 0.07        # 7c edge floor (NO <= 0.93)
YES_PRICE_MAX = 0.50        # market-wisdom cap (market <= ~50% on +1)
LOCAL_FLOOR_HOUR = 15       # 15:00 city-local
HRRR_MIN_PEAK_COV = 6       # distinct valid-hours in 12-22 local

# IS / OOS fold boundaries (pre-registered)
IS_START = date(2026, 3, 11)
IS_END = date(2026, 3, 25)
OOS_START = date(2026, 3, 26)
OOS_END = date(2026, 4, 10)


@dataclass
class Stats:
    n: int
    wins: int
    losses: int
    hit: float
    per_trade: float
    total: float
    t: float


def summarize(df: pd.DataFrame) -> Stats:
    n = len(df)
    if n == 0:
        return Stats(0, 0, 0, 0.0, 0.0, 0.0, 0.0)
    wins = int(df.won_no.sum())
    losses = n - wins
    hit = float(df.won_no.mean())
    per = float(df.pnl.mean())
    total = float(df.pnl.sum())
    sd = float(df.pnl.std(ddof=1)) if n > 1 else 0.0
    t = per / (sd / n ** 0.5) if sd > 0 else 0.0
    return Stats(n, wins, losses, hit, per, total, t)


def _mos(model: str) -> pd.DataFrame:
    con = duckdb.connect()
    col = "txn_f" if model == "NBS" else "n_x_f"
    df = con.execute(f"""
        SELECT station, runtime, ftime, {col} AS n_x_f
        FROM read_parquet('{REPO}/data/processed/iem_mos/{model}/*.parquet')
        WHERE {col} IS NOT NULL
    """).df()
    df["runtime"] = pd.to_datetime(df["runtime"], utc=True)
    df["ftime"] = pd.to_datetime(df["ftime"], utc=True)
    df["station"] = df["station"].str.removeprefix("K")
    return df


def _hrrr() -> dict[str, pd.DataFrame]:
    """Load each station's raw HRRR into a per-station frame. The
    per-station DataFrame is what `lib.weather.hrrr.hrrr_peak_max_f_from_frame`
    wants (columns: init_time, valid_time, t_k)."""
    con = duckdb.connect()
    frames: dict[str, pd.DataFrame] = {}
    for st in TZ:
        try:
            sub = con.execute(f"""
                SELECT init_time, valid_time, t2m_heightAboveGround_2 AS t_k
                FROM read_parquet('{REPO}/data/raw/hrrr/K{st}/hourly.parquet')
                WHERE t2m_heightAboveGround_2 IS NOT NULL
            """).df()
        except Exception:
            continue
        if sub.empty:
            continue
        sub["init_time"] = pd.to_datetime(sub["init_time"], utc=True)
        sub["valid_time"] = pd.to_datetime(sub["valid_time"], utc=True)
        frames[st] = sub[["init_time", "valid_time", "t_k"]]
    return frames


def _prices() -> pd.DataFrame:
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT slug, timestamp, p_yes
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/year=2026/month=*/data_0.parquet')
    """).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _nbs_gfs_as_of(sub: pd.DataFrame, t: pd.Timestamp,
                   pk_start: pd.Timestamp, pk_end: pd.Timestamp) -> float | None:
    s = sub[(sub.runtime <= t) & (sub.runtime >= t - pd.Timedelta(hours=24))
            & (sub.ftime >= pk_start) & (sub.ftime <= pk_end)]
    if s.empty:
        return None
    return float(s[s.runtime == s.runtime.max()].n_x_f.max())


# `_hrrr_as_of` removed — HRRR compute now lives in
# `lib.weather.hrrr.hrrr_peak_max_f_from_frame`, the single canonical
# implementation shared by this backtest AND the features builder
# (which live reads via features.parquet). Any change to HRRR
# semantics happens in one place.


def _pick_price(prices: pd.DataFrame, slug: str, at: pd.Timestamp,
                max_wait_hours: int = 3) -> float | None:
    s = prices[(prices.slug == slug) & (prices.timestamp >= at)
               & (prices.timestamp <= at + pd.Timedelta(hours=max_wait_hours))]
    if s.empty:
        return None
    return float(s.sort_values("timestamp").iloc[0].p_yes)


def run_strategy(tbl: pd.DataFrame, nbs: pd.DataFrame, gfs: pd.DataFrame,
                 hrrr: dict[str, pd.DataFrame], prices: pd.DataFrame, *,
                 consensus_max: float = CONSENSUS_MAX_F,
                 offset: int = OFFSET,
                 local_floor: int = LOCAL_FLOOR_HOUR,
                 yes_price_max: float = YES_PRICE_MAX,
                 yes_price_min: float = YES_PRICE_MIN,
                 hrrr_min_cov: int = HRRR_MIN_PEAK_COV) -> pd.DataFrame:
    """Walk every (city, market_date); return a trade row per fill."""
    from lib.weather.hrrr import hrrr_peak_max_f_from_frame

    rows = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        station = day.station.iloc[0]
        tz = TZ[station]
        target = md.date()
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        fav = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == fav + offset]
        if row.empty:
            continue
        r = row.iloc[0]

        pk_s = (pd.Timestamp(target).tz_localize(tz) + pd.Timedelta(hours=12)).tz_convert("UTC")
        pk_e = (pd.Timestamp(target).tz_localize(tz) + pd.Timedelta(hours=22)).tz_convert("UTC")
        nbs_st = nbs[nbs.station == station]
        gfs_st = gfs[gfs.station == station]
        hrrr_frame = hrrr.get(station)
        local_mid_utc = pd.Timestamp(target).tz_localize(tz).tz_convert("UTC")

        entry_ts = None
        for local_hr in range(local_floor, 24):
            t = local_mid_utc + pd.Timedelta(hours=local_hr)
            n = _nbs_gfs_as_of(nbs_st, t, pk_s, pk_e)
            g = _nbs_gfs_as_of(gfs_st, t, pk_s, pk_e)
            h = hrrr_peak_max_f_from_frame(hrrr_frame, target, tz, t,
                                           min_coverage=hrrr_min_cov) \
                if hrrr_frame is not None else None
            if n is None or g is None or h is None:
                continue
            if max(n, g, h) - min(n, g, h) <= consensus_max:
                entry_ts = t
                break
        if entry_ts is None:
            continue

        yes_p = _pick_price(prices, r["slug"], entry_ts)
        if yes_p is None or yes_p < yes_price_min or yes_p > yes_price_max:
            continue
        price = 1 - yes_p
        won_no = 1 - int(r["won_yes"])
        fee = FEE_RATE * price * (1 - price)
        pnl = float(won_no) - price - fee
        rows.append({"city": city, "date": target, "station": station,
                     "entry_ts": entry_ts, "yes_price": yes_p, "price_paid": price,
                     "won_no": won_no, "fee": fee, "pnl": pnl,
                     "bucket_idx": int(r["bucket_idx"]),
                     "bucket_title": r["group_item_title"]})
    return pd.DataFrame(rows)


def _print_stats(df: pd.DataFrame, label: str) -> None:
    s = summarize(df)
    if s.n == 0:
        print(f"  {label:<32}  n=0")
        return
    print(f"  {label:<32}  n={s.n:>3}  W={s.wins:>3} L={s.losses:>2}  "
          f"hit={s.hit*100:>5.1f}%  per=${s.per_trade:>+.4f}  "
          f"tot=${s.total:>+.2f}  t={s.t:>+.2f}")


def main() -> int:
    print("Consensus-Fade +1 Offset — Backtest Reproducer (v2 canonical)")
    print("=" * 72)

    tbl_path = REPO / "data" / "processed" / "backtest_v2" / "trade_table.parquet"
    if not tbl_path.exists():
        sys.exit(f"Missing {tbl_path}. See STRATEGY.md §3 for data requirements.")
    tbl = pd.read_parquet(tbl_path)
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl[(tbl.date >= IS_START) & (tbl.date <= OOS_END)].copy()
    tbl = tbl.dropna(subset=["nbs_pred_max_f"])
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)

    print("Loading forecasts + prices...")
    nbs = _mos("NBS")
    gfs = _mos("GFS")
    hrrr = _hrrr()
    prices = _prices()
    print(f"  NBS={len(nbs):,}  GFS={len(gfs):,}  HRRR={len(hrrr):,}  "
          f"prices={len(prices):,}")

    print(f"\n=== Canonical: ≥{LOCAL_FLOOR_HOUR}:00 local, cs ≤ {CONSENSUS_MAX_F}°F, "
          f"yes_ask in [{YES_PRICE_MIN}, {YES_PRICE_MAX}] ===")
    t = run_strategy(tbl, nbs, gfs, hrrr, prices)
    _print_stats(t, "FULL period")
    _print_stats(t[t.date <= IS_END], f"IS  {IS_START}..{IS_END}")
    _print_stats(t[t.date >= OOS_START], f"OOS {OOS_START}..{OOS_END}")

    print("\n=== Tight-cap reference: yes_ask ≤ 0.22 (cosmetic 100% hit) ===")
    t22 = run_strategy(tbl, nbs, gfs, hrrr, prices, yes_price_max=0.22)
    _print_stats(t22, "FULL period")
    _print_stats(t22[t22.date <= IS_END], f"IS  {IS_START}..{IS_END}")
    _print_stats(t22[t22.date >= OOS_START], f"OOS {OOS_START}..{OOS_END}")

    print("\n=== Wide-cap reference: yes_ask ≤ 0.75 (shows hit-rate erosion) ===")
    t75 = run_strategy(tbl, nbs, gfs, hrrr, prices, yes_price_max=0.75)
    _print_stats(t75, "FULL period")
    _print_stats(t75[t75.date <= IS_END], f"IS  {IS_START}..{IS_END}")
    _print_stats(t75[t75.date >= OOS_START], f"OOS {OOS_START}..{OOS_END}")

    print("\n=== Per-city (canonical) ===")
    for city, g in t.sort_values("city").groupby("city"):
        _print_stats(g, city)

    print("\n=== Losses (canonical) ===")
    losses = t[t.won_no == 0]
    if losses.empty:
        print("  (none)")
    else:
        print(losses[["city", "date", "yes_price", "price_paid", "pnl"]].to_string(index=False))

    print("\n=== Daily aggregate (canonical) ===")
    daily = t.groupby("date").agg(
        n_trades=("pnl", "count"),
        day_pnl=("pnl", "sum"),
        day_capital=("price_paid", "sum"),
    ).reset_index()
    n_days = len(daily)
    n_pos = int((daily["day_pnl"] > 0).sum())
    sharpe = (daily["day_pnl"].mean() / daily["day_pnl"].std()
              if daily["day_pnl"].std() > 0 else 0)
    print(f"  Trading days: {n_days}")
    print(f"  Positive days: {n_pos} / {n_days}  ({n_pos/n_days*100:.0f}%)")
    print(f"  Avg trades/day: {daily['n_trades'].mean():.2f}")
    print(f"  Daily Sharpe: {sharpe:.3f}")
    print(f"  Annualized Sharpe: {sharpe * np.sqrt(252):.2f}")
    print(f"  Total PnL (1 share scale): ${daily['day_pnl'].sum():+.2f}")
    print(f"  Return on gross capital: "
          f"{daily['day_pnl'].sum() / daily['day_capital'].sum() * 100:.2f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())

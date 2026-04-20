"""Consensus-Fade +1 Offset — Backtest reproducer.

Runs the backtest exactly as described in STRATEGY.md §5. Outputs the
headline stats, IS/OOS split, consensus-threshold sweep, and per-city
breakdown.

Data requirements (must already exist in data/processed/):
- data/processed/backtest_v3/features.parquet  (per-station daily
  NBS/GFS/HRRR/METAR features, via notebooks/experiments/backtest-v3/
  build_features.py)
- data/processed/backtest_v2/trade_table.parquet  (per-slug market +
  resolution data, via notebooks/experiments/backtest-v2/harness.py)

If these are missing, regenerate them from the source scripts.

Usage:
    uv run python strategies/consensus_fade_plus1/backtest.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]

CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL",
    "Seattle": "SEA", "Chicago": "ORD", "Miami": "MIA",
    "Austin": "AUS", "Houston": "HOU", "Denver": "DEN",
    "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE_RATE = 0.05  # Polymarket weather fee formula: C × 0.05 × p × (1-p)

# Strategy parameters (pre-registered, from backtest v3 iter 7-8)
CONSENSUS_MAX_F = 3.0
OFFSET = 1  # NBS_fav + 1
SIDE = "NO"  # buy NO (fade)
YES_PRICE_MIN = 0.005
YES_PRICE_MAX = 0.5

# Fold split for in-sample / out-of-sample reporting (strategy discovery
# used Mar 11-25 IS, Mar 26-Apr 10 OOS)
IS_START = date(2026, 3, 11)
IS_END = date(2026, 3, 25)
OOS_START = date(2026, 3, 26)
OOS_END = date(2026, 4, 10)


@dataclass
class TradeStats:
    n: int
    hit_rate: float
    per_trade_pnl: float
    total_pnl: float
    std_pnl: float
    t_stat: float


def summarize(trades: pd.DataFrame) -> TradeStats:
    n = len(trades)
    if n == 0:
        return TradeStats(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    hit = float(trades["won_no"].mean())
    per = float(trades["pnl"].mean())
    total = float(trades["pnl"].sum())
    std = float(trades["pnl"].std(ddof=1)) if n > 1 else 0.0
    t = per / (std / n**0.5) if std > 0 else 0.0
    return TradeStats(n, hit, per, total, std, t)


def load_data() -> pd.DataFrame:
    """Join features + trade table; compute consensus spread."""
    feat_path = REPO / "data" / "processed" / "backtest_v3" / "features.parquet"
    tbl_path = REPO / "data" / "processed" / "backtest_v2" / "trade_table.parquet"
    if not feat_path.exists() or not tbl_path.exists():
        sys.exit(
            "Missing data. Run:\n"
            "  uv run python notebooks/experiments/backtest-v2/harness.py\n"
            "  uv run python notebooks/experiments/backtest-v3/build_features.py"
        )
    feat = pd.read_parquet(feat_path)
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat = feat.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"])
    feat["consensus_spread"] = (
        feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].max(axis=1)
        - feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].min(axis=1)
    )
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)
    feat = feat.dropna(subset=["city"])

    tbl = pd.read_parquet(tbl_path)
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date

    # Drop pre-existing consensus_spread from tbl to avoid collision
    for c in ("consensus_spread", "nbs_pred_max_f_f"):
        if c in tbl.columns:
            tbl = tbl.drop(columns=[c])

    tbl = tbl.merge(
        feat[["city", "local_date", "consensus_spread", "nbs_pred_max_f"]]
        .rename(columns={"local_date": "market_date"}),
        on=["city", "market_date"], how="left",
        suffixes=("", "_feat"),
    )
    tbl = tbl.dropna(subset=["consensus_spread", "nbs_pred_max_f"])
    tbl = tbl[(tbl.date >= IS_START) & (tbl.date <= OOS_END)].copy()
    return tbl


def run_strategy(df: pd.DataFrame, consensus_max: float,
                 offset: int, side: str = "NO",
                 yes_price_min: float = YES_PRICE_MIN,
                 yes_price_max: float = YES_PRICE_MAX) -> pd.DataFrame:
    """Apply the strategy to every (city, market_date) group."""
    trades = []
    for (city, md), grp in df.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        cs = float(day["consensus_spread"].iloc[0])
        if cs > consensus_max:
            continue
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == fav_idx + offset]
        if row.empty:
            continue
        r = row.iloc[0]
        yes_p = float(r["entry_price"])
        if yes_p < yes_price_min or yes_p > yes_price_max:
            continue
        if side == "NO":
            price = 1 - yes_p
            won = 1 - int(r["won_yes"])
        else:
            price = yes_p
            won = int(r["won_yes"])
        fee = FEE_RATE * price * (1 - price)
        pnl = float(won) - price - fee
        trades.append({
            "city": city, "market_date": md, "date": md.date(),
            "consensus_spread": cs,
            "nbs_pred": float(nbs_pred),
            "bucket_idx": int(r["bucket_idx"]),
            "bucket_title": r["group_item_title"],
            "yes_price": yes_p, "price_paid": price,
            "won_no": won, "fee": fee, "pnl": pnl,
        })
    return pd.DataFrame(trades)


def print_stats(trades: pd.DataFrame, label: str) -> None:
    s = summarize(trades)
    if s.n == 0:
        print(f"  {label:<30}  n=0")
        return
    print(f"  {label:<30}  n={s.n:>3}  hit={s.hit_rate*100:>5.1f}%  "
          f"per=${s.per_trade_pnl:>+.4f}  tot=${s.total_pnl:>+.2f}  t={s.t_stat:>+.2f}")


def main() -> int:
    print("Consensus-Fade +1 Offset — Backtest Reproducer")
    print("=" * 70)
    tbl = load_data()
    print(f"Loaded {len(tbl)} buckets × days ({tbl['date'].min()} → {tbl['date'].max()})")

    # === HEADLINE ===
    print(f"\n=== Primary result: consensus ≤ {CONSENSUS_MAX_F}°F + {SIDE} on offset=+{OFFSET} ===")
    t = run_strategy(tbl, CONSENSUS_MAX_F, OFFSET, SIDE)
    is_t = t[t.date <= IS_END]
    oos_t = t[t.date >= OOS_START]
    print_stats(t, "FULL period")
    print_stats(is_t, f"  IS: {IS_START} → {IS_END}")
    print_stats(oos_t, f"  OOS: {OOS_START} → {OOS_END}")

    # === CONSENSUS THRESHOLD SWEEP ===
    print(f"\n=== Consensus threshold sweep (offset=+{OFFSET} NO) ===")
    for cs_max in (1.0, 1.5, 2.0, 2.5, 3.0, 99.0):
        t_cs = run_strategy(tbl, cs_max, OFFSET, SIDE)
        label = f"cs ≤ {cs_max:.1f}°F" if cs_max < 99 else "no filter"
        print_stats(t_cs, label)

    # === OFFSET SWEEP UNDER CHOSEN CONSENSUS ===
    print(f"\n=== Offset sweep under consensus ≤ {CONSENSUS_MAX_F}°F ===")
    for off in (-2, -1, 0, 1, 2, 3):
        t_o = run_strategy(tbl, CONSENSUS_MAX_F, off, SIDE)
        print_stats(t_o, f"offset=+{off} NO")

    # === PER-CITY ===
    print(f"\n=== Per-city (primary strategy: cs ≤ {CONSENSUS_MAX_F}°F, +{OFFSET} NO) ===")
    t = run_strategy(tbl, CONSENSUS_MAX_F, OFFSET, SIDE)
    for city, g in t.sort_values("city").groupby("city"):
        s = summarize(g)
        if s.n < 2:
            print(f"  {city:<18} n={s.n:>2}  (too few)")
            continue
        print(f"  {city:<18} n={s.n:>2}  hit={s.hit_rate*100:>5.1f}%  "
              f"per=${s.per_trade_pnl:>+.4f}  tot=${s.total_pnl:>+.2f}  t={s.t_stat:>+.2f}")

    # === DAILY AGGREGATE ===
    print("\n=== Daily aggregate (primary strategy) ===")
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
    print(f"  Avg day PnL: ${daily['day_pnl'].mean():+.4f}")
    print(f"  Day PnL std: ${daily['day_pnl'].std():.4f}")
    print(f"  Daily Sharpe: {sharpe:.3f}")
    print(f"  Annualized Sharpe: {sharpe * np.sqrt(252):.2f}")
    print(f"  Avg capital/day: ${daily['day_capital'].mean():.2f}")
    print(f"  Total PnL (1 share scale): ${daily['day_pnl'].sum():+.2f}")
    print(f"  Total gross capital outlay: ${daily['day_capital'].sum():.2f}")
    print(f"  Return on gross capital: "
          f"{daily['day_pnl'].sum() / daily['day_capital'].sum() * 100:.2f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())

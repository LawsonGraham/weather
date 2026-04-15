"""Run S0-S4 on IS fold only. S5 is built separately.

Reports per-strategy: n, hit_rate, per_trade, total_pnl, std_pnl.
Also per-city breakdown to spot city-specific overfit.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import pandas as pd

from harness import run_strategy, summarize
from strategies import (
    S0_nbs_fav, S0b_market_fav,
    S1_plus2f, S1m_plus2f_mkt,
    S2_plus4f, S2m_plus4f_mkt,
    S3_basket_plus2_plus4, S3m_basket_plus2_plus4_mkt,
    S4_plus2f_nbs_spread_2_3,
)

STRATEGIES = [
    ("S0_nbs_fav", S0_nbs_fav),
    ("S0b_market_fav", S0b_market_fav),
    ("S1_plus2f_nbs", S1_plus2f),
    ("S1m_plus2f_mkt", S1m_plus2f_mkt),
    ("S2_plus4f_nbs", S2_plus4f),
    ("S2m_plus4f_mkt", S2m_plus4f_mkt),
    ("S3_basket_nbs", S3_basket_plus2_plus4),
    ("S3m_basket_mkt", S3m_basket_plus2_plus4_mkt),
    ("S4_spread_filter", S4_plus2f_nbs_spread_2_3),
]


def main():
    tbl = pd.read_parquet("/Users/lawsongraham/git/weather/data/processed/backtest_v2/trade_table.parquet")
    print(f"loaded {len(tbl)} rows")
    print()

    all_trades = []
    print(f"{'strategy':<30} {'n':>5} {'hit':>7} {'per_trade':>10} {'total':>10} {'std':>8} {'t_stat':>8}")
    print("-" * 86)
    for name, fn in STRATEGIES:
        t = run_strategy(tbl, fn, "IS", name)
        t["strategy"] = name
        all_trades.append(t)
        s = summarize(t)
        if s["n"] == 0:
            print(f"{name:<30} {'0':>5} (no trades)")
            continue
        # Welch-style t-stat: (per_trade - 0) / (std / sqrt(n))
        t_stat = s["per_trade"] / (s["std_pnl"] / (s["n"] ** 0.5)) if s["std_pnl"] > 0 else 0
        print(
            f"{name:<30} {s['n']:>5} "
            f"{s['hit_rate']*100:>6.1f}% "
            f"${s['per_trade']:>+8.3f} "
            f"${s['total_pnl']:>+8.2f} "
            f"{s['std_pnl']:>7.3f} "
            f"{t_stat:>+7.2f}"
        )

    print()
    print("=== per-city IS breakdown ===")
    combined = pd.concat(all_trades, ignore_index=True)
    pvt = combined.groupby(["strategy", "city"]).agg(
        n=("pnl", "count"), pnl=("pnl", "sum"), hit=("won_yes", "mean"),
    ).reset_index()
    for strat, grp in pvt.groupby("strategy"):
        print(f"\n{strat}:")
        for _, r in grp.iterrows():
            print(f"  {r['city']:<20} n={r['n']:>4} hit={r['hit']*100:>5.1f}%  pnl=${r['pnl']:>+7.2f}")

    # save IS trades
    out = Path("/Users/lawsongraham/git/weather/data/processed/backtest_v2/is_trades.parquet")
    combined.to_parquet(out, index=False)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()

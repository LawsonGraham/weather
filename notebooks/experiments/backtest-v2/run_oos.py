"""One-shot OOS evaluation (Apr 1 - Apr 10, 2026).

Strategies evaluated:
- S0-S4 (pre-registered in PRE_REGISTRATION.md)
- S6: exploratory "market-fav -1 offset" (discovered in IS offset sweep,
      flagged as exploratory, NOT pre-registered)

Per pre-reg: no tuning of strategies based on OOS results. One-shot only.
Results reported honestly whether good or bad.
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
    _market_fav_bucket_idx, _bucket_by_offset,
)


# --- S6 (exploratory, IS-discovered) -------------------------------- #
def S6_market_fav_minus1(day):
    fav = _market_fav_bucket_idx(day)
    t = _bucket_by_offset(day, fav, -1)
    return [t] if t is not None else []


STRATEGIES = [
    ("S0_nbs_fav", S0_nbs_fav, "CONTROL"),
    ("S0b_market_fav", S0b_market_fav, "CONTROL"),
    ("S1_plus2f_nbs", S1_plus2f, "PRE-REG"),
    ("S1m_plus2f_mkt", S1m_plus2f_mkt, "PRE-REG"),
    ("S2_plus4f_nbs", S2_plus4f, "PRE-REG"),
    ("S2m_plus4f_mkt", S2m_plus4f_mkt, "PRE-REG"),
    ("S3_basket_nbs", S3_basket_plus2_plus4, "PRE-REG"),
    ("S3m_basket_mkt", S3m_basket_plus2_plus4_mkt, "PRE-REG"),
    ("S4_spread_filter", S4_plus2f_nbs_spread_2_3, "PRE-REG"),
    ("S6_mkt_fav_minus1", S6_market_fav_minus1, "EXPLORATORY"),
]


def _fmt_row(name, tag, s):
    if s["n"] == 0:
        return f"{name:<25} [{tag:<11}]  (no trades)"
    ts = s["per_trade"] / (s["std_pnl"] / s["n"]**0.5) if s["std_pnl"] > 0 else 0
    return (
        f"{name:<25} [{tag:<11}]  n={s['n']:>3}  "
        f"hit={s['hit_rate']*100:>5.1f}%  "
        f"per=${s['per_trade']:>+7.3f}  "
        f"tot=${s['total_pnl']:>+8.2f}  "
        f"t={ts:>+6.2f}"
    )


def main():
    tbl = pd.read_parquet("/Users/lawsongraham/git/weather/data/processed/backtest_v2/trade_table.parquet")
    print(f"loaded {len(tbl)} rows")
    print()

    all_trades = []
    print("=== IS (Mar 11-31) ===")
    for name, fn, tag in STRATEGIES:
        t = run_strategy(tbl, fn, "IS", name)
        t["strategy"] = name
        t["tag"] = tag
        all_trades.append(t)
        s = summarize(t)
        print(_fmt_row(name, tag, s))

    print()
    print("=== OOS (Apr 1-10) ===")
    for name, fn, tag in STRATEGIES:
        t = run_strategy(tbl, fn, "OOS", name)
        t["strategy"] = name
        t["tag"] = tag
        all_trades.append(t)
        s = summarize(t)
        print(_fmt_row(name, tag, s))

    print()
    print("=== IS → OOS DELTA (per_trade) ===")
    print(f"{'strategy':<25}  {'IS':>9}  {'OOS':>9}  {'Δ':>9}  verdict")
    print("-" * 70)
    combined = pd.concat(all_trades, ignore_index=True)
    for name, _, tag in STRATEGIES:
        is_sub = combined[(combined.strategy==name) & (combined.fold=="IS")]
        oos_sub = combined[(combined.strategy==name) & (combined.fold=="OOS")]
        is_pt = is_sub.pnl.mean() if len(is_sub) else 0
        oos_pt = oos_sub.pnl.mean() if len(oos_sub) else 0
        delta = oos_pt - is_pt
        if len(oos_sub) == 0:
            verdict = "NO TRADES"
        elif oos_pt > 0 and is_pt > 0:
            verdict = "SURVIVES (both pos)"
        elif oos_pt > 0 > is_pt:
            verdict = "UNEXPECTED OOS WIN"
        elif oos_pt <= 0 and is_pt > 0:
            verdict = "FAILS OOS (was pos IS)"
        else:
            verdict = "FAILS OOS (both neg)"
        print(f"{name:<25}  ${is_pt:>+7.3f}  ${oos_pt:>+7.3f}  ${delta:>+7.3f}  {verdict}")

    # Save
    out = Path("/Users/lawsongraham/git/weather/data/processed/backtest_v2/all_trades.parquet")
    combined.to_parquet(out, index=False)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()

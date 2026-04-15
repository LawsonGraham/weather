"""Exploratory offset sweep across -4 to +6 bucket offsets (IS only).

This IS allowed exploratory analysis — the pre-reg only locks the OOS
evaluation set. We can study IS to see if ANY offset shows edge.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import pandas as pd

from harness import run_strategy, summarize
from strategies import _nbs_fav_bucket_idx, _bucket_by_offset


def make_offset_strategy(offset_buckets: int, use_market_fav: bool = False):
    def _sel(day):
        if use_market_fav:
            fav = int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])
        else:
            fav = _nbs_fav_bucket_idx(day)
        t = _bucket_by_offset(day, fav, offset_buckets)
        return [t] if t is not None else []
    return _sel


def main():
    tbl = pd.read_parquet("/Users/lawsongraham/git/weather/data/processed/backtest_v2/trade_table.parquet")
    print("=== IS offset sweep (NBS anchor) ===")
    print(f"{'offset':>6}  {'n':>4}  {'hit':>6}  {'per_trade':>10}  {'total':>9}  {'t_stat':>7}")
    rows = []
    for off in range(-4, 7):
        fn = make_offset_strategy(off, use_market_fav=False)
        t = run_strategy(tbl, fn, "IS", f"nbs_off_{off:+d}")
        s = summarize(t)
        if s["n"] == 0:
            continue
        ts = s["per_trade"] / (s["std_pnl"] / s["n"]**0.5) if s["std_pnl"] > 0 else 0
        print(f"  {off:+d}  {s['n']:>4}  {s['hit_rate']*100:>5.1f}%  ${s['per_trade']:>+8.3f}  ${s['total_pnl']:>+7.2f}  {ts:>+6.2f}")
        rows.append({"anchor":"NBS","offset":off, **s, "t_stat": ts})

    print()
    print("=== IS offset sweep (market anchor) ===")
    print(f"{'offset':>6}  {'n':>4}  {'hit':>6}  {'per_trade':>10}  {'total':>9}  {'t_stat':>7}")
    for off in range(-4, 7):
        fn = make_offset_strategy(off, use_market_fav=True)
        t = run_strategy(tbl, fn, "IS", f"mkt_off_{off:+d}")
        s = summarize(t)
        if s["n"] == 0:
            continue
        ts = s["per_trade"] / (s["std_pnl"] / s["n"]**0.5) if s["std_pnl"] > 0 else 0
        print(f"  {off:+d}  {s['n']:>4}  {s['hit_rate']*100:>5.1f}%  ${s['per_trade']:>+8.3f}  ${s['total_pnl']:>+7.2f}  {ts:>+6.2f}")
        rows.append({"anchor":"MKT","offset":off, **s, "t_stat": ts})

    pd.DataFrame(rows).to_csv(
        "/Users/lawsongraham/git/weather/data/processed/backtest_v2/offset_sweep_is.csv",
        index=False
    )


if __name__ == "__main__":
    main()

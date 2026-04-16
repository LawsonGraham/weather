"""Strict clean holdout validation.

Principle: use ONLY Mar 11-25 for strategy discovery (13 days).
Apply discovered strategy to Mar 26-Apr 10 (16 days) blind.

This addresses the in-sample concern from iter 5: the "+1 offset YES
loses" pattern was observed on the FULL Mar 11-Apr 10 window, so
my "within-OOS split" was post-hoc slicing.

For a properly clean test:
1. ON MAR 11-25 ONLY, sweep offsets and find which are consistently
   mispriced.
2. Lock in the best-performing offset(s) and strategy.
3. Apply blindly to Mar 26-Apr 10. Report as OOS.

Comparison baseline: the iter-5 full-sample result.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE = 0.05

STRAT_IS_START = date(2026, 3, 11)
STRAT_IS_END = date(2026, 3, 25)
STRAT_OOS_START = date(2026, 3, 26)
STRAT_OOS_END = date(2026, 4, 10)


def main():
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date

    def apply_off(day, off):
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == fav_idx + off]
        return row.iloc[0] if len(row) == 1 else None

    def eval_yes(df, off, min_p=0.005, max_p=0.95):
        """Buy YES at the offset bucket."""
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            r = apply_off(day, off)
            if r is None or r["entry_price"] < min_p or r["entry_price"] > max_p:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({"city": city, "date": md.date(), "price": float(r["entry_price"]),
                          "won": int(r["won_yes"]), "pnl": pnl})
        return pd.DataFrame(trades)

    def eval_no(df, off, min_yes_p=0.005, max_yes_p=0.5):
        """Buy NO at the offset bucket."""
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            r = apply_off(day, off)
            if r is None:
                continue
            yes_p = float(r["entry_price"])
            if yes_p < min_yes_p or yes_p > max_yes_p:
                continue
            no_p = 1 - yes_p
            no_won = 1 - int(r["won_yes"])
            fee = FEE * no_p * (1 - no_p)
            pnl = no_won - no_p - fee
            trades.append({"city": city, "date": md.date(), "no_price": no_p,
                          "won_no": no_won, "pnl": pnl})
        return pd.DataFrame(trades)

    def stats(t, name):
        if len(t) == 0:
            return None
        std = t.pnl.std() if len(t) > 1 else 0
        ts = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        won_col = "won" if "won" in t.columns else "won_no"
        hit_col = won_col if won_col in t.columns else None
        hit_rate = t[hit_col].mean() if hit_col else 0
        print(f"  {name:<32}  n={len(t):>3}  hit={hit_rate*100:>5.1f}%  "
              f"per=${t.pnl.mean():>+.4f}  tot=${t.pnl.sum():>+.2f}  t={ts:>+.2f}")
        return {"n": len(t), "per": t.pnl.mean(), "tot": t.pnl.sum(),
                "std": std, "t": ts, "hit": hit_rate}

    # IS data (Mar 11-25)
    is_tbl = tbl[(tbl.date >= STRAT_IS_START) & (tbl.date <= STRAT_IS_END)].copy()
    oos_tbl = tbl[(tbl.date >= STRAT_OOS_START) & (tbl.date <= STRAT_OOS_END)].copy()
    print(f"Strategy-IS: Mar 11-25, n_tradeable_days = "
          f"{is_tbl[['city','market_date']].drop_duplicates().shape[0]}")
    print(f"Strategy-OOS: Mar 26-Apr10, n_tradeable_days = "
          f"{oos_tbl[['city','market_date']].drop_duplicates().shape[0]}")

    print()
    print("=== STEP 1: Strategy discovery on Mar 11-25 (IS) ONLY ===")
    print("\nOffset sweep — buy YES:")
    is_yes = {}
    for off in range(-3, 4):
        t = eval_yes(is_tbl, off)
        is_yes[off] = stats(t, f"  YES offset={off:+d}")

    print("\nOffset sweep — buy NO:")
    is_no = {}
    for off in range(-3, 4):
        t = eval_no(is_tbl, off, min_yes_p=0.005, max_yes_p=0.5)
        is_no[off] = stats(t, f"  NO offset={off:+d}")

    # Find the best IS offset based on t-stat
    best_no_offset = None
    best_no_t = 0
    for off, s in is_no.items():
        if s and s["n"] >= 10 and s["t"] > best_no_t:
            best_no_t = s["t"]
            best_no_offset = off
    print(f"\nBest IS offset for NO strategy: {best_no_offset} (t={best_no_t:.2f})")

    print()
    print("=== STEP 2: Apply to Mar 26-Apr 10 (OOS) blind ===")
    if best_no_offset is not None:
        t_oos = eval_no(oos_tbl, best_no_offset, min_yes_p=0.005, max_yes_p=0.5)
        stats(t_oos, f"OOS @ best NO offset={best_no_offset:+d}")

    # Also run the SAME strategy on the full period for reference
    print(f"\n=== Reference: full Mar 11-Apr10 at offset=+1 NO ===")
    t_full = eval_no(pd.concat([is_tbl, oos_tbl]), 1, min_yes_p=0.005, max_yes_p=0.5)
    stats(t_full, "Full Mar 11-Apr 10")

    # Also run buy-YES offset=0 for comparison (common baseline)
    print(f"\n=== Additional: buy NBS-fav YES (offset=0) ===")
    print("IS (Mar 11-25):")
    t_is_fav = eval_yes(is_tbl, 0)
    stats(t_is_fav, "YES offset=0")
    print("OOS (Mar 26-Apr 10):")
    t_oos_fav = eval_yes(oos_tbl, 0)
    stats(t_oos_fav, "YES offset=0")

    # Let's also verify: is the "+1 offset NO edge" clean on OOS alone?
    # (as a pre-known strategy from iter 3)
    print(f"\n=== The iter-5 claim re-verified: OOS-only Mar 26-Apr 10 ===")
    for off in (-1, 0, 1, 2, 3):
        print(f"  offset={off:+d}:")
        t_yes = eval_yes(oos_tbl, off)
        stats(t_yes, "    YES")
        t_no = eval_no(oos_tbl, off, min_yes_p=0.005, max_yes_p=0.5)
        stats(t_no, "    NO")


if __name__ == "__main__":
    main()

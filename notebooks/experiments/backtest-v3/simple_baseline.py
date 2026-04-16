"""Ultra-simple baseline: buy the bucket one below NBS favorite always.

Given 74% of IS days and ~74% of OOS days have actual < NBS_pred, a
simple "fade NBS by 1 bucket" strategy should be the mechanical edge.

Also test:
- Buy bucket centered on median-prediction (NBS + IS_median_error)
- Buy bucket centered on mean-prediction (NBS + IS_mean_error)
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
from datetime import date

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE = 0.05


def main():
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    # Compute IS stats from features
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat["nbs_err"] = feat["actual_max_f"] - feat["nbs_pred_max_f"]
    is_err = feat[feat.fold == "IS"]["nbs_err"].dropna()
    print(f"IS: n={len(is_err)}, mean(actual-NBS)={is_err.mean():.3f}, "
          f"median={is_err.median():.3f}, P(actual<NBS)={(is_err<0).mean():.3f}")

    # Apply offset strategies
    def apply_offset(day, offset_buckets):
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        target = nbs_fav_idx + offset_buckets
        row = day[day["bucket_idx"] == target]
        if row.empty:
            return None
        return row.iloc[0]

    def run(df, offset, min_p=0.02, max_p=0.95):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            r = apply_offset(day, offset)
            if r is None or r["entry_price"] < min_p or r["entry_price"] > max_p:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({"city": city, "date": md.date(),
                          "price": float(r["entry_price"]),
                          "won_yes": int(r["won_yes"]),
                          "pnl": pnl})
        return pd.DataFrame(trades)

    def summarize(t, name):
        if len(t) == 0:
            return
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name:<24}  n={len(t):>3}  hit={t.won_yes.mean()*100:>5.1f}%  "
              f"per=${t.pnl.mean():>+.3f}  tot=${t.pnl.sum():>+.2f}  t={tstat:>+.2f}  "
              f"avg_price=${t.price.mean():.3f}")

    # Split IS / OOS
    tbl_first = tbl[tbl.date <= date(2026, 3, 25)]
    tbl_second = tbl[tbl.date > date(2026, 3, 25)]

    print("\n=== Offset strategy (shift from NBS fav) — FULL Mar 11-Apr 10 ===")
    for off in (-3, -2, -1, 0, 1, 2):
        t = run(tbl, off)
        summarize(t, f"offset={off}")

    print("\n=== Offset strategy — FIRST HALF Mar 11-25 ===")
    for off in (-3, -2, -1, 0, 1, 2):
        t = run(tbl_first, off)
        summarize(t, f"offset={off}")

    print("\n=== Offset strategy — SECOND HALF Mar 26-Apr 10 ===")
    for off in (-3, -2, -1, 0, 1, 2):
        t = run(tbl_second, off)
        summarize(t, f"offset={off}")

    # Per-city for -1 offset (the natural "fade NBS")
    print("\n=== offset=-1 per city (FULL) ===")
    full = run(tbl, -1)
    if len(full) > 0:
        for city, g in full.groupby("city"):
            std = g.pnl.std() if len(g) > 1 else 0
            tstat = g.pnl.mean() / (std / len(g)**0.5) if std > 0 else 0
            print(f"  {city:<18} n={len(g):>3}  hit={g.won_yes.mean()*100:>5.1f}%  "
                  f"per=${g.pnl.mean():>+.3f}  tot=${g.pnl.sum():>+.2f}  t={tstat:>+.2f}")

    # Per-week pattern for offset=-1
    print("\n=== offset=-1 by week ===")
    if len(full) > 0:
        full["week"] = pd.to_datetime(full.date).dt.isocalendar().week
        for wk, g in full.groupby("week"):
            std = g.pnl.std() if len(g) > 1 else 0
            tstat = g.pnl.mean() / (std / len(g)**0.5) if std > 0 else 0
            print(f"  Wk{wk}: n={len(g):>3}  hit={g.won_yes.mean()*100:>5.1f}%  "
                  f"per=${g.pnl.mean():>+.3f}  tot=${g.pnl.sum():>+.2f}  t={tstat:>+.2f}")


if __name__ == "__main__":
    main()

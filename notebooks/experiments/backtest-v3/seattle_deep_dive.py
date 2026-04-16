"""Seattle deep-dive: why is it consistently positive across strategies?

Seattle (KSEA) stats from iter 2:
- NBS OOS MAE: 1.20 (best)
- IS bias: -0.37, OOS bias: -0.11 (very stable)
- Per-station model OOS MAE: 1.83 (slightly WORSE than NBS)

Seattle gave the best per-city results in iter 4:
- per-city offset=0: 61.5% hit, +$0.167/trade
- per-city shifted: 61.5% hit, +$0.184/trade

Hypothesis: Seattle's weather is highly predictable (small daily
variance), so NBS is accurate AND the market happens to slightly
underprice the NBS favorite. The +3-4% edge compounds into $0.18/trade.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np
from datetime import date
import duckdb

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"

FEE = 0.05


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat["nbs_err"] = feat["actual_max_f"] - feat["nbs_pred_max_f"]

    # Seattle-specific analysis
    sea = feat[feat.station == "SEA"].dropna(subset=["actual_max_f", "nbs_pred_max_f"]).copy()
    sea["nbs_abs_err"] = sea["nbs_err"].abs()
    print(f"Seattle daily max distribution:")
    print(sea.groupby("fold")["actual_max_f"].agg(["mean", "std", "min", "max"]))
    print()
    print("NBS prediction quality for Seattle:")
    print(sea.groupby("fold")["nbs_err"].agg(["mean", "std", "count"]))
    print()
    print(f"Absolute NBS error (MAE) by fold:")
    for f in ("IS", "OOS"):
        m = sea[sea.fold == f].nbs_abs_err.mean()
        print(f"  {f}: {m:.3f}")

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl[tbl.city == "Seattle"]
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))]

    # Seattle day-by-day: NBS fav bucket, actual, and whether market was "efficient"
    print()
    print("Seattle day-by-day:")
    for md, grp in tbl.groupby("market_date"):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if len(day) < 9: continue
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        actual = day["actual_max_f"].iloc[0] if day["actual_max_f"].notna().any() else None
        # NBS fav bucket
        diff = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        nbs_fav_row = day[day["bucket_idx"] == nbs_fav_idx].iloc[0]
        # Market fav bucket
        mkt_fav_idx = int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])
        mkt_fav_row = day[day["bucket_idx"] == mkt_fav_idx].iloc[0]
        # Winning bucket
        win_row = day[day["won_yes"] == 1]
        win_idx = int(win_row["bucket_idx"].iloc[0]) if len(win_row) else None
        print(f"  {md.date()}: NBS={nbs_pred:>5.1f} actual={actual if actual else 'NA':<5}  "
              f"nbs_fav={nbs_fav_idx} (${nbs_fav_row['entry_price']:.3f})  "
              f"mkt_fav={mkt_fav_idx} (${mkt_fav_row['entry_price']:.3f})  "
              f"winner={win_idx}")

    # Compute various "buy NBS fav" PnLs
    print()
    print("Seattle strategy comparison:")

    def apply_off(day, off):
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == nbs_fav_idx + off]
        return row.iloc[0] if len(row) == 1 else None

    def eval_strat(off, name):
        trades = []
        for md, grp in tbl.groupby("market_date"):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            r = apply_off(day, off)
            if r is None or r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({"pnl": pnl, "won": int(r["won_yes"]),
                          "price": float(r["entry_price"])})
        t = pd.DataFrame(trades)
        if len(t) == 0:
            return
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(t)}  hit={t.won.mean()*100:.1f}%  "
              f"per=${t.pnl.mean():+.3f}  tot=${t.pnl.sum():+.2f}  t={tstat:+.2f}")

    for off in range(-3, 4):
        eval_strat(off, f"offset={off:+d}")

    # Buy MARKET favorite (not NBS)
    print()
    print("Seattle: buy MARKET favorite")
    trades = []
    for md, grp in tbl.groupby("market_date"):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        r = day.loc[day["entry_price"].idxmax()]
        if r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
            continue
        fee = FEE * r["entry_price"] * (1 - r["entry_price"])
        pnl = float(r["won_yes"]) - r["entry_price"] - fee
        trades.append({"pnl": pnl, "won": int(r["won_yes"]), "price": float(r["entry_price"])})
    t = pd.DataFrame(trades)
    std = t.pnl.std() if len(t) > 1 else 0
    tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
    print(f"  n={len(t)}  hit={t.won.mean()*100:.1f}%  per=${t.pnl.mean():+.3f}  "
          f"tot=${t.pnl.sum():+.2f}  t={tstat:+.2f}  avg_price=${t.price.mean():.3f}")


if __name__ == "__main__":
    main()

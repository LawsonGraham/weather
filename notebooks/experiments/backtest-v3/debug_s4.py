"""Debug S4 — is the IS→OOS inversion a real signal or noise?

S4: buy market favorite iff ensemble-model prediction's favorite bucket
is within 1 bucket of the market-favorite bucket.

IS: +$0.010/trade on 103 trades (flat)
OOS: +$0.101/trade on 74 trades (strong)

This is backwards from the expected IS≥OOS pattern. Possible
explanations:
1. Noise on small samples (103/74)
2. Period effect — Mar vs Apr differ in some feature
3. Real edge that's stronger in certain regimes
4. Selection bias in trade counts (if we filter 30% out, is that 30%
   systematically different between IS and OOS?)

This script audits to distinguish.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np
from datetime import date

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"

CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
IS_START = date(2026, 3, 11)
IS_END = date(2026, 3, 31)
OOS_START = date(2026, 4, 1)
OOS_END = date(2026, 4, 10)
FEE = 0.05


def main():
    # Rebuild predictions (same as simple_bias_strat.py)
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date

    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feats_cols = ["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
                  "yesterday_max_f", "tmp_noon_f", "tmp_morning_f",
                  "nbs_spread_f", "nbs_minus_gfs", "hrrr_minus_nbs",
                  "day_of_year", "month"]
    complete = feat.dropna(subset=feats_cols + ["actual_max_f"]).copy()
    complete["nbs_spread_f"] = complete["nbs_spread_f"].fillna(complete["nbs_spread_f"].median())
    is_train = complete[complete.fold == "IS"]

    from sklearn.linear_model import Ridge
    model = Ridge(alpha=5.0)
    model.fit(is_train[feats_cols].values, is_train["actual_max_f"].values)
    complete["pred_linear"] = model.predict(complete[feats_cols].values)

    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    complete["city"] = complete["station"].map(station_to_city)
    complete = complete.dropna(subset=["city"])

    tbl = tbl.merge(
        complete[["city", "local_date", "pred_linear"]].rename(
            columns={"local_date": "market_date"}),
        on=["city", "market_date"], how="left"
    )
    tbl = tbl.dropna(subset=["pred_linear"])

    def fold(d):
        if IS_START <= d <= IS_END: return "IS"
        if OOS_START <= d <= OOS_END: return "OOS"
        return "OOB"
    tbl["strat_fold"] = tbl["date"].apply(fold)
    tbl = tbl[tbl.strat_fold.isin(["IS", "OOS"])].copy()

    # For each (city, market_date), compute market_fav_idx and model_fav_idx
    results = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        mkt_fav_idx = int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])
        pred = day["pred_linear"].iloc[0]
        diff = (day["bucket_center"] - pred).abs()
        model_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        mkt_fav_row = day[day["bucket_idx"] == mkt_fav_idx].iloc[0]
        gap = abs(mkt_fav_idx - model_fav_idx)
        fee = FEE * mkt_fav_row["entry_price"] * (1 - mkt_fav_row["entry_price"])
        pnl_mkt_fav = float(mkt_fav_row["won_yes"]) - mkt_fav_row["entry_price"] - fee

        results.append({
            "city": city, "market_date": md,
            "strat_fold": day["strat_fold"].iloc[0],
            "mkt_fav_idx": mkt_fav_idx,
            "model_fav_idx": model_fav_idx,
            "gap": gap,
            "mkt_fav_price": float(mkt_fav_row["entry_price"]),
            "won_yes": int(mkt_fav_row["won_yes"]),
            "pnl": pnl_mkt_fav,
            "model_pred": pred,
            "actual_max": mkt_fav_row.get("actual_max_f"),
            "nbs_pred": mkt_fav_row.get("nbs_pred_max_f"),
        })
    res = pd.DataFrame(results)
    print(f"Total (city, market_date) days in IS/OOS: {len(res)}")
    print(res.groupby("strat_fold").size())

    print()
    print("=== Gap distribution between market_fav and model_fav ===")
    print(res.groupby(["strat_fold", "gap"]).size().unstack(fill_value=0))

    print()
    print("=== Per-gap strategy ===")
    for gap_max in (0, 1, 2, 99):
        sub = res[res.gap <= gap_max]
        for f in ("IS", "OOS"):
            fsub = sub[sub.strat_fold == f]
            if len(fsub) == 0:
                continue
            hit = fsub.won_yes.mean()
            per = fsub.pnl.mean()
            std = fsub.pnl.std() if len(fsub) > 1 else 0
            t = per / (std / len(fsub)**0.5) if std > 0 else 0
            print(f"  gap<={gap_max:<2}  {f:<4}  n={len(fsub):>3}  hit={hit*100:>5.1f}%  "
                  f"per=${per:>+7.3f}  tot=${fsub.pnl.sum():>+6.2f}  t={t:>+5.2f}  "
                  f"avg_price=${fsub.mkt_fav_price.mean():.3f}")

    print()
    print("=== Did OOS's strong S4 come from a specific gap level? ===")
    oos_only = res[res.strat_fold == "OOS"]
    print("OOS by gap:")
    for g, grp in oos_only.groupby("gap"):
        hit = grp.won_yes.mean()
        per = grp.pnl.mean()
        print(f"  gap={g}  n={len(grp):>3}  hit={hit*100:>5.1f}%  per=${per:>+7.3f}  "
              f"tot=${grp.pnl.sum():>+5.2f}")

    # Check if OOS's OOS-specific period (Apr 1-10) had unusually high
    # favorite hit rates for structural reason
    print()
    print("=== Market-favorite hit rate BY WEEK (sanity) ===")
    res["week"] = pd.to_datetime(res.market_date).dt.isocalendar().week
    for (fold, wk), g in res.groupby(["strat_fold", "week"]):
        hit = g.won_yes.mean()
        per = g.pnl.mean()
        print(f"  {fold:<4} wk{wk}  n={len(g):>3}  hit={hit*100:>5.1f}%  per=${per:>+7.3f}")

    # Most damning check: WHEN model disagrees with market (gap > 1) on OOS,
    # is the model or the market right more often?
    print()
    print("=== When model disagrees with market (gap ≥ 2), who is right? ===")
    for f in ("IS", "OOS"):
        fsub = res[(res.strat_fold == f) & (res.gap >= 2)]
        print(f"\n{f}: {len(fsub)} days where |model_fav - mkt_fav| ≥ 2")
        if len(fsub) == 0: continue
        fsub = fsub.copy()
        # Did the model fav win? Did the market fav win?
        fsub["mkt_fav_won"] = fsub.won_yes == 1
        # Actual max vs model_fav center vs mkt_fav center
        fsub["actual_near_model"] = (fsub.actual_max - fsub.pnl).abs()  # proxy
        mkt_win = fsub.mkt_fav_won.mean()
        print(f"  Market fav hit rate (when disagreeing): {mkt_win*100:.1f}%")


if __name__ == "__main__":
    main()

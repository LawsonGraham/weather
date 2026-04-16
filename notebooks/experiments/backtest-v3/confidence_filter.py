"""Test confidence-filter variants on the max-model_p strategy.

We showed in iter-2 that `max model_p` with per-station + Normal CDF
gives IS +$0.036, OOS +$0.046 /trade — directionally consistent but
underpowered.

Hypotheses for filters that might sharpen this:
1. NBS txn_spread (model uncertainty): low spread → higher confidence → better bets
2. top1 - top2 probability gap: wide → model is decisive → should win more often
3. distance of model prediction from market favorite: when model disagrees
   with market slightly, maybe that's the alpha spot

We evaluate each filter on IS + OOS. We LOCK the filter threshold on IS
and report OOS at the locked threshold.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
from datetime import date
from scipy.stats import norm

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

FEATS = ["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
         "yesterday_max_f", "tmp_noon_f", "tmp_morning_f",
         "nbs_minus_gfs", "hrrr_minus_nbs", "day_of_year"]


def bucket_prob(pred, sigma, lo, hi):
    p_hi = 1.0 if hi == float("inf") else norm.cdf(hi + 0.5, pred, sigma)
    p_lo = 0.0 if lo == float("-inf") else norm.cdf(lo - 0.5, pred, sigma)
    return max(0.0, p_hi - p_lo)


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    complete = feat.dropna(subset=FEATS + ["actual_max_f"]).copy()

    # Train per-station models
    from sklearn.linear_model import Ridge
    per_st_models = {}
    for st, sub in complete.groupby("station"):
        tr = sub[sub.fold == "IS"]
        if len(tr) < 30:
            continue
        m = Ridge(alpha=5.0)
        m.fit(tr[FEATS].values, tr["actual_max_f"].values)
        yhat = m.predict(tr[FEATS].values)
        sigma = float((tr["actual_max_f"].values - yhat).std(ddof=1))
        per_st_models[st] = {"model": m, "sigma": sigma}

    # Predict for all complete rows
    complete["pred"] = None
    complete["sigma"] = None
    for st, info in per_st_models.items():
        mask = complete.station == st
        complete.loc[mask, "pred"] = info["model"].predict(complete.loc[mask, FEATS].values)
        complete.loc[mask, "sigma"] = info["sigma"]
    complete["pred"] = pd.to_numeric(complete["pred"])
    complete["sigma"] = pd.to_numeric(complete["sigma"])

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)

    # Drop nbs_spread_f from tbl (from v2) to avoid collision
    if "nbs_spread_f" in tbl.columns:
        tbl = tbl.drop(columns=["nbs_spread_f"])
    tbl = tbl.merge(
        complete[["station", "local_date", "pred", "sigma", "nbs_spread_f"]].rename(
            columns={"local_date": "market_date"}),
        on=["station", "market_date"], how="left"
    )
    tbl = tbl.dropna(subset=["pred", "sigma"])

    tbl["model_p"] = tbl.apply(
        lambda r: bucket_prob(r["pred"], r["sigma"], r["bucket_low"], r["bucket_high"]),
        axis=1
    )
    tbl["edge"] = tbl["model_p"] - tbl["entry_price"]

    def fold(d):
        if IS_START <= d <= IS_END: return "IS"
        if OOS_START <= d <= OOS_END: return "OOS"
        return "OOB"
    tbl["strat_fold"] = tbl["date"].apply(fold)
    tbl = tbl[tbl.strat_fold.isin(["IS", "OOS"])].copy()

    # For each (city, market_date), compute: top1_p, top2_p, gap, and the
    # max_model_p bucket_idx
    daily = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        # Distribution
        probs_sorted = day.sort_values("model_p", ascending=False)
        top1_row = probs_sorted.iloc[0]
        top2_p = probs_sorted.iloc[1]["model_p"] if len(probs_sorted) > 1 else 0
        top_gap = top1_row["model_p"] - top2_p
        mkt_fav_idx = int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])
        mdl_fav_idx = int(top1_row["bucket_idx"])
        gap_to_mkt = abs(mdl_fav_idx - mkt_fav_idx)
        daily.append({
            "city": city, "market_date": md, "strat_fold": day["strat_fold"].iloc[0],
            "mdl_bucket": mdl_fav_idx,
            "mkt_bucket": mkt_fav_idx,
            "bucket_gap": gap_to_mkt,
            "top1_p": float(top1_row["model_p"]),
            "top2_p": float(top2_p),
            "top_gap": float(top_gap),
            "nbs_spread": float(day["nbs_spread_f"].iloc[0]) if pd.notna(day["nbs_spread_f"].iloc[0]) else 2.0,
            "mdl_price": float(top1_row["entry_price"]),
            "mdl_edge": float(top1_row["edge"]),
            "mkt_price": float(day.loc[day["entry_price"].idxmax(), "entry_price"]),
            "mdl_won": int(top1_row["won_yes"]),
            "mdl_pnl": (float(top1_row["won_yes"])
                        - top1_row["entry_price"]
                        - FEE * top1_row["entry_price"] * (1 - top1_row["entry_price"])),
        })
    d = pd.DataFrame(daily)
    # Drop extreme prices
    d = d[(d.mdl_price >= 0.02) & (d.mdl_price <= 0.95)]
    print(f"Daily rows: {len(d)} ({d.strat_fold.value_counts().to_dict()})")

    def show(sub, name):
        if len(sub) == 0:
            print(f"  {name}: n=0")
            return
        std = sub.mdl_pnl.std() if len(sub) > 1 else 0
        tstat = sub.mdl_pnl.mean() / (std / len(sub)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(sub):>3}  hit={sub.mdl_won.mean()*100:>5.1f}%  "
              f"per=${sub.mdl_pnl.mean():>+.3f}  tot=${sub.mdl_pnl.sum():>+.2f}  t={tstat:>+.2f}  "
              f"avg_price=${sub.mdl_price.mean():.3f}")

    # Baseline unfiltered
    print("\n=== UNFILTERED (baseline) ===")
    show(d[d.strat_fold == "IS"], "IS ")
    show(d[d.strat_fold == "OOS"], "OOS")

    # Filter 1: top_gap threshold (model confidence = gap between top-1 and top-2)
    print("\n=== Filter 1: top_gap > threshold (model decisiveness) ===")
    for th in (0.02, 0.05, 0.08, 0.10, 0.15):
        sub = d[d.top_gap > th]
        print(f"  top_gap > {th}")
        show(sub[sub.strat_fold == "IS"], "   IS ")
        show(sub[sub.strat_fold == "OOS"], "   OOS")

    # Filter 2: top1_p > threshold (absolute confidence)
    print("\n=== Filter 2: top1_p > threshold (prediction strength) ===")
    for th in (0.25, 0.30, 0.35, 0.40, 0.45):
        sub = d[d.top1_p > th]
        print(f"  top1_p > {th}")
        show(sub[sub.strat_fold == "IS"], "   IS ")
        show(sub[sub.strat_fold == "OOS"], "   OOS")

    # Filter 3: nbs_spread ≤ threshold (low NBS uncertainty)
    print("\n=== Filter 3: nbs_spread ≤ threshold ===")
    for th in (1.0, 1.5, 2.0, 3.0):
        sub = d[d.nbs_spread <= th]
        print(f"  nbs_spread <= {th}")
        show(sub[sub.strat_fold == "IS"], "   IS ")
        show(sub[sub.strat_fold == "OOS"], "   OOS")

    # Filter 4: bucket_gap (model vs market agreement)
    print("\n=== Filter 4: model within N buckets of market favorite ===")
    for th in (0, 1, 2):
        sub = d[d.bucket_gap <= th]
        print(f"  bucket_gap <= {th}")
        show(sub[sub.strat_fold == "IS"], "   IS ")
        show(sub[sub.strat_fold == "OOS"], "   OOS")

    # Filter 5: compound — top_gap > 0.05 AND nbs_spread ≤ 2
    print("\n=== Filter 5: top_gap>0.05 AND nbs_spread≤2 (compound) ===")
    sub = d[(d.top_gap > 0.05) & (d.nbs_spread <= 2)]
    show(sub[sub.strat_fold == "IS"], "IS ")
    show(sub[sub.strat_fold == "OOS"], "OOS")

    # Filter 6: reverse — when model disagrees with market, don't bet
    print("\n=== Filter 6: bucket_gap=0 (model and market agree on favorite) ===")
    sub = d[d.bucket_gap == 0]
    show(sub[sub.strat_fold == "IS"], "IS ")
    show(sub[sub.strat_fold == "OOS"], "OOS")

    # Filter 7: reverse - trade only when model disagrees by exactly 1
    print("\n=== Filter 7: bucket_gap=1 (model picks different adjacent bucket) ===")
    sub = d[d.bucket_gap == 1]
    show(sub[sub.strat_fold == "IS"], "IS ")
    show(sub[sub.strat_fold == "OOS"], "OOS")


if __name__ == "__main__":
    main()

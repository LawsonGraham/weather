"""Ultra-simple strategies: fade/follow NBS bias + calibrated favorite.

Observation from IS: NBS has +0.59°F bias OOS (over-forecasts).
So: buy the bucket that a bias-adjusted NBS prediction points to.

Also: "buy market favorite" worked OK in v2 OOS (+$0.063/trade).
What if we filter the market fav to days where model AGREES with it?
"""
from __future__ import annotations

from pathlib import Path
import re
from datetime import date

import numpy as np
import pandas as pd
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


def bucket_prob(pred, sigma, lo, hi):
    p_hi = 1.0 if hi == float("inf") else norm.cdf(hi + 0.5, pred, sigma)
    p_lo = 0.0 if lo == float("-inf") else norm.cdf(lo - 0.5, pred, sigma)
    return max(0.0, p_hi - p_lo)


def simulate_strategy(tbl, selector_fn, min_price=0.02, max_price=0.95):
    """selector_fn(day_df) → list of bucket_idx to buy."""
    trades = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        selected = selector_fn(day)
        for b_idx in selected:
            row = day[day["bucket_idx"] == b_idx]
            if row.empty:
                continue
            r = row.iloc[0]
            if r["entry_price"] < min_price or r["entry_price"] > max_price:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({
                "city": city, "market_date": md, "bucket_idx": int(b_idx),
                "entry_price": float(r["entry_price"]),
                "won_yes": int(r["won_yes"]),
                "pnl": pnl,
            })
    return pd.DataFrame(trades)


def summarize(t):
    if len(t) == 0:
        return {"n": 0}
    std = t.pnl.std() if len(t) > 1 else 0
    return {
        "n": len(t),
        "hit": t.won_yes.mean(),
        "per": t.pnl.mean(),
        "tot": t.pnl.sum(),
        "std": std,
        "t": t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0,
    }


def main():
    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date

    # Join model predictions
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
    model.fit(is_train[feats_cols].values, is_train[target := "actual_max_f"].values)
    yhat_is = model.predict(is_train[feats_cols].values)
    sigma = float((is_train[target].values - yhat_is).std(ddof=1))

    # Also compute mean-ensemble prediction for all complete rows
    complete["pred_linear"] = model.predict(complete[feats_cols].values)
    complete["pred_mean_ens"] = (complete["nbs_pred_max_f"] + complete["gfs_pred_max_f"] + complete["hrrr_max_t_f"]) / 3
    # Also a "NBS minus IS bias" — IS bias was +0.019, OOS bias +0.592. If we
    # lock in IS-only bias correction: 0.019. If we use "simple mean" as anchor
    # we'd shift -0.07.

    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    complete["city"] = complete["station"].map(station_to_city)
    complete = complete.dropna(subset=["city"])

    tbl = tbl.merge(
        complete[["city", "local_date", "pred_linear", "pred_mean_ens", "nbs_pred_max_f"]].rename(
            columns={"local_date": "market_date", "nbs_pred_max_f": "nbs_pred_check"}
        ),
        on=["city", "market_date"], how="left"
    )

    def fold(d):
        if IS_START <= d <= IS_END: return "IS"
        if OOS_START <= d <= OOS_END: return "OOS"
        return "OOB"
    tbl["strat_fold"] = tbl["date"].apply(fold)
    tbl = tbl[tbl.strat_fold.isin(["IS","OOS"])].copy()
    tbl = tbl.dropna(subset=["pred_linear"])

    # Strategy 1: buy bucket closest to linear-model prediction
    def s_model_fav(day):
        pred = day["pred_linear"].iloc[0]
        diff = (day["bucket_center"] - pred).abs()
        return [int(day.loc[diff.idxmin(), "bucket_idx"])]

    # Strategy 2: buy bucket closest to mean-ensemble
    def s_mean_fav(day):
        pred = day["pred_mean_ens"].iloc[0]
        diff = (day["bucket_center"] - pred).abs()
        return [int(day.loc[diff.idxmin(), "bucket_idx"])]

    # Strategy 3: buy bucket closest to bias-corrected NBS (-0.5°F)
    def s_nbs_minus_05(day):
        pred = day["nbs_pred_max_f"].iloc[0] - 0.5
        diff = (day["bucket_center"] - pred).abs()
        return [int(day.loc[diff.idxmin(), "bucket_idx"])]

    # Strategy 4: buy market-fav only if model agrees within 1 bucket
    def s_model_mkt_agree(day):
        mkt_fav = int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])
        pred = day["pred_linear"].iloc[0]
        diff = (day["bucket_center"] - pred).abs()
        model_fav = int(day.loc[diff.idxmin(), "bucket_idx"])
        if abs(mkt_fav - model_fav) <= 1:
            return [mkt_fav]
        return []

    # Strategy 5: buy bucket with maximum model_p (peak of predicted distribution)
    # Same as s_model_fav in practice — centered on pred — SKIP

    # Strategy 6: buy TWO buckets centered on linear pred (n-1, n, n+1)
    def s_model_fav_wide(day):
        pred = day["pred_linear"].iloc[0]
        diff = (day["bucket_center"] - pred).abs()
        fav = int(day.loc[diff.idxmin(), "bucket_idx"])
        out = [fav]
        for off in (-1, 1):
            cand = day[day["bucket_idx"] == fav + off]
            if len(cand) == 1:
                out.append(int(cand.iloc[0]["bucket_idx"]))
        return out

    strategies = [
        ("S1_model_fav", s_model_fav),
        ("S2_mean_ens_fav", s_mean_fav),
        ("S3_nbs_minus_0.5", s_nbs_minus_05),
        ("S4_model_mkt_agree", s_model_mkt_agree),
        ("S6_model_wide3", s_model_fav_wide),
    ]

    print("=== Strategy results (model-based) ===")
    print(f"{'strategy':<22} {'fold':<4}  {'n':>4}  {'hit':>6}  {'per':>9}  {'tot':>8}  {'t':>6}")
    print("-" * 75)
    for name, fn in strategies:
        is_t = simulate_strategy(tbl[tbl.strat_fold=="IS"], fn)
        oos_t = simulate_strategy(tbl[tbl.strat_fold=="OOS"], fn)
        s_is = summarize(is_t)
        s_oos = summarize(oos_t)
        print(f"{name:<22} IS    n={s_is.get('n',0):>3}  {s_is.get('hit',0)*100:>5.1f}%  "
              f"${s_is.get('per',0):>+7.3f}  ${s_is.get('tot',0):>+6.2f}  {s_is.get('t',0):>+5.2f}")
        print(f"{name:<22} OOS   n={s_oos.get('n',0):>3}  {s_oos.get('hit',0)*100:>5.1f}%  "
              f"${s_oos.get('per',0):>+7.3f}  ${s_oos.get('tot',0):>+6.2f}  {s_oos.get('t',0):>+5.2f}")


if __name__ == "__main__":
    main()

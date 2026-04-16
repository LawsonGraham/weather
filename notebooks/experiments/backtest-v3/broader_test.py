"""Broader test — treat all Mar 11-Apr 10 as one big model-OOS period.

The per-station model was trained ONLY on Dec 1 - Feb 28. Everything after
Feb 28 is model-OOS. The previous iteration split strategy-IS / strategy-OOS
at Mar 31 - but that's an artifact of noting v2 boundaries, not a model
leakage boundary.

This script evaluates the `max model_p` strategy and the `bucket_gap=0`
filter on the FULL Mar 11 - Apr 10 period (n=180) to get a larger sample
and see if the filter effect is robust.

Also does a WITHIN-model-OOS split (Mar 11-24 IS, Mar 25-Apr 10 OOS) for
a cleaner 50/50 strategy-level holdout with more data.
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

    from sklearn.linear_model import Ridge
    per_st_models = {}
    for st, sub in complete.groupby("station"):
        tr = sub[sub.fold == "IS"]
        if len(tr) < 30: continue
        m = Ridge(alpha=5.0)
        m.fit(tr[FEATS].values, tr["actual_max_f"].values)
        yhat = m.predict(tr[FEATS].values)
        sigma = float((tr["actual_max_f"].values - yhat).std(ddof=1))
        per_st_models[st] = {"model": m, "sigma": sigma}

    complete["pred"] = np.nan
    complete["sigma"] = np.nan
    for st, info in per_st_models.items():
        mask = complete.station == st
        complete.loc[mask, "pred"] = info["model"].predict(complete.loc[mask, FEATS].values)
        complete.loc[mask, "sigma"] = info["sigma"]

    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)

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

    # Filter to Mar 11 - Apr 10
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    # Build daily selection
    daily = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        probs_sorted = day.sort_values("model_p", ascending=False)
        top1 = probs_sorted.iloc[0]
        top2_p = probs_sorted.iloc[1]["model_p"] if len(probs_sorted) > 1 else 0
        top_gap = top1["model_p"] - top2_p
        mkt_fav_idx = int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])
        mdl_fav_idx = int(top1["bucket_idx"])
        gap_to_mkt = abs(mdl_fav_idx - mkt_fav_idx)
        daily.append({
            "city": city, "market_date": md, "date": md.date(),
            "mdl_bucket": mdl_fav_idx, "mkt_bucket": mkt_fav_idx,
            "bucket_gap": gap_to_mkt,
            "top1_p": float(top1["model_p"]),
            "top_gap": float(top_gap),
            "mdl_price": float(top1["entry_price"]),
            "mkt_price": float(day.loc[day["entry_price"].idxmax(), "entry_price"]),
            "mdl_won": int(top1["won_yes"]),
            "mdl_pnl": (float(top1["won_yes"]) - top1["entry_price"]
                       - FEE * top1["entry_price"] * (1 - top1["entry_price"])),
            "mkt_won": int(day[day["bucket_idx"] == mkt_fav_idx]["won_yes"].iloc[0]),
            "mkt_pnl": (float(day[day["bucket_idx"] == mkt_fav_idx]["won_yes"].iloc[0])
                       - day.loc[day["entry_price"].idxmax(), "entry_price"]
                       - FEE * day.loc[day["entry_price"].idxmax(), "entry_price"]
                         * (1 - day.loc[day["entry_price"].idxmax(), "entry_price"])),
        })
    d = pd.DataFrame(daily)
    d = d[(d.mdl_price >= 0.02) & (d.mdl_price <= 0.95)]
    print(f"Total daily rows (Mar 11 - Apr 10): {len(d)}")

    def stats(sub, col):
        if len(sub) == 0:
            return None
        pnl = sub[col + "_pnl"]
        won = sub[col + "_won"]
        price = sub[col + "_price"]
        std = pnl.std() if len(pnl) > 1 else 0
        t = pnl.mean() / (std / len(pnl)**0.5) if std > 0 else 0
        return {
            "n": len(sub), "hit": won.mean(), "per": pnl.mean(),
            "tot": pnl.sum(), "t": t, "price": price.mean(),
        }

    def show(sub, name):
        for col in ("mdl", "mkt"):
            s = stats(sub, col)
            if s is None:
                print(f"  {name:<25}  {col}: n=0")
                continue
            print(f"  {name:<25}  {col}: n={s['n']:>3}  hit={s['hit']*100:>5.1f}%  "
                  f"per=${s['per']:>+.3f}  tot=${s['tot']:>+.2f}  t={s['t']:>+.2f}  "
                  f"price=${s['price']:.3f}")

    # Full 31-day OOS
    print()
    print("=== Full Mar 11 - Apr 10 (model OOS period, no strategy split) ===")
    show(d, "all")
    show(d[d.bucket_gap == 0], "bucket_gap=0")
    show(d[d.bucket_gap == 1], "bucket_gap=1")
    show(d[d.bucket_gap >= 2], "bucket_gap>=2")
    show(d[d.top_gap > 0.02], "top_gap>0.02")
    show(d[d.top_gap > 0.05], "top_gap>0.05")
    show(d[(d.top_gap > 0.05) & (d.bucket_gap == 0)], "top_gap>0.05 & gap=0")

    # 50/50 within-OOS split: Mar 11-25 vs Mar 26-Apr 10
    split = date(2026, 3, 25)
    d1 = d[d.date <= split]
    d2 = d[d.date > split]
    print()
    print(f"=== Within-OOS split: Mar 11-25 vs Mar 26-Apr 10 ===")
    print(f"--- FIRST HALF (n={len(d1)}) ---")
    show(d1, "all")
    show(d1[d1.bucket_gap == 0], "bucket_gap=0")
    show(d1[d1.top_gap > 0.02], "top_gap>0.02")
    print(f"--- SECOND HALF (n={len(d2)}) ---")
    show(d2, "all")
    show(d2[d2.bucket_gap == 0], "bucket_gap=0")
    show(d2[d2.top_gap > 0.02], "top_gap>0.02")

    # Per-week breakdown
    d["week"] = pd.to_datetime(d.market_date).dt.isocalendar().week
    print()
    print("=== Week-by-week (bucket_gap=0 vs all) ===")
    for wk in sorted(d.week.unique()):
        wk_sub = d[d.week == wk]
        wk_g0 = wk_sub[wk_sub.bucket_gap == 0]
        print(f"  Wk{wk}: all n={len(wk_sub)}  mkt_fav_hit={wk_sub.mkt_won.mean()*100:>5.1f}%  "
              f"mkt_per=${wk_sub.mkt_pnl.mean():>+.3f} | "
              f"gap=0 n={len(wk_g0)}  hit={wk_g0.mkt_won.mean()*100 if len(wk_g0) else 0:>5.1f}%  "
              f"per=${wk_g0.mkt_pnl.mean() if len(wk_g0) else 0:>+.3f}")

    # Focus on bucket_gap=0 variant: what's the model doing here?
    g0 = d[d.bucket_gap == 0].copy()
    g0["is_later"] = g0.date > date(2026, 3, 25)
    print()
    print("=== bucket_gap=0 cumulative PnL ===")
    g0 = g0.sort_values("date").reset_index(drop=True)
    g0["cum_pnl_mkt"] = g0["mkt_pnl"].cumsum()
    g0["cum_pnl_mdl"] = g0["mdl_pnl"].cumsum()
    for i, r in g0.iterrows():
        if i % 3 == 0 or i == len(g0) - 1:
            print(f"  {r.date}  city={r.city[:10]:<10}  "
                  f"mkt_won={r.mkt_won}  mkt_pnl=${r.mkt_pnl:+.3f}  "
                  f"cum=${r.cum_pnl_mkt:+.2f}")


if __name__ == "__main__":
    main()

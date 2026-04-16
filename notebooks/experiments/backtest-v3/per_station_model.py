"""Per-station ensemble models.

For each station, train an independent Ridge regression on IS (Dec 1 -
Feb 28). Use each model to predict on OOS, and specifically on the
market overlap period (Mar 11+).

Then examine whether stations with large MAE improvements (LGA +2.2°F,
LAX +1.5°F) also show trading edge vs market.
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
TARGET = "actual_max_f"


def bucket_prob(pred, sigma, lo, hi):
    p_hi = 1.0 if hi == float("inf") else norm.cdf(hi + 0.5, pred, sigma)
    p_lo = 0.0 if lo == float("-inf") else norm.cdf(lo - 0.5, pred, sigma)
    return max(0.0, p_hi - p_lo)


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    # Don't use nbs_spread (often missing) for per-station to keep feature count small
    complete = feat.dropna(subset=FEATS + [TARGET]).copy()

    from sklearn.linear_model import Ridge

    print("=== Per-station OOS MAE vs global ensemble ===")
    print(f"{'station':<8} {'n_is':>5} {'n_oos':>5}  {'MAE_nbs':>8}  {'MAE_global':>11}  {'MAE_perstation':>14}  {'IS_sigma':>9}")

    per_station = {}
    for st, sub in complete.groupby("station"):
        is_tr = sub[sub.fold == "IS"]
        oos = sub[sub.fold == "OOS"]
        if len(is_tr) < 30 or len(oos) < 10:
            continue
        # Per-station model
        mdl = Ridge(alpha=5.0)
        mdl.fit(is_tr[FEATS].values, is_tr[TARGET].values)
        yhat_oos = mdl.predict(oos[FEATS].values)
        yhat_is = mdl.predict(is_tr[FEATS].values)
        sigma = float((is_tr[TARGET].values - yhat_is).std(ddof=1))
        mae_per = float(np.mean(np.abs(oos[TARGET].values - yhat_oos)))
        mae_nbs = float(np.mean(np.abs(oos[TARGET].values - oos["nbs_pred_max_f"].values)))
        # Global model (trained on all IS)
        global_tr = complete[complete.fold == "IS"]
        gmdl = Ridge(alpha=5.0)
        gmdl.fit(global_tr[FEATS].values, global_tr[TARGET].values)
        yhat_global = gmdl.predict(oos[FEATS].values)
        mae_global = float(np.mean(np.abs(oos[TARGET].values - yhat_global)))

        per_station[st] = {
            "model": mdl, "sigma": sigma,
            "mae_nbs": mae_nbs, "mae_global": mae_global, "mae_per_station": mae_per,
        }
        print(f"{st:<8} {len(is_tr):>5} {len(oos):>5}  {mae_nbs:>8.3f}  "
              f"{mae_global:>11.3f}  {mae_per:>14.3f}  {sigma:>9.3f}")

    # Now use per-station predictions to trade
    # Build a predictions dict: (station, local_date) → (pred, sigma)
    preds = {}
    for st, info in per_station.items():
        sub = complete[complete.station == st].copy()
        sub["pred"] = info["model"].predict(sub[FEATS].values)
        sub["sigma"] = info["sigma"]
        for _, r in sub.iterrows():
            preds[(st, r["local_date"])] = (r["pred"], info["sigma"])

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)

    # Attach per-station prediction
    tbl["ps_pred"] = [preds.get((s, d), (None, None))[0]
                     for s, d in zip(tbl["station"], tbl["market_date"])]
    tbl["ps_sigma"] = [preds.get((s, d), (None, None))[1]
                      for s, d in zip(tbl["station"], tbl["market_date"])]
    tbl = tbl.dropna(subset=["ps_pred"])

    # Compute bucket probability
    tbl["model_p"] = tbl.apply(
        lambda r: bucket_prob(r["ps_pred"], r["ps_sigma"], r["bucket_low"], r["bucket_high"]),
        axis=1
    )
    tbl["edge"] = tbl["model_p"] - tbl["entry_price"]

    def fold(d):
        if IS_START <= d <= IS_END: return "IS"
        if OOS_START <= d <= OOS_END: return "OOS"
        return "OOB"
    tbl["strat_fold"] = tbl["date"].apply(fold)
    tbl = tbl[tbl.strat_fold.isin(["IS", "OOS"])].copy()

    # Strategy: buy bucket with highest model_p
    print()
    print("=== Per-station model: buy bucket with max model_p ===")
    def run(df, name):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            idx = day["model_p"].idxmax()
            r = day.loc[idx]
            if r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({
                "city": city, "md": md,
                "bucket_idx": int(r["bucket_idx"]),
                "price": float(r["entry_price"]),
                "model_p": float(r["model_p"]),
                "edge": float(r["edge"]),
                "won_yes": int(r["won_yes"]),
                "pnl": pnl,
            })
        t = pd.DataFrame(trades)
        if len(t) == 0:
            return None, None
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(t)}  hit={t.won_yes.mean()*100:.1f}%  "
              f"per=${t.pnl.mean():+.4f}  tot=${t.pnl.sum():+.2f}  t={tstat:+.2f}")
        return t, tstat

    run(tbl[tbl.strat_fold=="IS"], "IS  max_model_p")
    run(tbl[tbl.strat_fold=="OOS"], "OOS max_model_p")

    # Bet where edge > threshold
    print()
    print("=== Edge threshold sweep (per-station model) ===")
    for th in (0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25):
        for f in ("IS", "OOS"):
            bets = tbl[(tbl.strat_fold==f) & (tbl.edge > th)
                       & (tbl.entry_price >= 0.02) & (tbl.entry_price <= 0.95)].copy()
            if len(bets) == 0:
                print(f"  th={th:.2f}  {f:<3}  n=0")
                continue
            bets["fee"] = FEE * bets.entry_price * (1 - bets.entry_price)
            bets["pnl"] = bets.won_yes - bets.entry_price - bets.fee
            std = bets.pnl.std() if len(bets) > 1 else 0
            tstat = bets.pnl.mean() / (std / len(bets)**0.5) if std > 0 else 0
            print(f"  th={th:.2f}  {f:<3}  n={len(bets):>4}  hit={bets.won_yes.mean()*100:>5.1f}%  "
                  f"per=${bets.pnl.mean():>+7.3f}  tot=${bets.pnl.sum():>+6.2f}  t={tstat:>+5.2f}")

    # Per-city at edge > 0
    print()
    print("=== Per-city OOS — buy bucket with max model_p ===")
    oos_trades = []
    for (city, md), grp in tbl[tbl.strat_fold=="OOS"].groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        idx = day["model_p"].idxmax()
        r = day.loc[idx]
        if r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
            continue
        fee = FEE * r["entry_price"] * (1 - r["entry_price"])
        pnl = float(r["won_yes"]) - r["entry_price"] - fee
        oos_trades.append({"city": city, "pnl": pnl, "hit": int(r["won_yes"])})
    ot = pd.DataFrame(oos_trades)
    for city, g in ot.groupby("city"):
        print(f"  {city:<18} n={len(g):>3}  hit={g.hit.mean()*100:>5.1f}%  "
              f"per=${g.pnl.mean():>+6.3f}  tot=${g.pnl.sum():>+6.2f}")


if __name__ == "__main__":
    main()

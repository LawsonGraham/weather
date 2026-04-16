"""Predict SIGN of NBS error: will actual > NBS or actual < NBS?

If classifiable with ≥55% accuracy, we know which side of NBS to bet on.

Target: sign(actual - nbs_pred). Binary classification.
Features: same as regression.
Evaluate: accuracy on OOS, and trading strategy.

Strategy if sign is predictable:
- If predicted sign positive: buy NBS_fav + 1 bucket (shift up)
- If predicted sign negative: buy NBS_fav - 1 bucket (shift down)
- If uncertain: buy NBS_fav (bucket)

Evaluate on full Mar 11 - Apr 10 (model-OOS).
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
FEATS = ["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
         "yesterday_max_f", "tmp_noon_f", "tmp_morning_f",
         "nbs_minus_gfs", "hrrr_minus_nbs", "day_of_year"]


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    complete = feat.dropna(subset=FEATS + ["actual_max_f"]).copy()

    # Target: sign of NBS residual
    complete["nbs_err"] = complete["actual_max_f"] - complete["nbs_pred_max_f"]
    complete["sign_nbs_err"] = (complete["nbs_err"] > 0).astype(int)

    is_tr = complete[complete.fold == "IS"]
    oos = complete[complete.fold == "OOS"]
    print(f"IS n={len(is_tr)}, OOS n={len(oos)}")
    print(f"IS P(actual > NBS) = {is_tr.sign_nbs_err.mean():.3f}")
    print(f"OOS P(actual > NBS) = {oos.sign_nbs_err.mean():.3f}")

    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import GradientBoostingClassifier

    for mdl_name, mdl in [("LogReg", LogisticRegression(max_iter=1000, C=0.5)),
                          ("GBC", GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05))]:
        mdl.fit(is_tr[FEATS].values, is_tr["sign_nbs_err"].values)
        yhat_proba_is = mdl.predict_proba(is_tr[FEATS].values)[:, 1]
        yhat_proba_oos = mdl.predict_proba(oos[FEATS].values)[:, 1]
        yhat_class_is = (yhat_proba_is > 0.5).astype(int)
        yhat_class_oos = (yhat_proba_oos > 0.5).astype(int)
        acc_is = (yhat_class_is == is_tr["sign_nbs_err"].values).mean()
        acc_oos = (yhat_class_oos == oos["sign_nbs_err"].values).mean()
        print(f"\n{mdl_name}: IS acc={acc_is:.3f}, OOS acc={acc_oos:.3f}")

        # Confidence thresholds
        for th in (0.5, 0.55, 0.60, 0.65):
            mask_hi = yhat_proba_oos > th
            mask_lo = yhat_proba_oos < (1 - th)
            mask = mask_hi | mask_lo
            if mask.sum() > 0:
                sub_oos = oos[mask]
                pred_sub = yhat_class_oos[mask]
                acc = (pred_sub == sub_oos["sign_nbs_err"].values).mean()
                print(f"  |p|>{th}: n={mask.sum()}, acc={acc:.3f}")

    # Now trade using GBC
    mdl = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05)
    mdl.fit(is_tr[FEATS].values, is_tr["sign_nbs_err"].values)
    complete["p_above_nbs"] = mdl.predict_proba(complete[FEATS].values)[:, 1]

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)

    tbl = tbl.merge(
        complete[["station", "local_date", "p_above_nbs", "nbs_pred_max_f"]].rename(
            columns={"local_date": "market_date", "nbs_pred_max_f": "nbs_pred_v3"}),
        on=["station", "market_date"], how="left"
    )
    tbl = tbl.dropna(subset=["p_above_nbs"])
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    # For each (city, market_date), select bet based on sign prediction
    def run_strategy(df, threshold, name):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            # Which bucket is NBS favorite?
            nbs_pred = day["nbs_pred_v3"].iloc[0]
            diff = (day["bucket_center"] - nbs_pred).abs()
            nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
            p = day["p_above_nbs"].iloc[0]
            # Strategy: bet the SHIFTED bucket based on sign prob
            if p > threshold:
                target = nbs_fav_idx + 1  # shift up
            elif p < (1 - threshold):
                target = nbs_fav_idx - 1  # shift down
            else:
                continue  # no bet
            row = day[day["bucket_idx"] == target]
            if row.empty:
                continue
            r = row.iloc[0]
            if r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({
                "city": city, "market_date": md,
                "direction": "up" if p > threshold else "down",
                "p_above_nbs": p,
                "bucket_idx": target,
                "price": float(r["entry_price"]),
                "won_yes": int(r["won_yes"]),
                "pnl": pnl,
            })
        t = pd.DataFrame(trades)
        if len(t) == 0:
            print(f"  {name}: no trades")
            return t
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(t)}  hit={t.won_yes.mean()*100:.1f}%  "
              f"per=${t.pnl.mean():+.3f}  tot=${t.pnl.sum():+.2f}  t={tstat:+.2f}  "
              f"avg_price=${t.price.mean():.3f}")
        # Direction breakdown
        for dir in ("up", "down"):
            sub = t[t.direction == dir]
            if len(sub) > 0:
                print(f"    {dir}: n={len(sub)}  hit={sub.won_yes.mean()*100:.1f}%  "
                      f"per=${sub.pnl.mean():+.3f}")
        return t

    print("\n=== Sign-based strategy (shift from NBS fav) ===")
    for th in (0.5, 0.55, 0.60, 0.65):
        print(f"  threshold={th}")
        run_strategy(tbl, th, f"    all (th={th})")

    # Within-OOS split
    print("\n=== Sign-based strategy split: Mar 11-25 vs Mar 26-Apr 10 ===")
    for th in (0.5, 0.55, 0.60):
        print(f"  threshold={th}")
        run_strategy(tbl[tbl.date <= date(2026, 3, 25)], th, f"    Mar 11-25 (th={th})")
        run_strategy(tbl[tbl.date > date(2026, 3, 25)], th, f"    Mar 26-Apr 10 (th={th})")


if __name__ == "__main__":
    main()

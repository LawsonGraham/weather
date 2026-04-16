"""Direct bucket-classification approach.

Instead of regressing on max temp → converting to buckets via Normal CDF,
train a direct multi-class classifier: "which 2°F window will the max
fall into?"

Challenge: bucket labels vary per market-day (each day has its own
threshold). So the target must be normalized — e.g., bucket relative
to NBS favorite (offset in °F). Then at test time, for each market-day,
apply the learned probability distribution centered at that day's NBS
favorite.

This captures the MARKET-relevant distribution directly without
assuming Normal residuals.
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
IS_START = date(2026, 3, 11)
IS_END = date(2026, 3, 31)
OOS_START = date(2026, 4, 1)
OOS_END = date(2026, 4, 10)
FEE = 0.05

FEATS = ["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
         "yesterday_max_f", "tmp_noon_f", "tmp_morning_f",
         "nbs_minus_gfs", "hrrr_minus_nbs", "day_of_year"]


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    complete = feat.dropna(subset=FEATS + ["actual_max_f"]).copy()

    # Target: actual_max - nbs_pred (bias of NBS, in °F)
    # This normalizes across cities — the target is always "how much did
    # actual differ from NBS prediction"
    complete["resid"] = complete["actual_max_f"] - complete["nbs_pred_max_f"]

    # Empirical distribution of residuals IS — can use as base
    is_tr = complete[complete.fold == "IS"]
    oos = complete[complete.fold == "OOS"]

    print(f"IS residuals distribution (actual - NBS):")
    print(is_tr["resid"].describe())
    print()
    print(f"OOS residuals distribution:")
    print(oos["resid"].describe())

    # Train a model to predict the residual (actual - NBS)
    from sklearn.linear_model import Ridge
    mdl = Ridge(alpha=5.0)
    mdl.fit(is_tr[FEATS].values, is_tr["resid"].values)
    yhat_is = mdl.predict(is_tr[FEATS].values)
    yhat_oos = mdl.predict(oos[FEATS].values)

    # Model's final prediction = NBS + predicted_residual
    pred_is = is_tr["nbs_pred_max_f"].values + yhat_is
    pred_oos = oos["nbs_pred_max_f"].values + yhat_oos

    mae_oos_mdl = float(np.mean(np.abs(oos["actual_max_f"].values - pred_oos)))
    mae_oos_nbs = float(np.mean(np.abs(oos["actual_max_f"].values - oos["nbs_pred_max_f"].values)))
    print(f"NBS alone OOS MAE: {mae_oos_nbs:.3f}")
    print(f"NBS + predicted residual OOS MAE: {mae_oos_mdl:.3f}")

    # Residuals from the residual-prediction (i.e., the remaining error)
    resid_after = is_tr["actual_max_f"].values - pred_is
    sigma_after = float(resid_after.std(ddof=1))
    print(f"IS sigma after residual correction: {sigma_after:.3f}")
    print(f"OOS sigma after correction: {(oos['actual_max_f'].values - pred_oos).std(ddof=1):.3f}")

    # Empirical bucket probability (histogram approach):
    # For each IS sample, compute residual from the model.
    # The EMPIRICAL residual distribution gives bucket probabilities.
    is_residuals = is_tr["actual_max_f"].values - pred_is
    print(f"\nEmpirical residuals (n={len(is_residuals)}): "
          f"mean={is_residuals.mean():.3f}, "
          f"std={is_residuals.std():.3f}")

    # Percentile check
    print(f"P05={np.percentile(is_residuals, 5):.2f}, "
          f"P25={np.percentile(is_residuals, 25):.2f}, "
          f"P50={np.percentile(is_residuals, 50):.2f}, "
          f"P75={np.percentile(is_residuals, 75):.2f}, "
          f"P95={np.percentile(is_residuals, 95):.2f}")

    # Now trade using the empirical residual distribution (histogram, not Gaussian)
    def empirical_bucket_prob(pred, lo, hi, residuals):
        """P(max ∈ [lo,hi]) based on empirical distribution of (actual - pred).

        For each historical residual r, check if (pred + r) falls in [lo, hi].
        Return fraction.
        """
        mc = pred + residuals  # Monte-Carlo-like sample of possible actual maxes
        mask = (mc >= lo - 0.5) & (mc <= hi + 0.5)
        return float(mask.mean())

    # Load trade table + join
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)

    # Attach residual-model prediction
    # Use pre-computed model on complete dataset
    complete["pred_full"] = complete["nbs_pred_max_f"] + mdl.predict(complete[FEATS].values)
    tbl = tbl.merge(
        complete[["station", "local_date", "pred_full"]].rename(
            columns={"local_date": "market_date"}),
        on=["station", "market_date"], how="left"
    )
    tbl = tbl.dropna(subset=["pred_full"])

    def fold(d):
        if IS_START <= d <= IS_END: return "IS"
        if OOS_START <= d <= OOS_END: return "OOS"
        return "OOB"
    tbl["strat_fold"] = tbl["date"].apply(fold)
    tbl = tbl[tbl.strat_fold.isin(["IS", "OOS"])].copy()

    # Apply empirical bucket probability
    print("\nComputing empirical bucket probabilities...")
    tbl["model_p_emp"] = [
        empirical_bucket_prob(p, lo, hi, is_residuals)
        for p, lo, hi in zip(tbl["pred_full"], tbl["bucket_low"], tbl["bucket_high"])
    ]
    tbl["edge_emp"] = tbl["model_p_emp"] - tbl["entry_price"]

    # Strategy: buy bucket with max model_p
    def run(df, name):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            idx = day["model_p_emp"].idxmax()
            r = day.loc[idx]
            if r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({"city": city, "pnl": pnl, "won": int(r["won_yes"]),
                          "price": float(r["entry_price"]),
                          "model_p": float(r["model_p_emp"])})
        t = pd.DataFrame(trades)
        if len(t) == 0:
            print(f"  {name}: no trades")
            return None
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(t)}  hit={t.won.mean()*100:.1f}%  "
              f"per=${t.pnl.mean():+.4f}  tot=${t.pnl.sum():+.2f}  t={tstat:+.2f}  "
              f"avg_price=${t.price.mean():.3f}")
        return t

    print("\n=== Empirical residual model: buy max model_p ===")
    is_tr = run(tbl[tbl.strat_fold=="IS"], "IS")
    oos_tr = run(tbl[tbl.strat_fold=="OOS"], "OOS")

    # Edge threshold sweep
    print("\n=== Edge threshold sweep ===")
    for th in (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
        for f in ("IS", "OOS"):
            bets = tbl[(tbl.strat_fold==f) & (tbl.edge_emp > th)
                       & (tbl.entry_price >= 0.02) & (tbl.entry_price <= 0.95)].copy()
            if len(bets) == 0:
                continue
            bets["fee"] = FEE * bets.entry_price * (1 - bets.entry_price)
            bets["pnl"] = bets.won_yes - bets.entry_price - bets.fee
            std = bets.pnl.std() if len(bets) > 1 else 0
            tstat = bets.pnl.mean() / (std / len(bets)**0.5) if std > 0 else 0
            print(f"  th={th:.2f}  {f:<3}  n={len(bets):>4}  hit={bets.won_yes.mean()*100:>5.1f}%  "
                  f"per=${bets.pnl.mean():>+7.3f}  tot=${bets.pnl.sum():>+6.2f}  t={tstat:>+5.2f}")

    # Per-city OOS
    if oos_tr is not None:
        print("\n=== Per-city OOS ===")
        for city, g in oos_tr.groupby("city"):
            print(f"  {city:<18} n={len(g):>3}  hit={g.won.mean()*100:>5.1f}%  "
                  f"per=${g.pnl.mean():>+6.3f}  tot=${g.pnl.sum():>+6.2f}")


if __name__ == "__main__":
    main()

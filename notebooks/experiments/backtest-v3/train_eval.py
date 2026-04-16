"""Train ensemble of forecasts → daily max regression.

Baselines:
- M0: NBS alone (best single forecast)
- M1: GFS alone
- M2: HRRR alone
- M3: Simple mean of NBS + GFS + HRRR
- M4: Linear regression (trained on IS)
- M5: LightGBM (trained on IS)

Evaluation:
- MAE, RMSE per model on OOS
- Per-station breakdown
- Calibrated residual std for bucket probability

Then: compute bucket probabilities via Normal CDF and compare to market.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
IN = REPO / "data" / "processed" / "backtest_v3" / "features.parquet"
OUT = REPO / "data" / "processed" / "backtest_v3"


def mae(y, yhat):
    return np.mean(np.abs(y - yhat))


def rmse(y, yhat):
    return float(np.sqrt(np.mean((y - yhat) ** 2)))


def main():
    print("Loading features...")
    df = pd.read_parquet(IN)
    # Filter to complete rows
    feats = ["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
             "yesterday_max_f", "tmp_noon_f", "tmp_morning_f",
             "nbs_spread_f", "nbs_minus_gfs", "hrrr_minus_nbs",
             "day_of_year", "month"]
    target = "actual_max_f"

    complete = df.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f",
                                 "hrrr_max_t_f", target,
                                 "yesterday_max_f", "tmp_noon_f", "tmp_morning_f"])
    # nbs_spread may be missing for some rows — impute with median
    complete = complete.copy()
    complete["nbs_spread_f"] = complete["nbs_spread_f"].fillna(complete["nbs_spread_f"].median())

    is_df = complete[complete.fold == "IS"]
    oos_df = complete[complete.fold == "OOS"]
    print(f"IS: {len(is_df)}, OOS: {len(oos_df)}")
    print(f"IS stations: {sorted(is_df.station.unique())}")
    print(f"OOS stations: {sorted(oos_df.station.unique())}")

    X_is = is_df[feats].values
    y_is = is_df[target].values
    X_oos = oos_df[feats].values
    y_oos = oos_df[target].values

    print("\n" + "="*70)
    print("OOS MAE / RMSE / BIAS per model")
    print("="*70)

    results = []

    # Baselines
    for name, col in [("M0_NBS", "nbs_pred_max_f"),
                      ("M1_GFS", "gfs_pred_max_f"),
                      ("M2_HRRR", "hrrr_max_t_f")]:
        yhat = oos_df[col].values
        r = {
            "model": name,
            "mae_oos": mae(y_oos, yhat),
            "rmse_oos": rmse(y_oos, yhat),
            "bias_oos": (yhat - y_oos).mean(),
            "mae_is": mae(y_is, is_df[col].values),
            "bias_is": (is_df[col].values - y_is).mean(),
        }
        results.append(r)

    # Simple mean ensemble
    mean_is = (is_df["nbs_pred_max_f"] + is_df["gfs_pred_max_f"] + is_df["hrrr_max_t_f"]) / 3
    mean_oos = (oos_df["nbs_pred_max_f"] + oos_df["gfs_pred_max_f"] + oos_df["hrrr_max_t_f"]) / 3
    results.append({
        "model": "M3_mean_ens",
        "mae_oos": mae(y_oos, mean_oos.values),
        "rmse_oos": rmse(y_oos, mean_oos.values),
        "bias_oos": (mean_oos.values - y_oos).mean(),
        "mae_is": mae(y_is, mean_is.values),
        "bias_is": (mean_is.values - y_is).mean(),
    })

    # Linear regression
    from sklearn.linear_model import LinearRegression, Ridge
    lr = LinearRegression()
    lr.fit(X_is, y_is)
    yhat_oos_lr = lr.predict(X_oos)
    yhat_is_lr = lr.predict(X_is)
    results.append({
        "model": "M4_linear",
        "mae_oos": mae(y_oos, yhat_oos_lr),
        "rmse_oos": rmse(y_oos, yhat_oos_lr),
        "bias_oos": (yhat_oos_lr - y_oos).mean(),
        "mae_is": mae(y_is, yhat_is_lr),
        "bias_is": (yhat_is_lr - y_is).mean(),
    })

    # Ridge (L2 reg)
    ridge = Ridge(alpha=5.0)
    ridge.fit(X_is, y_is)
    yhat_oos_ridge = ridge.predict(X_oos)
    yhat_is_ridge = ridge.predict(X_is)
    results.append({
        "model": "M4b_ridge",
        "mae_oos": mae(y_oos, yhat_oos_ridge),
        "rmse_oos": rmse(y_oos, yhat_oos_ridge),
        "bias_oos": (yhat_oos_ridge - y_oos).mean(),
        "mae_is": mae(y_is, yhat_is_ridge),
        "bias_is": (yhat_is_ridge - y_is).mean(),
    })

    # LightGBM
    import lightgbm as lgb
    lgb_model = lgb.LGBMRegressor(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=4,
        min_data_in_leaf=20,
        num_leaves=15,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )
    # Split IS into training/early-stop: last 10% of IS by date
    is_sorted = is_df.sort_values("local_date")
    split_idx = int(len(is_sorted) * 0.9)
    tr = is_sorted.iloc[:split_idx]
    va = is_sorted.iloc[split_idx:]
    lgb_model.fit(
        tr[feats].values, tr[target].values,
        eval_set=[(va[feats].values, va[target].values)],
        callbacks=[lgb.early_stopping(stopping_rounds=50)],
    )
    yhat_oos_lgb = lgb_model.predict(X_oos)
    yhat_is_lgb = lgb_model.predict(X_is)
    results.append({
        "model": "M5_lgbm",
        "mae_oos": mae(y_oos, yhat_oos_lgb),
        "rmse_oos": rmse(y_oos, yhat_oos_lgb),
        "bias_oos": (yhat_oos_lgb - y_oos).mean(),
        "mae_is": mae(y_is, yhat_is_lgb),
        "bias_is": (yhat_is_lgb - y_is).mean(),
    })

    print(f"{'model':<14}  {'IS_MAE':>7}  {'OOS_MAE':>7}  {'OOS_RMSE':>9}  "
          f"{'IS_bias':>8}  {'OOS_bias':>8}")
    print("-"*66)
    for r in results:
        print(f"{r['model']:<14}  {r['mae_is']:>7.3f}  {r['mae_oos']:>7.3f}  "
              f"{r['rmse_oos']:>9.3f}  {r['bias_is']:>+7.3f}  {r['bias_oos']:>+7.3f}")

    # Per-station OOS breakdown for best model
    best = min(results, key=lambda r: r["mae_oos"])
    print(f"\n\nBest OOS model: {best['model']} (MAE={best['mae_oos']:.3f})")

    # Rebuild predictions dataframe for the best model for deeper analysis
    # Using LGBM as the reference rich model for per-station + uncertainty
    print("\n=== Per-station OOS MAE (NBS vs LGBM) ===")
    oos_df_copy = oos_df.copy()
    oos_df_copy["nbs_pred"] = oos_df["nbs_pred_max_f"]
    oos_df_copy["lgbm_pred"] = yhat_oos_lgb
    for st, g in oos_df_copy.groupby("station"):
        m_nbs = mae(g[target].values, g["nbs_pred"].values)
        m_lgb = mae(g[target].values, g["lgbm_pred"].values)
        delta = m_nbs - m_lgb
        print(f"  {st}: n={len(g)}  NBS_MAE={m_nbs:.3f}  LGBM_MAE={m_lgb:.3f}  Δ={delta:+.3f}")

    # Calibrated residual std per model
    # For LGBM, compute residual std on IS (not OOS) for bucket probabilities
    resid_is = y_is - yhat_is_lgb
    resid_std_is = resid_is.std(ddof=1)
    print(f"\nLGBM IS residual std: {resid_std_is:.3f}")
    resid_oos = y_oos - yhat_oos_lgb
    resid_std_oos = resid_oos.std(ddof=1)
    print(f"LGBM OOS residual std: {resid_std_oos:.3f}")

    # Feature importance
    print("\n=== LGBM Feature Importance ===")
    imp = sorted(zip(feats, lgb_model.feature_importances_),
                 key=lambda x: -x[1])
    for name, v in imp:
        print(f"  {name:<22}  {v}")

    # Save predictions
    oos_preds = oos_df[["station", "local_date", target, "nbs_pred_max_f",
                        "gfs_pred_max_f", "hrrr_max_t_f", "nbs_spread_f"]].copy()
    oos_preds["pred_lgbm"] = yhat_oos_lgb
    oos_preds["pred_linear"] = yhat_oos_lr
    oos_preds["pred_ridge"] = yhat_oos_ridge
    oos_preds["pred_mean_ens"] = mean_oos.values
    oos_preds["resid_std_is"] = resid_std_is
    oos_preds.to_parquet(OUT / "oos_predictions.parquet", index=False)

    pd.DataFrame(results).to_csv(OUT / "model_comparison.csv", index=False)
    print(f"\nWrote predictions to {OUT / 'oos_predictions.parquet'}")


if __name__ == "__main__":
    main()

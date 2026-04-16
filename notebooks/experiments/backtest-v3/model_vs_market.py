"""Model-vs-market strategy using the ensemble (M4 linear) predictions.

For each (city, market_date) in OOS overlap with prices_history (Mar 11+):
1. Load the model prediction + IS residual std
2. Compute bucket probabilities via Normal CDF
3. Compare to market price at entry hour (20 UTC)
4. Bet where model_p - market_p > threshold AND entry_price > 0.02

Thresholds tested on IS-extending (Mar 11-31 since prices start then),
then locked for rest of OOS (Apr 1-10). Since the model IS/OOS split was
Feb 28, Mar 11-31 is MODEL-OOS — a second holdout.

Output: trades.parquet + summary.
"""
from __future__ import annotations

from pathlib import Path
import re
from datetime import date, datetime, timedelta, UTC

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import norm

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"

# Same IS used in v2 backtest for strategy tuning; reuse table structure
CITY_TO_STATION = {
    "New York City": "LGA",
    "Atlanta": "ATL",
    "Dallas": "DAL",
    "Seattle": "SEA",
    "Chicago": "ORD",
    "Miami": "MIA",
    "Austin": "AUS",
    "Houston": "HOU",
    "Denver": "DEN",
    "Los Angeles": "LAX",
    "San Francisco": "SFO",
}

STRATEGY_IS_START = date(2026, 3, 11)
STRATEGY_IS_END = date(2026, 3, 31)
STRATEGY_OOS_START = date(2026, 4, 1)
STRATEGY_OOS_END = date(2026, 4, 10)
ENTRY_HOUR_UTC = 20
FEE_RATE = 0.05


def parse_bucket(title: str) -> tuple[float, float, float]:
    RE_RANGE = re.compile(r"^(\d+)-(\d+)°F$")
    RE_BELOW = re.compile(r"^(\d+)°F or below$")
    RE_ABOVE = re.compile(r"^(\d+)°F or higher$")
    m = RE_RANGE.match(title)
    if m:
        lo, hi = int(m[1]), int(m[2])
        return (float(lo), float(hi), (lo + hi) / 2.0)
    m = RE_BELOW.match(title)
    if m:
        hi = int(m[1])
        return (float("-inf"), float(hi), float(hi - 1))
    m = RE_ABOVE.match(title)
    if m:
        lo = int(m[1])
        return (float(lo), float("inf"), float(lo + 1))
    raise ValueError(f"bad bucket: {title}")


def bucket_prob(pred: float, sigma: float, lo: float, hi: float) -> float:
    """P(daily_max ∈ [lo, hi]) given pred, sigma from Normal distribution."""
    p_hi = 1.0 if hi == float("inf") else norm.cdf(hi + 0.5, pred, sigma)
    p_lo = 0.0 if lo == float("-inf") else norm.cdf(lo - 0.5, pred, sigma)
    return max(0.0, p_hi - p_lo)


def main():
    print("Loading v2 trade table...")
    tbl = pd.read_parquet(REPO / "data" / "processed" / "backtest_v2" / "trade_table.parquet")
    # Filter to complete and resolved
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0].copy()

    # tbl has market_date as Timestamp; ensure we can filter
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])

    # Now we need station + model prediction + IS residual_std for each market_date
    print("Loading features + retraining M4_linear to get predictions on all OOS dates...")
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])

    # Train M4_linear from features
    feats_cols = ["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
                  "yesterday_max_f", "tmp_noon_f", "tmp_morning_f",
                  "nbs_spread_f", "nbs_minus_gfs", "hrrr_minus_nbs",
                  "day_of_year", "month"]
    target = "actual_max_f"
    complete = feat.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
                                   target, "yesterday_max_f", "tmp_noon_f", "tmp_morning_f"]).copy()
    complete["nbs_spread_f"] = complete["nbs_spread_f"].fillna(complete["nbs_spread_f"].median())
    is_train = complete[complete.fold == "IS"]

    from sklearn.linear_model import Ridge
    model = Ridge(alpha=5.0)
    model.fit(is_train[feats_cols].values, is_train[target].values)
    # Residual std on IS
    yhat_is = model.predict(is_train[feats_cols].values)
    sigma_is = float((is_train[target].values - yhat_is).std(ddof=1))
    print(f"IS residual sigma: {sigma_is:.3f}°F")

    # Generate predictions for ALL rows (including OOS rows with complete features)
    pred_df = complete.copy()
    pred_df["model_pred"] = model.predict(pred_df[feats_cols].values)
    pred_df = pred_df[["station", "local_date", "model_pred", target, "nbs_pred_max_f"]]

    # Map station → city (reverse of CITY_TO_STATION)
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    pred_df["city"] = pred_df["station"].map(station_to_city)
    pred_df = pred_df.dropna(subset=["city"])

    # Join with trade table on (city, market_date)
    tbl = tbl.merge(
        pred_df[["city", "local_date", "model_pred", "nbs_pred_max_f"]].rename(
            columns={"local_date": "market_date", "nbs_pred_max_f": "nbs_pred_feat"}
        ),
        on=["city", "market_date"], how="left"
    )
    print(f"Trade table after join: {len(tbl)}, with model_pred: {tbl.model_pred.notna().sum()}")

    # Compute bucket probabilities via Normal CDF(model_pred, sigma)
    def compute_p(row):
        if pd.isna(row["model_pred"]):
            return np.nan
        return bucket_prob(row["model_pred"], sigma_is, row["bucket_low"], row["bucket_high"])

    tbl["model_p"] = tbl.apply(compute_p, axis=1)
    tbl["edge"] = tbl["model_p"] - tbl["entry_price"]
    tbl["date"] = tbl["market_date"].dt.date

    # Assign strategy fold
    def strat_fold(d):
        if STRATEGY_IS_START <= d <= STRATEGY_IS_END:
            return "IS"
        if STRATEGY_OOS_START <= d <= STRATEGY_OOS_END:
            return "OOS"
        return "OOB"

    tbl["strat_fold"] = tbl["date"].apply(strat_fold)
    tbl = tbl[tbl["strat_fold"].isin(["IS", "OOS"])].copy()
    tbl = tbl.dropna(subset=["model_p"])
    print(f"After filtering to strategy IS/OOS with model_p: {len(tbl)}")
    print(tbl.strat_fold.value_counts().to_dict())

    # Simulate strategies at different edge thresholds
    def simulate(df, threshold, min_price=0.02, max_price=0.90):
        """Buy every bucket where edge > threshold and price in bounds."""
        bets = df[(df.edge > threshold) & (df.entry_price >= min_price)
                  & (df.entry_price <= max_price)].copy()
        if len(bets) == 0:
            return bets, {"n": 0, "hit": 0, "pnl": 0, "per": 0}
        bets["fee"] = FEE_RATE * bets.entry_price * (1 - bets.entry_price)
        bets["pnl"] = bets.won_yes - bets.entry_price - bets.fee
        return bets, {
            "n": len(bets),
            "hit": bets.won_yes.mean(),
            "pnl": bets.pnl.sum(),
            "per": bets.pnl.mean(),
            "mean_edge": bets.edge.mean(),
            "mean_entry": bets.entry_price.mean(),
            "std": bets.pnl.std() if len(bets) > 1 else 0,
        }

    # === Threshold sweep on strat IS (Mar 11-31) ===
    print("\n=== Strategy IS (Mar 11-31): edge threshold sweep ===")
    is_tbl = tbl[tbl.strat_fold == "IS"]
    oos_tbl = tbl[tbl.strat_fold == "OOS"]
    thresholds = [0.00, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20]
    print(f"{'thresh':>7}  {'n':>4}  {'hit':>6}  {'per':>8}  {'tot':>9}  "
          f"{'meanE':>7}  {'meanP':>7}  {'t':>6}")
    is_best = {"per": -99, "thresh": None}
    for th in thresholds:
        _, s = simulate(is_tbl, th)
        ts = s["per"] / (s["std"] / s["n"]**0.5) if s["n"] > 1 and s["std"] > 0 else 0
        print(f"{th:>7.2f}  {s['n']:>4}  {s['hit']*100:>5.1f}%  "
              f"${s['per']:>+7.3f}  ${s['pnl']:>+8.2f}  "
              f"{s.get('mean_edge', 0):>+6.3f}  {s.get('mean_entry', 0):>6.3f}  {ts:>+5.2f}")
        if s["n"] >= 10 and s["per"] > is_best["per"]:
            is_best = {"per": s["per"], "thresh": th, **s}

    if is_best["thresh"] is None:
        print("No threshold had ≥10 trades on IS. Aborting OOS.")
        return

    print(f"\nBest IS threshold: edge > {is_best['thresh']} (per-trade ${is_best['per']:+.3f})")

    # === Apply best threshold to OOS (Apr 1-10) ===
    print(f"\n=== Strategy OOS (Apr 1-10) at threshold {is_best['thresh']} ===")
    oos_bets, oos_s = simulate(oos_tbl, is_best["thresh"])
    ts_oos = oos_s["per"] / (oos_s["std"] / oos_s["n"]**0.5) if oos_s["n"] > 1 and oos_s["std"] > 0 else 0
    print(f"  n={oos_s['n']}, hit={oos_s['hit']*100:.1f}%, "
          f"per-trade=${oos_s['per']:+.3f}, total=${oos_s['pnl']:+.2f}, t={ts_oos:+.2f}")

    # Also sweep thresholds on OOS (diagnostic only, not for decision)
    print(f"\n=== OOS threshold sweep (diagnostic) ===")
    for th in thresholds:
        _, s = simulate(oos_tbl, th)
        ts = s["per"] / (s["std"] / s["n"]**0.5) if s["n"] > 1 and s["std"] > 0 else 0
        print(f"{th:>7.2f}  n={s['n']:>4}  hit={s['hit']*100:>5.1f}%  "
              f"per=${s['per']:>+7.3f}  tot=${s['pnl']:>+8.2f}  t={ts:>+5.2f}")

    # Per-city breakdown at best threshold
    print(f"\n=== Per-city OOS at best threshold {is_best['thresh']} ===")
    if len(oos_bets) > 0:
        for city, grp in oos_bets.groupby("city"):
            print(f"  {city:<18} n={len(grp):>3}  hit={grp.won_yes.mean()*100:>5.1f}%  "
                  f"per=${grp.pnl.mean():>+6.3f}  tot=${grp.pnl.sum():>+6.2f}")

    # Save trades
    out = V3 / "trades.parquet"
    if len(oos_bets):
        pd.concat([
            simulate(is_tbl, is_best['thresh'])[0].assign(fold="IS"),
            oos_bets.assign(fold="OOS"),
        ]).to_parquet(out, index=False)
        print(f"\nWrote trades: {out}")


if __name__ == "__main__":
    main()

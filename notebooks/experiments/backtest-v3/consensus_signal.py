"""Forecast-consensus as a trading signal.

Hypothesis: when NBS, GFS MOS, and HRRR all AGREE on the daily max,
the market should be near-efficient (forecasts are informative to
serious traders). When they DISAGREE, the market may mis-weight
which forecast to trust.

Consensus = inverse of spread between NBS, GFS, HRRR predictions.
Quantify: max(pred) - min(pred) across the 3 forecasts.

Test:
1. When consensus is HIGH (spread ≤ 2°F), does offset=+1 NO edge
   improve or stay same?
2. When LOW (spread ≥ 4°F), does it fail?
3. Independently: does consensus predict direction of NBS error?

Use strict holdout: discover on Mar 11-25, validate on Mar 26-Apr 10.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE = 0.05
STRAT_IS_START = date(2026, 3, 11)
STRAT_IS_END = date(2026, 3, 25)
STRAT_OOS_START = date(2026, 3, 26)
STRAT_OOS_END = date(2026, 4, 10)


def main():
    # Load features for consensus computation
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    # Compute consensus: max - min across NBS, GFS, HRRR
    feat = feat.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"])
    feat["consensus_spread"] = (
        feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].max(axis=1)
        - feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].min(axis=1)
    )
    print(f"Consensus spread distribution:")
    print(feat.consensus_spread.describe())

    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)
    feat_map = feat.set_index(["city", "local_date"])[[
        "consensus_spread", "nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"
    ]]

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date

    # Join
    tbl = tbl.merge(feat_map.reset_index().rename(columns={"local_date": "market_date"}),
                    on=["city", "market_date"], how="left",
                    suffixes=("", "_feat"))
    tbl = tbl.dropna(subset=["consensus_spread"])
    print(f"Joined rows: {len(tbl)}")

    def eval_no(df, off, filters=None):
        """Buy NO at the offset bucket, apply optional filter."""
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            if filters and not all(f(day) for f in filters):
                continue
            nbs_pred = day["nbs_pred_max_f"].iloc[0]
            diff = (day["bucket_center"] - nbs_pred).abs()
            fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
            row = day[day["bucket_idx"] == fav_idx + off]
            if row.empty:
                continue
            r = row.iloc[0]
            yes_p = float(r["entry_price"])
            if yes_p < 0.005 or yes_p > 0.5:
                continue
            no_p = 1 - yes_p
            no_won = 1 - int(r["won_yes"])
            fee = FEE * no_p * (1 - no_p)
            pnl = no_won - no_p - fee
            trades.append({"city": city, "date": md.date(), "no_price": no_p,
                          "won_no": no_won, "pnl": pnl,
                          "consensus": float(day["consensus_spread"].iloc[0])})
        return pd.DataFrame(trades)

    def stat(t, name):
        if len(t) == 0:
            print(f"  {name}: n=0")
            return
        std = t.pnl.std() if len(t) > 1 else 0
        ts = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(t):>3}  hit={t.won_no.mean()*100:>5.1f}%  "
              f"per=${t.pnl.mean():>+.4f}  tot=${t.pnl.sum():>+.2f}  t={ts:>+.2f}")

    is_tbl = tbl[(tbl.date >= STRAT_IS_START) & (tbl.date <= STRAT_IS_END)]
    oos_tbl = tbl[(tbl.date >= STRAT_OOS_START) & (tbl.date <= STRAT_OOS_END)]

    # Consensus-spread buckets
    print()
    print("=== Consensus-spread buckets on +1 offset NO ===")
    for lo, hi in [(0, 2), (2, 4), (4, 99)]:
        print(f"  consensus spread [{lo}, {hi})°F:")
        is_sub = is_tbl[(is_tbl.consensus_spread >= lo) & (is_tbl.consensus_spread < hi)]
        oos_sub = oos_tbl[(oos_tbl.consensus_spread >= lo) & (oos_tbl.consensus_spread < hi)]
        t_is = eval_no(is_sub, 1)
        t_oos = eval_no(oos_sub, 1)
        stat(t_is, "    IS")
        stat(t_oos, "    OOS")

    # Tight consensus filter (≤ 2°F spread) — test
    print()
    print("=== STRICT filter: consensus spread ≤ 2°F ===")
    print("Strategy B (offset=+1 NO):")
    filters = [lambda d: float(d["consensus_spread"].iloc[0]) <= 2.0]
    stat(eval_no(is_tbl, 1, filters=filters), "IS")
    stat(eval_no(oos_tbl, 1, filters=filters), "OOS")

    # Does NBS residual correlate with consensus spread?
    print()
    print("=== Consensus spread vs NBS error (feat-level IS) ===")
    is_feat = feat[feat.fold == "IS"].copy()
    is_feat["nbs_err"] = is_feat["actual_max_f"] - is_feat["nbs_pred_max_f"]
    for lo, hi in [(0, 2), (2, 4), (4, 99)]:
        sub = is_feat[(is_feat.consensus_spread >= lo) & (is_feat.consensus_spread < hi)]
        if len(sub) == 0:
            continue
        print(f"  spread [{lo}, {hi}): n={len(sub)}, mean|err|={sub.nbs_err.abs().mean():.2f}, "
              f"mean_err={sub.nbs_err.mean():+.2f}")

    # Also check: when consensus is HIGH (all agree), is NBS err smaller?
    # That would confirm the intuition.
    print()
    print("=== NBS MAE by consensus spread (IS data) ===")
    is_feat["consensus_bucket"] = pd.cut(is_feat["consensus_spread"],
                                         bins=[-1, 1, 2, 3, 5, 100],
                                         labels=["0-1", "1-2", "2-3", "3-5", "5+"])
    print(is_feat.groupby("consensus_bucket", observed=True)["nbs_err"].agg(["count", "mean", "std"]).to_string())
    is_feat["nbs_abs_err"] = is_feat["nbs_err"].abs()
    print(is_feat.groupby("consensus_bucket", observed=True)["nbs_abs_err"].agg(["count", "mean"]).to_string())

    # Compound: low-MAE cities (Seattle+Miami) AND offset=+1 NO
    # Is that robust to strict holdout?
    print()
    print("=== Compound: buy NO on +1 offset, SEATTLE + MIAMI only ===")
    for name, sub in [("IS", is_tbl), ("OOS", oos_tbl)]:
        sub_filt = sub[sub.city.isin(["Seattle", "Miami"])]
        t = eval_no(sub_filt, 1)
        stat(t, name)


if __name__ == "__main__":
    main()

"""Analyze losing trades in Strategy C (consensus ≤ 2°F + offset=+1 NO).

Questions:
1. What do losing days look like? (NBS vs actual, consensus spread, city, week)
2. Can we predict which days will lose?
3. Test morning-METAR filter: is morning-hot a useful 'skip' signal?
4. Test tighter consensus (≤1.5°F) — does edge strengthen or noise up?
5. Test symmetric strategy (-1 YES vs +1 NO) under same consensus filter
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


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat = feat.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f",
                               "tmp_noon_f", "tmp_morning_f", "actual_max_f"])
    feat["consensus_spread"] = (
        feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].max(axis=1)
        - feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].min(axis=1)
    )
    feat["ensemble_mean"] = feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].mean(axis=1)
    feat["nbs_err"] = feat["actual_max_f"] - feat["nbs_pred_max_f"]
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)
    feat = feat.dropna(subset=["city"])

    # Seasonal morning-temp baseline: per (station, month), mean of tmp_noon_f
    # Using IS data only (no leakage)
    is_feat = feat[feat.fold == "IS"].copy()
    seasonal_noon = is_feat.groupby("station")["tmp_noon_f"].mean().to_dict()
    feat["morning_anomaly"] = feat["tmp_noon_f"] - feat["station"].map(seasonal_noon)

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date

    tbl = tbl.merge(
        feat[["city", "local_date", "consensus_spread", "nbs_pred_max_f",
              "gfs_pred_max_f", "hrrr_max_t_f", "tmp_noon_f", "tmp_morning_f",
              "actual_max_f", "morning_anomaly", "ensemble_mean", "nbs_err"]].rename(
            columns={"local_date": "market_date"}),
        on=["city", "market_date"], how="left", suffixes=("", "_f")
    )

    # Filter to Mar 11-Apr 10 and drop missing
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()
    tbl = tbl.dropna(subset=["consensus_spread"])

    # Compute +1 offset NO trades
    def run_strategy_c(df, consensus_max=2.0, off=1, extra_filter=None):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            cs = float(day["consensus_spread"].iloc[0])
            if cs > consensus_max:
                continue
            if extra_filter is not None and not extra_filter(day):
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
            trades.append({
                "city": city, "date": md.date(),
                "consensus_spread": cs,
                "nbs_pred": float(nbs_pred),
                "actual_max": float(day["actual_max_f"].iloc[0]) if pd.notna(day["actual_max_f"].iloc[0]) else None,
                "nbs_err": float(day["nbs_err"].iloc[0]) if pd.notna(day["nbs_err"].iloc[0]) else None,
                "morning_anomaly": float(day["morning_anomaly"].iloc[0]) if pd.notna(day["morning_anomaly"].iloc[0]) else 0,
                "tmp_noon": float(day["tmp_noon_f"].iloc[0]) if pd.notna(day["tmp_noon_f"].iloc[0]) else None,
                "ensemble_mean": float(day["ensemble_mean"].iloc[0]),
                "bucket": f"{day[day['bucket_idx']==fav_idx+off]['group_item_title'].iloc[0]}",
                "yes_price": yes_p, "no_price": no_p,
                "yes_won": int(r["won_yes"]), "no_won": no_won,
                "pnl": pnl,
            })
        return pd.DataFrame(trades)

    # === Baseline Strategy C ===
    print("=== Baseline Strategy C (consensus ≤ 2°F, +1 NO) on FULL Mar 11-Apr 10 ===")
    t = run_strategy_c(tbl, consensus_max=2.0)
    print(f"Total: n={len(t)}, hit={t.no_won.mean()*100:.1f}%, per=${t.pnl.mean():+.4f}")

    # === Analyze LOSERS ===
    losers = t[t.yes_won == 1].copy()
    print(f"\n=== LOSING trades ({len(losers)} of {len(t)}, {len(losers)/len(t)*100:.1f}%) ===")
    if len(losers) > 0:
        print("\nLoser details:")
        for _, r in losers.iterrows():
            print(f"  {r.date} {r.city:<16} NBS={r.nbs_pred:.0f} actual={r.actual_max:.0f} "
                  f"err={r.nbs_err:+.1f}  consensus={r.consensus_spread:.1f}  "
                  f"morning_anom={r.morning_anomaly:+.1f}  bucket={r.bucket:<12} "
                  f"no_price={r.no_price:.3f}  pnl=${r.pnl:+.3f}")
    winners = t[t.yes_won == 0]
    print(f"\n=== WINNERS (comparative stats) ===")
    print(f"Winners: n={len(winners)}")
    print(f"  mean NBS_err: {winners.nbs_err.mean():+.2f} (vs losers: {losers.nbs_err.mean() if len(losers) else 0:+.2f})")
    print(f"  mean morning_anomaly: {winners.morning_anomaly.mean():+.2f} (vs losers: {losers.morning_anomaly.mean() if len(losers) else 0:+.2f})")
    print(f"  mean consensus_spread: {winners.consensus_spread.mean():.2f} (vs losers: {losers.consensus_spread.mean() if len(losers) else 0:.2f})")

    # === Morning-anomaly filter ===
    print()
    print("=== Strategy C + morning anomaly filter ===")
    for anom_max in (2.0, 3.0, 5.0, 99.0):
        t_f = run_strategy_c(tbl, consensus_max=2.0,
                            extra_filter=lambda d, mx=anom_max: (
                                (pd.notna(d["morning_anomaly"].iloc[0]))
                                and (float(d["morning_anomaly"].iloc[0]) <= mx)
                            ))
        if len(t_f) == 0:
            continue
        std = t_f.pnl.std() if len(t_f) > 1 else 0
        tstat = t_f.pnl.mean() / (std / len(t_f)**0.5) if std > 0 else 0
        print(f"  morning_anom ≤ {anom_max}°F: n={len(t_f)}, hit={t_f.no_won.mean()*100:.1f}%, "
              f"per=${t_f.pnl.mean():+.4f}, t={tstat:+.2f}")

    # === Tighter consensus test ===
    print()
    print("=== Consensus threshold sweep (keeping +1 NO) ===")
    for cs_max in (1.0, 1.5, 2.0, 2.5, 3.0):
        t_cs = run_strategy_c(tbl, consensus_max=cs_max)
        if len(t_cs) == 0: continue
        std = t_cs.pnl.std() if len(t_cs) > 1 else 0
        tstat = t_cs.pnl.mean() / (std / len(t_cs)**0.5) if std > 0 else 0
        # Split
        is_sub = t_cs[pd.to_datetime(t_cs.date) <= pd.Timestamp("2026-03-25")]
        oos_sub = t_cs[pd.to_datetime(t_cs.date) > pd.Timestamp("2026-03-25")]
        is_tstat = (is_sub.pnl.mean() / (is_sub.pnl.std() / len(is_sub)**0.5)
                    if len(is_sub) > 1 and is_sub.pnl.std() > 0 else 0)
        oos_tstat = (oos_sub.pnl.mean() / (oos_sub.pnl.std() / len(oos_sub)**0.5)
                    if len(oos_sub) > 1 and oos_sub.pnl.std() > 0 else 0)
        print(f"  cs ≤ {cs_max}: n={len(t_cs)}, hit={t_cs.no_won.mean()*100:.1f}%, "
              f"per=${t_cs.pnl.mean():+.4f}, t={tstat:+.2f}  "
              f"[IS t={is_tstat:+.2f}, OOS t={oos_tstat:+.2f}]")

    # === Per-city under consensus filter ===
    print()
    print("=== Per-city (Strategy C at consensus ≤ 2°F) ===")
    t = run_strategy_c(tbl, consensus_max=2.0)
    for city, g in t.groupby("city"):
        if len(g) < 3:
            continue
        std = g.pnl.std() if len(g) > 1 else 0
        tstat = g.pnl.mean() / (std / len(g)**0.5) if std > 0 else 0
        print(f"  {city:<18} n={len(g):>2} hit={g.no_won.mean()*100:>5.1f}% "
              f"per=${g.pnl.mean():>+.3f} tot=${g.pnl.sum():>+.2f} t={tstat:>+.2f}")

    # === Symmetric -1 offset YES ===
    print()
    print("=== Sister strategy: offset=-1 YES (buy below NBS fav) under consensus filter ===")
    def run_symmetric(df, consensus_max=2.0, off=-1):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            cs = float(day["consensus_spread"].iloc[0])
            if cs > consensus_max: continue
            nbs_pred = day["nbs_pred_max_f"].iloc[0]
            diff = (day["bucket_center"] - nbs_pred).abs()
            fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
            row = day[day["bucket_idx"] == fav_idx + off]
            if row.empty: continue
            r = row.iloc[0]
            yes_p = float(r["entry_price"])
            if yes_p < 0.02 or yes_p > 0.95:
                continue
            fee = FEE * yes_p * (1 - yes_p)
            pnl = float(r["won_yes"]) - yes_p - fee
            trades.append({"city": city, "date": md.date(), "yes_price": yes_p,
                          "won": int(r["won_yes"]), "pnl": pnl})
        return pd.DataFrame(trades)

    t_sym = run_symmetric(tbl, consensus_max=2.0, off=-1)
    if len(t_sym) > 0:
        std = t_sym.pnl.std() if len(t_sym) > 1 else 0
        tstat = t_sym.pnl.mean() / (std / len(t_sym)**0.5) if std > 0 else 0
        print(f"  -1 YES: n={len(t_sym)}, hit={t_sym.won.mean()*100:.1f}%, "
              f"per=${t_sym.pnl.mean():+.4f}, t={tstat:+.2f}, avg_price=${t_sym.yes_price.mean():.3f}")

    # Also try -1 NO (fade below)
    print()
    print("=== -1 NO (fade below NBS fav) ===")
    def run_minus1_no(df, consensus_max=2.0):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            cs = float(day["consensus_spread"].iloc[0])
            if cs > consensus_max: continue
            nbs_pred = day["nbs_pred_max_f"].iloc[0]
            diff = (day["bucket_center"] - nbs_pred).abs()
            fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
            row = day[day["bucket_idx"] == fav_idx - 1]
            if row.empty: continue
            r = row.iloc[0]
            yes_p = float(r["entry_price"])
            if yes_p < 0.005 or yes_p > 0.5:
                continue
            no_p = 1 - yes_p
            no_won = 1 - int(r["won_yes"])
            fee = FEE * no_p * (1 - no_p)
            pnl = no_won - no_p - fee
            trades.append({"city": city, "pnl": pnl, "won_no": no_won})
        return pd.DataFrame(trades)

    t_m1 = run_minus1_no(tbl, consensus_max=2.0)
    if len(t_m1) > 0:
        std = t_m1.pnl.std() if len(t_m1) > 1 else 0
        tstat = t_m1.pnl.mean() / (std / len(t_m1)**0.5) if std > 0 else 0
        print(f"  -1 NO: n={len(t_m1)}, hit={t_m1.won_no.mean()*100:.1f}%, "
              f"per=${t_m1.pnl.mean():+.4f}, t={tstat:+.2f}")


if __name__ == "__main__":
    main()

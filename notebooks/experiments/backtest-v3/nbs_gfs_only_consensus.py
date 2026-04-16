"""Test Strategy C' variant using NBS+GFS-only consensus (no HRRR).

Rationale: HRRR data can be stale/missing on recent days, which makes the
3-forecast consensus filter unreliable in practice. If NBS+GFS-only
consensus gives similar results, it's a more robust deployable signal.

Compares:
- Original: max(NBS, GFS, HRRR) - min(NBS, GFS, HRRR) ≤ 3°F
- New:      max(NBS, GFS) - min(NBS, GFS) ≤ 2°F
"""
from __future__ import annotations

from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL",
    "Seattle": "SEA", "Chicago": "ORD", "Miami": "MIA",
    "Austin": "AUS", "Houston": "HOU", "Denver": "DEN",
    "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE = 0.05


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    # Compute both consensus variants
    feat = feat.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f"])
    feat["cs2"] = (feat["nbs_pred_max_f"] - feat["gfs_pred_max_f"]).abs()  # NBS/GFS only
    # For 3-way, only if HRRR present
    has_hrrr = feat["hrrr_max_t_f"].notna()
    feat["cs3"] = np.where(
        has_hrrr,
        feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].max(axis=1)
        - feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].min(axis=1),
        np.nan,
    )
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)
    feat = feat.dropna(subset=["city"])

    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    for c in ("cs2", "cs3", "nbs_pred_max_f_feat"):
        if c in tbl.columns: tbl = tbl.drop(columns=[c])
    tbl = tbl.merge(feat[["city", "local_date", "cs2", "cs3", "nbs_pred_max_f"]]
        .rename(columns={"local_date": "market_date"}),
        on=["city", "market_date"], how="left", suffixes=("", "_feat"))
    tbl = tbl.dropna(subset=["cs2", "nbs_pred_max_f"])
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))]

    def run(df, cs_col, cs_max, offset=1):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            cs = day[cs_col].iloc[0]
            if pd.isna(cs) or cs > cs_max:
                continue
            nbs_pred = day["nbs_pred_max_f"].iloc[0]
            diff = (day["bucket_center"] - nbs_pred).abs()
            fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
            row = day[day["bucket_idx"] == fav_idx + offset]
            if row.empty: continue
            r = row.iloc[0]
            yes_p = float(r["entry_price"])
            if yes_p < 0.005 or yes_p > 0.5: continue
            no_p = 1 - yes_p
            no_won = 1 - int(r["won_yes"])
            fee = FEE * no_p * (1 - no_p)
            pnl = no_won - no_p - fee
            trades.append({"city": city, "date": md.date(), "cs": float(cs),
                          "won_no": no_won, "pnl": pnl})
        return pd.DataFrame(trades)

    def stats(t, name):
        if len(t) == 0:
            print(f"  {name}: n=0")
            return
        is_t = t[pd.to_datetime(t.date) <= pd.Timestamp("2026-03-25")]
        oos_t = t[pd.to_datetime(t.date) > pd.Timestamp("2026-03-25")]
        tot_s = t.pnl.std() if len(t) > 1 else 0
        tot_ts = t.pnl.mean() / (tot_s / len(t)**0.5) if tot_s > 0 else 0
        is_s = is_t.pnl.std() if len(is_t) > 1 else 0
        is_ts = is_t.pnl.mean() / (is_s / len(is_t)**0.5) if is_s > 0 else 0
        oos_s = oos_t.pnl.std() if len(oos_t) > 1 else 0
        oos_ts = oos_t.pnl.mean() / (oos_s / len(oos_t)**0.5) if oos_s > 0 else 0
        print(f"  {name:<22}  n={len(t):>3} ({len(is_t):>2}IS/{len(oos_t):>2}OOS)  "
              f"hit={t.won_no.mean()*100:>5.1f}%  per=${t.pnl.mean():>+.4f}  "
              f"t={tot_ts:>+.2f}  [IS t={is_ts:+.2f} OOS t={oos_ts:+.2f}]")

    print("=== Consensus filter comparison: 3-way (NBS+GFS+HRRR) vs 2-way (NBS+GFS) ===")
    print()
    print("3-way (current): max - min of {NBS, GFS, HRRR}")
    for cs_max in (1.0, 2.0, 3.0):
        t = run(tbl, "cs3", cs_max)
        stats(t, f"cs3 ≤ {cs_max}")

    print()
    print("2-way (NBS+GFS only): |NBS - GFS|")
    for cs_max in (1.0, 2.0, 3.0):
        t = run(tbl, "cs2", cs_max)
        stats(t, f"cs2 ≤ {cs_max}")

    # Combined: 2-way AND 3-way both agree
    print()
    print("Combined: cs2 ≤ 2°F AND cs3 ≤ 3°F")
    def run_combined(df, cs2_max, cs3_max, offset=1):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            cs2 = day["cs2"].iloc[0]
            cs3 = day["cs3"].iloc[0]
            if pd.isna(cs2) or cs2 > cs2_max: continue
            if pd.notna(cs3) and cs3 > cs3_max: continue
            nbs_pred = day["nbs_pred_max_f"].iloc[0]
            diff = (day["bucket_center"] - nbs_pred).abs()
            fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
            row = day[day["bucket_idx"] == fav_idx + offset]
            if row.empty: continue
            r = row.iloc[0]
            yes_p = float(r["entry_price"])
            if yes_p < 0.005 or yes_p > 0.5: continue
            no_p = 1 - yes_p
            no_won = 1 - int(r["won_yes"])
            fee = FEE * no_p * (1 - no_p)
            pnl = no_won - no_p - fee
            trades.append({"date": md.date(), "won_no": no_won, "pnl": pnl})
        return pd.DataFrame(trades)

    t = run_combined(tbl, 2.0, 3.0)
    stats(t, "cs2≤2 AND cs3≤3")


if __name__ == "__main__":
    main()

"""Trade only cities where NBS has high accuracy (measured on IS).

Hypothesis: when NBS is accurate, the market may underprice the NBS-
favorite bucket because the market is a blend of NBS-informed + retail
money. Retail may spread probability across multiple buckets, leaving
the NBS-favorite as a slight value buy.

Strategy:
1. Measure per-city NBS MAE on IS (Dec-Feb)
2. Select cities with MAE below some threshold
3. For those cities, buy the NBS favorite
4. Evaluate OOS

No OOS-dependent city selection = clean.
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


def main():
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat["nbs_abs_err"] = (feat["actual_max_f"] - feat["nbs_pred_max_f"]).abs()
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)
    feat = feat.dropna(subset=["city", "nbs_abs_err"])

    # Per-city NBS MAE on IS
    print("=== Per-city NBS MAE (IS vs OOS) ===")
    is_mae = feat[feat.fold == "IS"].groupby("city").nbs_abs_err.mean().sort_values()
    oos_mae = feat[feat.fold == "OOS"].groupby("city").nbs_abs_err.mean()
    print(f"{'city':<18} {'IS_MAE':>7} {'OOS_MAE':>8}")
    for city in is_mae.index:
        print(f"{city:<18} {is_mae[city]:>7.3f} {oos_mae.get(city, np.nan):>8.3f}")

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    def buy_nbs_fav(day):
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == nbs_fav_idx]
        return row.iloc[0] if len(row) == 1 else None

    def buy_mkt_fav(day):
        return day.loc[day["entry_price"].idxmax()]

    def run(cities, selector, name):
        sub = tbl[tbl.city.isin(cities)]
        trades = []
        for (city, md), grp in sub.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            r = selector(day)
            if r is None or r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({"city": city, "pnl": pnl, "won": int(r["won_yes"]),
                          "price": float(r["entry_price"])})
        t = pd.DataFrame(trades)
        if len(t) == 0:
            print(f"  {name}: n=0")
            return None
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(t):>3}  hit={t.won.mean()*100:>5.1f}%  "
              f"per=${t.pnl.mean():>+.4f}  tot=${t.pnl.sum():>+.2f}  t={tstat:>+.2f}  "
              f"avg_price=${t.price.mean():.3f}")
        return t

    # Threshold sweeps
    print("\n=== NBS_fav strategy filtered by IS NBS MAE threshold ===")
    for mae_th in (1.0, 1.2, 1.5, 1.7, 2.0, 2.5, 3.0, 999):
        cities = is_mae[is_mae <= mae_th].index.tolist()
        print(f"  MAE ≤ {mae_th}  ({len(cities)} cities: {sorted(cities)[:5]}...)")
        run(cities, buy_nbs_fav, f"    NBS_fav")

    print("\n=== MKT_fav strategy filtered by IS NBS MAE threshold ===")
    for mae_th in (1.0, 1.2, 1.5, 1.7, 2.0, 2.5, 3.0, 999):
        cities = is_mae[is_mae <= mae_th].index.tolist()
        print(f"  MAE ≤ {mae_th}  ({len(cities)} cities)")
        run(cities, buy_mkt_fav, f"    MKT_fav")

    # Within-OOS split to confirm robustness
    split = date(2026, 3, 25)
    print("\n=== Within-OOS split for NBS_fav, MAE ≤ 1.5 cities ===")
    cities = is_mae[is_mae <= 1.5].index.tolist()
    print(f"  Cities: {sorted(cities)}")
    sub = tbl[tbl.city.isin(cities)]
    t1 = run(cities, buy_nbs_fav, f"    all")
    t1a = run([c for c in cities if c in sub[sub.date <= split].city.unique()], buy_nbs_fav, f"    (will be filtered by date)")
    # Actually need to filter tbl by date properly
    trades = []
    for (city, md), grp in sub.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        r = buy_nbs_fav(day)
        if r is None or r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
            continue
        fee = FEE * r["entry_price"] * (1 - r["entry_price"])
        pnl = float(r["won_yes"]) - r["entry_price"] - fee
        trades.append({"city": city, "date": md.date(), "pnl": pnl,
                      "won": int(r["won_yes"]), "price": float(r["entry_price"])})
    t = pd.DataFrame(trades)
    t1 = t[t.date <= split]
    t2 = t[t.date > split]
    std = t1.pnl.std() if len(t1) > 1 else 0
    ts = t1.pnl.mean() / (std / len(t1)**0.5) if std > 0 else 0
    print(f"    Mar 11-25: n={len(t1):>3}  hit={t1.won.mean()*100:>5.1f}%  "
          f"per=${t1.pnl.mean():>+.4f}  tot=${t1.pnl.sum():>+.2f}  t={ts:>+.2f}")
    std = t2.pnl.std() if len(t2) > 1 else 0
    ts = t2.pnl.mean() / (std / len(t2)**0.5) if std > 0 else 0
    print(f"    Mar 26-Apr10: n={len(t2):>3}  hit={t2.won.mean()*100:>5.1f}%  "
          f"per=${t2.pnl.mean():>+.4f}  tot=${t2.pnl.sum():>+.2f}  t={ts:>+.2f}")

    # Per-city for NBS_fav (MAE ≤ 1.5)
    print("\n=== Per-city for NBS_fav (cities with IS MAE ≤ 1.5) ===")
    for city, g in t.groupby("city"):
        std = g.pnl.std() if len(g) > 1 else 0
        ts = g.pnl.mean() / (std / len(g)**0.5) if std > 0 else 0
        print(f"  {city:<18} n={len(g):>3}  hit={g.won.mean()*100:>5.1f}%  "
              f"per=${g.pnl.mean():>+.3f}  tot=${g.pnl.sum():>+.2f}  t={ts:>+.2f}")


if __name__ == "__main__":
    main()

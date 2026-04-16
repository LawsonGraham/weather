"""Combined portfolio strategy.

Three independent strategies discovered in v3:
A. NBS-fav on IS-MAE≤1.5 cities (Seattle + Miami): n=39, +$0.126/trade
B. Buy-NO on NBS_fav+1 bucket (all cities): n=179, +$0.055/trade
C. Per-city buy NBS-fav (weaker baseline)

Run all three together, report aggregate PnL and per-day capital.
Use within-OOS split to verify robustness.

Key questions:
- Are A and B independent? Same day can have both.
- Total capital requirement?
- Sharpe / risk-adjusted returns?
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

# From iter 4: IS NBS MAE ≤ 1.5°F cities
LOW_MAE_CITIES = {"Seattle", "Miami"}


def main():
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    def nbs_fav_idx(day):
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        return int(day.loc[diff.idxmin(), "bucket_idx"])

    # Strategy A: NBS-fav YES in low-MAE cities
    # Strategy B: NBS_fav+1 NO in all cities (if yes price in [0.005, 0.5])
    all_trades = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        fav_idx = nbs_fav_idx(day)

        # Strategy A: NBS-fav YES on low-MAE cities
        if city in LOW_MAE_CITIES:
            row = day[day["bucket_idx"] == fav_idx]
            if len(row) == 1:
                r = row.iloc[0]
                if 0.02 <= r["entry_price"] <= 0.95:
                    fee = FEE * r["entry_price"] * (1 - r["entry_price"])
                    pnl = float(r["won_yes"]) - r["entry_price"] - fee
                    all_trades.append({
                        "strategy": "A_nbs_fav_lowmae",
                        "city": city, "date": md.date(),
                        "bucket_idx": int(r["bucket_idx"]),
                        "side": "YES",
                        "cost": float(r["entry_price"]),
                        "pnl": pnl,
                        "won": int(r["won_yes"]),
                    })

        # Strategy B: +1 offset NO (all cities)
        row = day[day["bucket_idx"] == fav_idx + 1]
        if len(row) == 1:
            r = row.iloc[0]
            yes_price = float(r["entry_price"])
            if 0.005 <= yes_price <= 0.5:
                no_price = 1 - yes_price
                no_won = 1 - int(r["won_yes"])
                fee = FEE * no_price * (1 - no_price)
                pnl = float(no_won) - no_price - fee
                all_trades.append({
                    "strategy": "B_no_plus1",
                    "city": city, "date": md.date(),
                    "bucket_idx": int(r["bucket_idx"]),
                    "side": "NO",
                    "cost": no_price,
                    "pnl": pnl,
                    "won": no_won,
                })

    t = pd.DataFrame(all_trades)
    print(f"Total strategy-trades: {len(t)}")
    print(f"By strategy: {t.groupby('strategy').size().to_dict()}")

    def summary(sub, name):
        if len(sub) == 0:
            print(f"  {name}: n=0")
            return
        std = sub.pnl.std() if len(sub) > 1 else 0
        ts = sub.pnl.mean() / (std / len(sub)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(sub):>3}  hit={sub.won.mean()*100:>5.1f}%  "
              f"per=${sub.pnl.mean():>+.4f}  tot=${sub.pnl.sum():>+.2f}  "
              f"t={ts:>+.2f}  avg_cost=${sub.cost.mean():.3f}")

    print()
    print("=== Individual strategies (FULL Mar 11-Apr 10) ===")
    summary(t[t.strategy == "A_nbs_fav_lowmae"], "A. NBS-fav low-MAE (YES)")
    summary(t[t.strategy == "B_no_plus1"], "B. +1 offset (NO)")
    summary(t, "COMBINED (all trades)")

    # Within-OOS split
    split = date(2026, 3, 25)
    print()
    print(f"=== Within-OOS split (Mar 11-25 vs Mar 26-Apr 10) ===")
    for half, name in [(t[t.date <= split], "Mar 11-25"),
                       (t[t.date > split], "Mar 26-Apr 10")]:
        print(f"  --- {name} ---")
        summary(half[half.strategy == "A_nbs_fav_lowmae"], "A. NBS-fav low-MAE")
        summary(half[half.strategy == "B_no_plus1"], "B. +1 offset NO")
        summary(half, "COMBINED")

    # Daily PnL aggregate
    print()
    print("=== Daily PnL & capital consumption ===")
    daily = t.groupby("date").agg(
        n_trades=("pnl", "count"),
        daily_pnl=("pnl", "sum"),
        daily_cost=("cost", "sum"),
    ).reset_index()
    print(f"Days traded: {len(daily)}")
    print(f"Avg trades per day: {daily.n_trades.mean():.1f}")
    print(f"Avg capital per day: ${daily.daily_cost.mean():.2f}")
    print(f"Avg daily PnL: ${daily.daily_pnl.mean():+.3f}")
    print(f"Daily PnL std: ${daily.daily_pnl.std():.3f}")
    print(f"Sharpe (daily): {daily.daily_pnl.mean() / daily.daily_pnl.std():.3f}")
    print(f"Annualized Sharpe: {daily.daily_pnl.mean() / daily.daily_pnl.std() * np.sqrt(252):.2f}")
    print(f"Total trading days: {len(daily)}")
    print(f"Total PnL: ${daily.daily_pnl.sum():+.2f}")
    print(f"Positive days: {(daily.daily_pnl > 0).sum()} / {len(daily)}")

    # Cumulative PnL plot-like
    print("\n=== Cumulative PnL trajectory ===")
    daily = daily.sort_values("date")
    daily["cum_pnl"] = daily["daily_pnl"].cumsum()
    daily["cum_cost"] = daily["daily_cost"].cumsum()
    for i, r in daily.iterrows():
        print(f"  {r.date}: n={r.n_trades:>2}  day_pnl=${r.daily_pnl:>+6.3f}  "
              f"day_cost=${r.daily_cost:>6.2f}  cum_pnl=${r.cum_pnl:>+6.2f}")


if __name__ == "__main__":
    main()

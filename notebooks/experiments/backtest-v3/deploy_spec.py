"""Final deployable spec — Strategy C refined.

Iter 7: cs ≤ 2°F + offset=+1 NO passed strict IS/OOS.
Iter 8: LOOSER cs ≤ 3°F is even better (n=91, t=+6.38, IS t=+4.44 OOS t=+4.93).

Need to:
1. Test +1 NO across cs ≤ 3°F with strict IS selection vs OOS
2. Test multi-bucket (buy +1 NO AND +2 NO)
3. Compute daily Sharpe + capital requirements
4. Write the final deployable spec
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
    feat = feat.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"])
    feat["consensus_spread"] = (
        feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].max(axis=1)
        - feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].min(axis=1)
    )
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)
    feat = feat.dropna(subset=["city"])

    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl.merge(
        feat[["city", "local_date", "consensus_spread", "nbs_pred_max_f"]].rename(
            columns={"local_date": "market_date"}),
        on=["city", "market_date"], how="left", suffixes=("", "_f")
    )
    tbl = tbl.dropna(subset=["consensus_spread"])
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    def run_strategy(df, consensus_max, offsets):
        """Buy NO on each of the `offsets` (e.g. [1], [1,2]) under consensus filter."""
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            cs = float(day["consensus_spread"].iloc[0])
            if cs > consensus_max:
                continue
            nbs_pred = day["nbs_pred_max_f"].iloc[0]
            diff = (day["bucket_center"] - nbs_pred).abs()
            fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
            for off in offsets:
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
                    "offset": off, "no_price": no_p,
                    "won_no": no_won, "pnl": pnl,
                    "consensus": cs,
                })
        return pd.DataFrame(trades)

    def stat(t, name):
        if len(t) == 0:
            return None
        std = t.pnl.std() if len(t) > 1 else 0
        ts = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        # Split
        is_sub = t[pd.to_datetime(t.date) <= pd.Timestamp("2026-03-25")]
        oos_sub = t[pd.to_datetime(t.date) > pd.Timestamp("2026-03-25")]
        is_t = (is_sub.pnl.mean() / (is_sub.pnl.std() / len(is_sub)**0.5)
                if len(is_sub) > 1 and is_sub.pnl.std() > 0 else 0)
        oos_t = (oos_sub.pnl.mean() / (oos_sub.pnl.std() / len(oos_sub)**0.5)
                if len(oos_sub) > 1 and oos_sub.pnl.std() > 0 else 0)
        print(f"  {name}: n={len(t):>3}  hit={t.won_no.mean()*100:>5.1f}%  "
              f"per=${t.pnl.mean():>+.4f}  tot=${t.pnl.sum():>+.2f}  t={ts:>+.2f}  "
              f"[IS t={is_t:+.2f} OOS t={oos_t:+.2f}]")
        return t

    # === OFFSET SWEEP under cs ≤ 3 ===
    print("=== Offset sweep under consensus ≤ 3°F ===")
    for offsets in ([1], [2], [3], [1, 2], [1, 2, 3]):
        t = run_strategy(tbl, 3.0, offsets)
        stat(t, f"offsets={offsets}")

    # === Combined: +1 NO at cs ≤ 3.0 (final candidate) ===
    print()
    print("=== FINAL CANDIDATE: +1 NO at consensus ≤ 3°F ===")
    t = run_strategy(tbl, 3.0, [1])
    stat(t, "Strategy C'")

    # Daily breakdown + Sharpe
    print()
    print("=== Daily PnL distribution (Strategy C', +1 NO at cs ≤ 3°F) ===")
    daily = t.groupby("date").agg(
        n_trades=("pnl", "count"),
        day_pnl=("pnl", "sum"),
        day_cost=("no_price", "sum"),
    ).reset_index()
    print(f"Trading days: {len(daily)}")
    print(f"Avg trades/day: {daily.n_trades.mean():.2f}")
    print(f"Avg daily PnL: ${daily.day_pnl.mean():+.4f}")
    print(f"Daily PnL std: ${daily.day_pnl.std():.4f}")
    print(f"Avg capital/day: ${daily.day_cost.mean():.2f}")
    print(f"Positive days: {(daily.day_pnl > 0).sum()}/{len(daily)} "
          f"({(daily.day_pnl > 0).mean()*100:.0f}%)")
    print(f"Sharpe (daily): {daily.day_pnl.mean() / daily.day_pnl.std():.3f}")
    print(f"Annualized Sharpe (252): {daily.day_pnl.mean() / daily.day_pnl.std() * np.sqrt(252):.2f}")

    # Worst days
    print()
    print("Worst 3 days:")
    for _, r in daily.nsmallest(3, "day_pnl").iterrows():
        print(f"  {r.date}: day_pnl=${r.day_pnl:+.3f}, trades={r.n_trades}")

    print()
    print("Best 3 days:")
    for _, r in daily.nlargest(3, "day_pnl").iterrows():
        print(f"  {r.date}: day_pnl=${r.day_pnl:+.3f}, trades={r.n_trades}")

    # Cumulative PnL
    print()
    print("=== Cumulative PnL trajectory ===")
    daily = daily.sort_values("date")
    daily["cum_pnl"] = daily["day_pnl"].cumsum()
    daily["cum_cost"] = daily["day_cost"].cumsum()
    for i, r in daily.iterrows():
        marker = " !!" if r.day_pnl < 0 else ""
        print(f"  {r.date}: n={r.n_trades:>2}  day=${r.day_pnl:>+7.3f}  cum=${r.cum_pnl:>+6.2f}{marker}")

    # Total summary
    print()
    print("=== TOTAL SUMMARY (1 share per trade scale) ===")
    print(f"  Trades: {len(t)}")
    print(f"  Total cost: ${t.no_price.sum():.2f}")
    print(f"  Total PnL: ${t.pnl.sum():+.2f}")
    print(f"  Return on gross cost: {t.pnl.sum() / t.no_price.sum() * 100:.2f}%")
    print(f"  Period: Mar 11 - Apr 10 (31 days)")
    # Turnover: capital recovers at end of each day, so "capital days"
    capital_days = daily.day_cost.sum()  # total dollar-days at risk
    print(f"  Total capital-days: ${capital_days:.2f}")
    print(f"  Return per capital-day: {t.pnl.sum() / capital_days * 100:.3f}%")

    # Per-city summary
    print()
    print("=== Per-city (Strategy C' at cs ≤ 3°F) ===")
    for city, g in t.groupby("city"):
        if len(g) < 3: continue
        std = g.pnl.std() if len(g) > 1 else 0
        tstat = g.pnl.mean() / (std / len(g)**0.5) if std > 0 else 0
        print(f"  {city:<18} n={len(g):>2} hit={g.won_no.mean()*100:>5.1f}% "
              f"per=${g.pnl.mean():>+.3f} tot=${g.pnl.sum():>+.2f} t={tstat:>+.2f}")


if __name__ == "__main__":
    main()

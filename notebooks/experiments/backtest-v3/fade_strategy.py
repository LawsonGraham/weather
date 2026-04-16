"""Buy-NO fade strategy on +N offset buckets.

From iter 3: buying YES on NBS_fav+1 lost -$0.071/trade (t=-3.52)
consistently across both halves of Mar 11-Apr 10.

The contrapositive: BUY NO on that same bucket. If YES hit rate was
10.3%, NO hit rate is 89.7%. NO entry price ~= 1 - YES price.

Expected per-trade:
- YES buy: 0.103 × 1 - 0.168 - fee = -$0.071 (observed)
- NO buy: 0.897 × 1 - 0.832 - fee = +$0.060

Testing:
1. Simple "buy NO on +1 offset" across all cities
2. Same across different offsets (+2, +3)
3. Per-city breakdown
4. Temporal robustness (within-OOS split)
5. Combined with iter 4 Seattle/Miami NBS-fav
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
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    def apply_offset(day, offset_buckets):
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        target = nbs_fav_idx + offset_buckets
        row = day[day["bucket_idx"] == target]
        return row.iloc[0] if len(row) == 1 else None

    def eval_fade(df, offset, min_yes_price=0.02, max_yes_price=0.35,
                  name="fade"):
        """Buy NO on the specified offset bucket.

        NO wins iff YES doesn't win (i.e., won_yes == 0).
        NO entry price = 1 - YES entry price.
        """
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            r = apply_offset(day, offset)
            if r is None:
                continue
            yes_price = float(r["entry_price"])
            if yes_price < min_yes_price or yes_price > max_yes_price:
                continue
            no_price = 1 - yes_price
            # NO wins when YES loses
            no_won = 1 - int(r["won_yes"])
            fee = FEE * no_price * (1 - no_price)  # same formula applied to NO
            pnl = float(no_won) - no_price - fee
            trades.append({"city": city, "date": md.date(),
                          "yes_price": yes_price, "no_price": no_price,
                          "yes_won": int(r["won_yes"]), "no_won": no_won,
                          "pnl": pnl})
        return pd.DataFrame(trades)

    def summarize(t, name):
        if len(t) == 0:
            print(f"  {name}: n=0")
            return None
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name}: n={len(t):>3}  hit={t.no_won.mean()*100:>5.1f}%  "
              f"per=${t.pnl.mean():>+.4f}  tot=${t.pnl.sum():>+.2f}  t={tstat:>+.2f}  "
              f"no_price=${t.no_price.mean():.3f}")
        return t

    # Test for different offsets
    print("=== Buy NO on +N offset bucket (fade strategy) — FULL Mar 11-Apr 10 ===")
    for off in (1, 2, 3):
        print(f"  offset=+{off}:")
        t = eval_fade(tbl, off, min_yes_price=0.005, max_yes_price=0.5,
                      name=f"+{off}")
        summarize(t, f"    yes price [0.005, 0.5]")
        t = eval_fade(tbl, off, min_yes_price=0.02, max_yes_price=0.3,
                      name=f"+{off}")
        summarize(t, f"    yes price [0.02, 0.3]")
        t = eval_fade(tbl, off, min_yes_price=0.05, max_yes_price=0.3,
                      name=f"+{off}")
        summarize(t, f"    yes price [0.05, 0.3]")

    # Focus on offset=+1 (biggest IS signal) and verify temporal robustness
    print("\n=== offset=+1 fade: within-OOS split ===")
    t_all = eval_fade(tbl, 1, min_yes_price=0.02, max_yes_price=0.35)
    split = date(2026, 3, 25)
    summarize(t_all[t_all.date <= split], "Mar 11-25")
    summarize(t_all[t_all.date > split], "Mar 26-Apr10")

    # Per-city for offset=+1
    print("\n=== offset=+1 fade per-city ===")
    if len(t_all) > 0:
        for city, g in t_all.groupby("city"):
            std = g.pnl.std() if len(g) > 1 else 0
            ts = g.pnl.mean() / (std / len(g)**0.5) if std > 0 else 0
            print(f"  {city:<18} n={len(g):>3}  hit={g.no_won.mean()*100:>5.1f}%  "
                  f"per=${g.pnl.mean():>+.4f}  tot=${g.pnl.sum():>+.2f}  t={ts:>+.2f}")

    # Weekly
    print("\n=== offset=+1 fade by week ===")
    if len(t_all) > 0:
        t_all["week"] = pd.to_datetime(t_all.date).dt.isocalendar().week
        for wk, g in t_all.groupby("week"):
            std = g.pnl.std() if len(g) > 1 else 0
            ts = g.pnl.mean() / (std / len(g)**0.5) if std > 0 else 0
            print(f"  Wk{wk}: n={len(g):>3}  hit={g.no_won.mean()*100:>5.1f}%  "
                  f"per=${g.pnl.mean():>+.3f}  tot=${g.pnl.sum():>+.2f}  t={ts:>+.2f}")

    # Try also offset=-1 (buying NO on bucket BELOW NBS fav)
    # This would be "NBS over-forecasts, so actual is often lower, fav-1 YES hits often,
    # so NO on fav-1 loses". Negative control.
    print("\n=== Negative control: offset=-1 (NO buy) ===")
    t_neg = eval_fade(tbl, -1, min_yes_price=0.02, max_yes_price=0.5)
    summarize(t_neg, "offset=-1 NO")

    # Also try +2 offset with tighter price range
    print("\n=== offset=+2 fade with tight price range ===")
    t2 = eval_fade(tbl, 2, min_yes_price=0.01, max_yes_price=0.20)
    summarize(t2, "offset=+2 NO")
    if len(t2) > 0:
        summarize(t2[t2.date <= split], "  Mar 11-25")
        summarize(t2[t2.date > split], "  Mar 26-Apr10")


if __name__ == "__main__":
    main()

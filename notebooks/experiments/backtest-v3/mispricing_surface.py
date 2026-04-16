"""Map the full mispricing surface.

For each bucket offset from NBS favorite, on consensus-tight days, compute:
  - actual hit rate (% of times YES wins)
  - market-implied probability (= YES midpoint)
  - mispricing gap (implied - actual)
  - potential PnL from buying NO on that bucket

Goal: identify ALL systematically mispriced buckets, not just the +1
we already exploit. If retail is bad at probability for deep tails,
"+3" might have bigger gap % even if per-trade $ is small.

Also: test market-favorite-based variants (offsets relative to highest
priced bucket, not NBS forecast).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

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
    for c in ("consensus_spread", "nbs_pred_max_f_f"):
        if c in tbl.columns:
            tbl = tbl.drop(columns=[c])
    tbl = tbl.merge(
        feat[["city", "local_date", "consensus_spread", "nbs_pred_max_f"]]
        .rename(columns={"local_date": "market_date"}),
        on=["city", "market_date"], how="left",
        suffixes=("", "_feat"),
    )
    tbl = tbl.dropna(subset=["consensus_spread", "nbs_pred_max_f"])
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    # For each (city, market_date) compute NBS favorite and collect per-bucket info
    rows = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        cs = float(day["consensus_spread"].iloc[0])
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        # market favorite (highest price)
        mkt_fav_idx = int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])
        for _, r in day.iterrows():
            rows.append({
                "city": city, "date": md.date(),
                "consensus_spread": cs,
                "bucket_idx": int(r["bucket_idx"]),
                "offset_nbs": int(r["bucket_idx"]) - nbs_fav_idx,
                "offset_mkt": int(r["bucket_idx"]) - mkt_fav_idx,
                "yes_price": float(r["entry_price"]),
                "won_yes": int(r["won_yes"]),
            })
    d = pd.DataFrame(rows)
    print(f"Total (bucket, day) rows: {len(d)}")

    def analyze(df, offset_col, cs_max=3.0, name=""):
        """For each offset, compute market-implied vs actual hit and mispricing."""
        sub = df[df.consensus_spread <= cs_max]
        print(f"\n=== {name} (cs ≤ {cs_max}°F) ===")
        print(f"{'offset':>7} {'n':>5}  {'actual_hit':>12} {'mkt_implied':>12} "
              f"{'gap':>8}  {'NO_edge/trade':>15}  {'YES_edge/trade':>15}")
        print("-" * 80)
        results = []
        for off in range(-4, 5):
            g = sub[sub[offset_col] == off]
            if len(g) < 10:
                continue
            actual_hit = g.won_yes.mean()
            mkt_implied = g.yes_price.mean()
            gap = mkt_implied - actual_hit  # positive = market over-prices YES (NO is underpriced)
            # Compute PnL for buy-NO and buy-YES at that offset
            no_prices = 1 - g.yes_price
            no_fees = FEE * no_prices * (1 - no_prices)
            no_wons = 1 - g.won_yes
            no_pnls = no_wons - no_prices - no_fees
            yes_fees = FEE * g.yes_price * (1 - g.yes_price)
            yes_pnls = g.won_yes - g.yes_price - yes_fees
            # Filter to YES in [0.005, 0.95] for reasonable trades
            valid = (g.yes_price >= 0.005) & (g.yes_price <= 0.95)
            if valid.sum() > 0:
                no_pnl = no_pnls[valid].mean()
                yes_pnl = yes_pnls[valid].mean()
            else:
                no_pnl = np.nan
                yes_pnl = np.nan
            results.append({
                "offset": off, "n": len(g),
                "actual_hit": actual_hit, "mkt_implied": mkt_implied,
                "gap": gap, "no_pnl": no_pnl, "yes_pnl": yes_pnl,
            })
            print(f"{off:>+7d} {len(g):>5}  {actual_hit*100:>10.1f}% {mkt_implied*100:>11.1f}%  "
                  f"{gap*100:>+6.1f}pp  ${no_pnl:>+13.4f}  ${yes_pnl:>+13.4f}")
        return pd.DataFrame(results)

    nbs_res = analyze(d, "offset_nbs", 3.0, "NBS-fav based")
    mkt_res = analyze(d, "offset_mkt", 3.0, "Market-fav based")

    # Also at no consensus filter (baseline)
    print("\n\n=== Same analysis WITHOUT consensus filter ===")
    analyze(d, "offset_nbs", 99.0, "NBS-fav, no filter")

    # Now: is there a bucket where YES (not NO) has edge?
    # And: compare tail buckets that are "deep away"

    # Also: across all cities and offsets, which buckets are most mispriced (gap%)?
    print("\n\n=== Most-mispriced (city, offset_nbs) combos under consensus filter ===")
    sub = d[d.consensus_spread <= 3.0]
    grid = sub.groupby(["city", "offset_nbs"]).agg(
        n=("won_yes", "count"),
        actual_hit=("won_yes", "mean"),
        mkt_implied=("yes_price", "mean"),
    ).reset_index()
    grid["gap_pp"] = (grid["mkt_implied"] - grid["actual_hit"]) * 100
    grid = grid[grid["n"] >= 5].sort_values("gap_pp", ascending=False)
    print("Top 15 (market over-prices YES = NO is underpriced):")
    print(grid.head(15).to_string(index=False))
    print("\nBottom 15 (market under-prices YES = YES is underpriced):")
    print(grid.tail(15).to_string(index=False))


if __name__ == "__main__":
    main()

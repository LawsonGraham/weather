"""Apr 11 holdout test: run the +1 offset NO strategy on Apr 11 resolved markets.

Apr 11 is after our backtest period (Mar 11-Apr 10). It's the only day
where we have:
- NBS forecast (in features.parquet)
- Actual outcome (markets resolved)
- Book data for some slugs (Apr 11 markets with post-resolution snapshots)

This is genuinely fresh data not used in strategy discovery.
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"

CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE = 0.05


def parse_bucket(title):
    m = re.match(r"^(\d+)-(\d+)°F$", title)
    if m:
        return (float(m[1]), float(m[2]), (int(m[1])+int(m[2]))/2)
    m = re.match(r"^(\d+)°F or below$", title)
    if m:
        return (float("-inf"), float(m[1]), float(int(m[1])-1))
    m = re.match(r"^(\d+)°F or higher$", title)
    if m:
        return (float(m[1]), float("inf"), float(int(m[1])+1))
    return (None, None, None)


def main():
    # Load Apr 11 resolved slugs
    con = duckdb.connect()
    slugs = con.execute(f"""
        SELECT slug, city, yes_token_id, group_item_threshold AS bucket_idx,
               group_item_title, outcome_prices, DATE(end_date) AS market_date,
               closed
        FROM '{REPO}/data/processed/polymarket_weather/markets.parquet'
        WHERE weather_tags ILIKE '%Daily Temperature%'
          AND DATE(end_date) = '2026-04-11'
          AND closed = true
    """).fetch_df()

    def won_yes(row):
        op = row["outcome_prices"]
        if op is None or len(op) != 2:
            return -1
        return int(op[0] == 1.0)
    slugs["won_yes"] = slugs.apply(won_yes, axis=1)
    slugs = slugs[slugs.won_yes >= 0]

    parsed = slugs["group_item_title"].apply(parse_bucket)
    slugs["bucket_low"] = parsed.apply(lambda t: t[0])
    slugs["bucket_high"] = parsed.apply(lambda t: t[1])
    slugs["bucket_center"] = parsed.apply(lambda t: t[2])
    slugs = slugs.dropna(subset=["bucket_center"])
    slugs["station"] = slugs["city"].map(CITY_TO_STATION)

    # Load price at entry time (20 UTC Apr 11)
    px = con.execute(f"""
        SELECT yes_token_id, timestamp, p_yes
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/**/*.parquet')
        WHERE DATE(timestamp) = '2026-04-11' AND EXTRACT(HOUR FROM timestamp) = 20
    """).fetch_df()
    px_map = dict(zip(px["yes_token_id"], px["p_yes"]))
    # Fallback: last price of the day
    px_all = con.execute(f"""
        SELECT yes_token_id, MAX(timestamp) AS last_ts, ANY_VALUE(p_yes) FILTER (WHERE timestamp IS NOT NULL) AS any_p
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/**/*.parquet')
        WHERE DATE(timestamp) <= '2026-04-11' AND EXTRACT(HOUR FROM timestamp) <= 20
        GROUP BY yes_token_id
    """).fetch_df()
    px_last = {}
    for _, r in px_all.iterrows():
        # Actually above returns the LAST p at last timestamp; let's redo with proper ORDER BY
        pass

    px_last_proper = con.execute(f"""
        WITH ranked AS (
            SELECT yes_token_id, timestamp, p_yes,
                   ROW_NUMBER() OVER (PARTITION BY yes_token_id ORDER BY timestamp DESC) rn
            FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/**/*.parquet')
            WHERE timestamp <= TIMESTAMPTZ '2026-04-11 21:00:00+0000'
              AND EXTRACT(HOUR FROM timestamp) <= 20
        )
        SELECT yes_token_id, p_yes FROM ranked WHERE rn = 1
    """).fetch_df()
    for _, r in px_last_proper.iterrows():
        px_last[r["yes_token_id"]] = r["p_yes"]

    slugs["yes_mid"] = slugs["yes_token_id"].map(
        lambda tid: px_map.get(tid, px_last.get(tid))
    )
    print(f"Apr 11: {len(slugs)} slugs, {slugs['yes_mid'].notna().sum()} with price")

    # Load NBS for Apr 11
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat_apr11 = feat[feat.local_date == "2026-04-11"].dropna(subset=["nbs_pred_max_f"])
    nbs_map = dict(zip(feat_apr11["station"], feat_apr11["nbs_pred_max_f"]))
    actual_map = dict(zip(feat_apr11["station"], feat_apr11["actual_max_f"]))
    print(f"NBS forecasts: {nbs_map}")
    print(f"Actual max:    {actual_map}")

    # For each (city) with Apr 11 markets, find NBS_fav and +1 offset bucket
    print()
    print("=== +1 offset NO trades on Apr 11 ===")
    trades = []
    for city, grp in slugs.groupby("city"):
        station = CITY_TO_STATION.get(city)
        if station not in nbs_map:
            continue
        nbs_pred = nbs_map[station]
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        diffs = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diffs.idxmin(), "bucket_idx"])
        target_idx = nbs_fav_idx + 1
        row = day[day["bucket_idx"] == target_idx]
        if row.empty or row.iloc[0]["yes_mid"] is None:
            continue
        r = row.iloc[0]
        yes_mid = float(r["yes_mid"]) if r["yes_mid"] is not None else None
        if yes_mid is None or yes_mid < 0.005 or yes_mid > 0.5:
            continue
        no_price = 1 - yes_mid
        no_won = 1 - int(r["won_yes"])
        fee = FEE * no_price * (1 - no_price)
        pnl = float(no_won) - no_price - fee
        trades.append({
            "city": city, "nbs_pred": nbs_pred, "actual": actual_map.get(station),
            "nbs_fav_bucket": nbs_fav_idx, "target_bucket": target_idx,
            "target_title": r["group_item_title"],
            "yes_mid": yes_mid, "no_price": no_price,
            "yes_won": int(r["won_yes"]), "no_won": no_won,
            "pnl": pnl,
        })
        print(f"  {city:<18} NBS={nbs_pred:>5.1f} actual={actual_map.get(station)!s:<5}  "
              f"+1bucket={r['group_item_title']:<15} YES_mid={yes_mid:.3f}  "
              f"yes_won={r['won_yes']}  no_pnl=${pnl:+.4f}")

    t = pd.DataFrame(trades)
    if len(t) > 0:
        std = t.pnl.std() if len(t) > 1 else 0
        ts = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print()
        print(f"Apr 11 holdout: n={len(t)}  hit={t.no_won.mean()*100:.1f}%  "
              f"per=${t.pnl.mean():+.4f}  tot=${t.pnl.sum():+.2f}  t={ts:+.2f}")
        print(f"  Cumulative PnL this day: ${t.pnl.sum():+.2f}")


if __name__ == "__main__":
    main()

"""Capacity analysis v2 — use MARKET favorite (highest-priced bucket) as
proxy for NBS favorite. Doesn't need fresh NBS data.

Approach:
1. For each (city, market_date) with book data:
   - From price_history, find the market-favorite bucket (highest price)
   - The +1 offset bucket = market_fav_idx + 1
2. Grab book snapshots near 20 UTC for the +1 bucket
3. Measure YES-bid depth = NO-ask depth at (1-price)
4. Aggregate per-day and overall capacity stats
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
BOOK_DIR = REPO / "data" / "raw" / "polymarket_book"


def parse_book_snapshots(slug_dir: Path, target_asset: str, target_ts: datetime,
                         window_hours: int = 2):
    """Find all book snapshots for target_asset within window_hours of target_ts."""
    snaps = []
    for f in sorted(slug_dir.glob("*.jsonl")):
        try:
            for line in f.read_text().splitlines():
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (m.get("event_type") or "") != "book":
                    continue
                if m.get("asset_id") != target_asset:
                    continue
                ts_str = m.get("_received_at", "")
                if not ts_str:
                    continue
                try:
                    snap_ts = datetime.strptime(
                        ts_str.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S.%f%z"
                    )
                except Exception:
                    continue
                delta = abs(snap_ts - target_ts)
                if delta > timedelta(hours=window_hours):
                    continue
                bids = sorted([(float(b["price"]), float(b["size"]))
                              for b in (m.get("bids") or [])], reverse=True)
                asks = sorted([(float(a["price"]), float(a["size"]))
                              for a in (m.get("asks") or [])])
                snaps.append((snap_ts, delta.total_seconds(), bids, asks))
        except Exception:
            continue
    return sorted(snaps, key=lambda x: x[1])


def main():
    con = duckdb.connect()
    markets = con.execute(f"""
        SELECT slug, city, yes_token_id, no_token_id,
               group_item_threshold AS bucket_idx,
               group_item_title,
               DATE(end_date) AS market_date
        FROM '{REPO}/data/processed/polymarket_weather/markets.parquet'
        WHERE weather_tags ILIKE '%Daily Temperature%'
    """).fetch_df()

    # Load hourly prices for bucket-favorite identification
    prices = con.execute(f"""
        SELECT yes_token_id, timestamp, p_yes
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/**/*.parquet')
        WHERE timestamp >= TIMESTAMPTZ '2026-04-13 00:00:00+0000'
          AND timestamp <= TIMESTAMPTZ '2026-04-14 23:00:00+0000'
    """).fetch_df()
    print(f"Prices data Apr 13-14: {len(prices)} rows")

    # Subset to recent markets with book data AVAILABLE
    book_slugs = {d.name for d in BOOK_DIR.iterdir() if d.is_dir()}
    # Filter: markets that have book data AND are in a reasonable date range
    markets = markets[markets["slug"].isin(book_slugs)].copy()
    markets["market_date"] = pd.to_datetime(markets["market_date"]).dt.date
    print(f"Markets with book data: {len(markets)}")

    # For each (city, market_date), find the market favorite and +1
    targets = []
    for (city, md), grp in markets.groupby(["city", "market_date"]):
        # Find highest price per bucket around 20 UTC
        prices_for_day = prices[prices["yes_token_id"].isin(grp["yes_token_id"])]
        if len(prices_for_day) == 0:
            continue
        # Latest price at or before 20 UTC on market_date
        target_ts = pd.Timestamp(datetime.combine(md, datetime.min.time(), tzinfo=UTC)) + pd.Timedelta(hours=20)
        prices_before = prices_for_day[
            pd.to_datetime(prices_for_day["timestamp"], utc=True) <= target_ts
        ]
        if len(prices_before) == 0:
            continue
        # Take latest price per token
        latest_px = prices_before.sort_values("timestamp").groupby(
            "yes_token_id", as_index=False).tail(1)
        # Merge into grp
        grp_px = grp.merge(
            latest_px[["yes_token_id", "p_yes"]], on="yes_token_id", how="left"
        )
        if grp_px["p_yes"].isna().all():
            continue
        # Highest YES price = market favorite
        mkt_fav_row = grp_px.loc[grp_px["p_yes"].idxmax()]
        mkt_fav_idx = int(mkt_fav_row["bucket_idx"])
        plus1 = grp_px[grp_px["bucket_idx"] == mkt_fav_idx + 1]
        if plus1.empty:
            continue
        r = plus1.iloc[0]
        # Also get plus2 for comparison
        plus2 = grp_px[grp_px["bucket_idx"] == mkt_fav_idx + 2]
        targets.append({
            "city": city, "market_date": md, "mkt_fav_idx": mkt_fav_idx,
            "mkt_fav_price": float(mkt_fav_row["p_yes"]),
            "plus1_slug": r["slug"], "plus1_yes_price": float(r["p_yes"]),
            "plus1_title": r["group_item_title"],
            "plus1_yes_token_id": r["yes_token_id"],
            "plus1_no_token_id": r["no_token_id"],
        })
    targets_df = pd.DataFrame(targets)
    print(f"Qualifying +1 offset buckets: {len(targets_df)}")

    # Analyze each target's book depth
    print()
    print("=== Book depth analysis for +1 offset buckets (Apr 13-14) ===")
    print(f"{'date':<11} {'city':<16} {'bucket':<17} "
          f"{'yes_p':>6} {'no_ask':>7} "
          f"{'@best':>6} {'<=1c':>6} {'<=2c':>7} {'<=5c':>7} "
          f"{'$cap_2c':>8} {'snap_Δ':>8}")
    print("-" * 116)

    rows = []
    for _, t in targets_df.iterrows():
        slug_dir = BOOK_DIR / t["plus1_slug"]
        if not slug_dir.exists():
            continue
        target_ts = datetime.combine(t["market_date"], datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
        snaps = parse_book_snapshots(slug_dir, t["plus1_yes_token_id"], target_ts)
        if not snaps:
            continue
        # Use the snapshot closest to 20 UTC
        snap_ts, delta_s, bids, asks = snaps[0]
        if not bids:
            continue
        # NO-ask capacity = YES-bid depth at various slippage levels
        yes_top_bid = bids[0][0]
        yes_top_bid_size = bids[0][1]
        no_ask_best = 1 - yes_top_bid
        # Depth walking down bids (= walking up NO-ask)
        depth_best = yes_top_bid_size
        depth_1c = sum(s for p, s in bids if p >= yes_top_bid - 0.01)
        depth_2c = sum(s for p, s in bids if p >= yes_top_bid - 0.02)
        depth_5c = sum(s for p, s in bids if p >= yes_top_bid - 0.05)
        cap_2c_usd = depth_2c * no_ask_best
        rows.append({
            "date": str(t["market_date"]),
            "city": t["city"],
            "bucket": t["plus1_title"],
            "yes_p": t["plus1_yes_price"],
            "no_ask": no_ask_best,
            "depth_best": depth_best,
            "depth_1c": depth_1c,
            "depth_2c": depth_2c,
            "depth_5c": depth_5c,
            "cap_2c_usd": cap_2c_usd,
            "snap_delta_s": delta_s,
        })
        print(f"{t['market_date']!s:<11} {t['city']:<16} {t['plus1_title']:<17} "
              f"{t['plus1_yes_price']:>6.3f} {no_ask_best:>7.3f} "
              f"{depth_best:>6.0f} {depth_1c:>6.0f} {depth_2c:>7.0f} {depth_5c:>7.0f} "
              f"${cap_2c_usd:>7.0f} {delta_s:>7.0f}s")

    if not rows:
        print("No usable book snapshots found.")
        return

    t = pd.DataFrame(rows)

    # Per-day summary
    print()
    print("=== Daily aggregate capacity ===")
    for date_, g in t.groupby("date"):
        print(f"  {date_}: {len(g)} qualifying +1 buckets")
        print(f"    total depth @ best ask: {g.depth_best.sum():>6.0f} shares "
              f"(${g.depth_best.sum() * g.no_ask.mean():.0f} notional)")
        print(f"    total depth within 2¢: {g.depth_2c.sum():>6.0f} shares "
              f"(${g.cap_2c_usd.sum():.0f} notional)")
        print(f"    total depth within 5¢: {g.depth_5c.sum():>6.0f} shares")

    print()
    print("=== Summary statistics ===")
    print(f"n qualifying slugs: {len(t)}")
    print(f"Mean NO price: {t.no_ask.mean():.3f}")
    print(f"Mean YES price (= +1 bucket): {t.yes_p.mean():.3f}")
    print()
    print(f"Depth at BEST NO-ask:")
    print(f"  mean: {t.depth_best.mean():.0f} shares  (${t.depth_best.mean() * t.no_ask.mean():.0f})")
    print(f"  median: {t.depth_best.median():.0f} shares")
    print(f"  p25-p75: {t.depth_best.quantile(0.25):.0f} - {t.depth_best.quantile(0.75):.0f}")
    print()
    print(f"Depth within 2¢ of best ask:")
    print(f"  mean: {t.depth_2c.mean():.0f} shares  (${t.cap_2c_usd.mean():.0f})")
    print(f"  median: {t.depth_2c.median():.0f} shares")
    print()
    print(f"Depth within 5¢ (aggressive scale-up):")
    print(f"  mean: {t.depth_5c.mean():.0f} shares")
    print(f"  median: {t.depth_5c.median():.0f} shares")

    # Answer the user's question concretely
    avg_qualifying_per_day = t.groupby("date").size().mean()
    print()
    print("=== Capacity for Strategy C' deployment ===")
    print(f"Avg qualifying +1 buckets per day (market-fav proxy): {avg_qualifying_per_day:.1f}")
    total_daily_depth_2c = t.groupby("date").depth_2c.sum().mean()
    total_daily_cap_2c = t.groupby("date").cap_2c_usd.sum().mean()
    print(f"Avg total depth within 2¢ per day: {total_daily_depth_2c:.0f} shares "
          f"(${total_daily_cap_2c:.0f} capital per day)")
    # Conservative: fill at 25% of depth to avoid moving market
    conservative = total_daily_cap_2c * 0.25
    print(f"Conservative fill (25% of depth): ${conservative:.0f}/day capital deployable")


if __name__ == "__main__":
    main()

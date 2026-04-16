"""Capacity analysis v3 — extract prices directly from book snapshots.

No reliance on external price parquet. For each slug with book data:
1. Get latest book snapshot near 20 UTC
2. YES mid = (best_bid + best_ask) / 2

For each (city, market_date) group:
1. Rank slugs by YES mid → find market favorite
2. The +1 bucket = fav_bucket_idx + 1
3. Analyze +1 bucket's book depth for NO-ask capacity
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


def get_book_near_20z(slug_dir: Path, yes_token_id: str):
    """Return list of book snapshots (YES side) within 2h of 20 UTC."""
    # Figure out which market_date this slug is for from files
    results = []
    for f in sorted(slug_dir.glob("*.jsonl")):
        fname = f.name  # e.g. 2026-04-13-20.jsonl
        try:
            date_part, hour_part = fname[:-6], int(fname[-7:-6])
            actual_hour = int(fname.split('-')[-1].split('.')[0])
            file_date = fname[:10]
            if actual_hour < 18 or actual_hour > 22:
                continue  # only 18-22 UTC files
        except Exception:
            continue
        try:
            for line in f.read_text().splitlines():
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (m.get("event_type") or "") != "book":
                    continue
                if m.get("asset_id") != yes_token_id:
                    continue
                ts_str = m.get("_received_at", "")
                try:
                    snap_ts = datetime.strptime(
                        ts_str.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S.%f%z"
                    )
                except Exception:
                    continue
                bids = sorted([(float(b["price"]), float(b["size"]))
                              for b in (m.get("bids") or [])], reverse=True)
                asks = sorted([(float(a["price"]), float(a["size"]))
                              for a in (m.get("asks") or [])])
                results.append((snap_ts, bids, asks, file_date))
        except Exception:
            continue
    return results


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
    book_slugs = {d.name for d in BOOK_DIR.iterdir() if d.is_dir()}
    markets = markets[markets["slug"].isin(book_slugs)].copy()
    markets["market_date"] = pd.to_datetime(markets["market_date"]).dt.date
    print(f"Markets with book data: {len(markets)}")

    # For each slug, extract book snapshot near 20 UTC on its market_date
    print("Extracting book snapshots per slug...")
    slug_books = {}
    for _, r in markets.iterrows():
        slug_dir = BOOK_DIR / r["slug"]
        snaps = get_book_near_20z(slug_dir, r["yes_token_id"])
        if not snaps:
            continue
        # Prefer snapshot on the market_date itself
        target_date = str(r["market_date"])
        on_date_snaps = [s for s in snaps if s[3] == target_date]
        use_snaps = on_date_snaps if on_date_snaps else snaps
        # Get snap closest to 20 UTC
        target_20z = datetime.combine(r["market_date"], datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
        use_snaps.sort(key=lambda s: abs(s[0] - target_20z))
        snap_ts, bids, asks, fdate = use_snaps[0]
        if not bids or not asks:
            continue
        slug_books[r["slug"]] = {
            "snap_ts": snap_ts,
            "bids": bids,
            "asks": asks,
            "yes_mid": (bids[0][0] + asks[0][0]) / 2,
            "yes_bid": bids[0][0],
            "yes_ask": asks[0][0],
            "market_date": r["market_date"],
            "city": r["city"],
            "bucket_idx": int(r["bucket_idx"]),
            "title": r["group_item_title"],
        }
    print(f"Slugs with usable book snapshots: {len(slug_books)}")

    # Group by (city, market_date), find market favorite and +1
    by_md = defaultdict(list)
    for slug, info in slug_books.items():
        key = (info["city"], info["market_date"])
        by_md[key].append((slug, info))

    targets = []
    for (city, md), slugs_list in by_md.items():
        if len(slugs_list) < 2:
            continue
        # Rank by yes_mid — market favorite = highest
        slugs_list.sort(key=lambda s: -s[1]["yes_mid"])
        mkt_fav_slug, mkt_fav_info = slugs_list[0]
        mkt_fav_idx = mkt_fav_info["bucket_idx"]
        # Find +1
        plus1 = [s for s in slugs_list if s[1]["bucket_idx"] == mkt_fav_idx + 1]
        if not plus1:
            continue
        plus1_slug, plus1_info = plus1[0]
        targets.append({
            "city": city, "market_date": md,
            "mkt_fav_idx": mkt_fav_idx,
            "mkt_fav_title": mkt_fav_info["title"],
            "mkt_fav_price": mkt_fav_info["yes_mid"],
            "plus1_slug": plus1_slug,
            "plus1_title": plus1_info["title"],
            "plus1_info": plus1_info,
        })
    print(f"Qualifying +1 offset buckets: {len(targets)}")
    print()
    print("=== Book depth on +1 offset buckets ===")
    print(f"{'date':<11} {'city':<16} {'fav_bucket':<13} {'+1_bucket':<15} "
          f"{'yes_p':>6} {'no_ask':>7} "
          f"{'@best':>7} {'<=1c':>7} {'<=2c':>7} {'<=5c':>7} {'$cap_2c':>9}")
    print("-" * 130)

    rows = []
    for t in targets:
        p = t["plus1_info"]
        bids = p["bids"]
        if not bids:
            continue
        yes_top_bid = bids[0][0]
        yes_top_bid_size = bids[0][1]
        no_ask_best = 1 - yes_top_bid
        depth_best = yes_top_bid_size
        depth_1c = sum(s for px, s in bids if px >= yes_top_bid - 0.01)
        depth_2c = sum(s for px, s in bids if px >= yes_top_bid - 0.02)
        depth_5c = sum(s for px, s in bids if px >= yes_top_bid - 0.05)
        cap_2c = depth_2c * no_ask_best
        rows.append({
            "date": str(t["market_date"]),
            "city": t["city"],
            "fav_bucket": t["mkt_fav_title"],
            "plus1_bucket": t["plus1_title"],
            "yes_p": p["yes_mid"],
            "no_ask": no_ask_best,
            "depth_best": depth_best,
            "depth_1c": depth_1c,
            "depth_2c": depth_2c,
            "depth_5c": depth_5c,
            "cap_2c_usd": cap_2c,
        })
        print(f"{str(t['market_date']):<11} {t['city']:<16} "
              f"{t['mkt_fav_title']:<13} {t['plus1_title']:<15} "
              f"{p['yes_mid']:>6.3f} {no_ask_best:>7.3f} "
              f"{depth_best:>7.0f} {depth_1c:>7.0f} {depth_2c:>7.0f} {depth_5c:>7.0f} "
              f"${cap_2c:>8.0f}")

    if not rows:
        return

    t = pd.DataFrame(rows)

    # Daily aggregate
    print()
    print("=== Daily aggregate capacity ===")
    for date_, g in t.groupby("date"):
        tot_best = g.depth_best.sum()
        tot_2c = g.depth_2c.sum()
        tot_5c = g.depth_5c.sum()
        cap_2c = (g.depth_2c * g.no_ask).sum()
        cap_5c = (g.depth_5c * g.no_ask).sum()
        print(f"  {date_}: {len(g)} qualifying +1 buckets")
        print(f"    Total NO-ask depth: @best={tot_best:>5.0f}  "
              f"<=1c={g.depth_1c.sum():>5.0f}  <=2c={tot_2c:>5.0f}  <=5c={tot_5c:>5.0f} shares")
        print(f"    Capital deployable: <=2c=${cap_2c:>5.0f}  <=5c=${cap_5c:>5.0f}")

    print()
    print("=== Summary: Strategy C' deployable capacity ===")
    print(f"Avg +1 buckets per day: {t.groupby('date').size().mean():.1f}")
    print(f"Mean YES price of +1 bucket: {t.yes_p.mean():.3f}")
    print(f"Mean NO-ask price: {t.no_ask.mean():.3f}")
    print()
    print(f"Per-bucket depth distribution:")
    for col, label in [("depth_best", "at best NO-ask"),
                       ("depth_1c", "within 1¢ of best"),
                       ("depth_2c", "within 2¢ of best"),
                       ("depth_5c", "within 5¢ of best")]:
        print(f"  {label:<22}: median={t[col].median():>5.0f}  mean={t[col].mean():>5.0f}  "
              f"p90={t[col].quantile(0.9):>5.0f} shares")

    print()
    print(f"=== Daily deployable capacity (across qualifying +1 buckets) ===")
    daily = t.groupby("date").agg(
        n=("city", "count"),
        depth_best_total=("depth_best", "sum"),
        depth_2c_total=("depth_2c", "sum"),
        depth_5c_total=("depth_5c", "sum"),
    ).reset_index()
    daily["usd_best"] = t.groupby("date").apply(lambda g: (g.depth_best * g.no_ask).sum(), include_groups=False).values
    daily["usd_2c"] = t.groupby("date").apply(lambda g: (g.depth_2c * g.no_ask).sum(), include_groups=False).values
    daily["usd_5c"] = t.groupby("date").apply(lambda g: (g.depth_5c * g.no_ask).sum(), include_groups=False).values
    print(daily.to_string(index=False))

    # Conservative recommendation
    print()
    print("=== Recommended deployment scale ===")
    avg_usd_2c = daily["usd_2c"].mean()
    avg_usd_5c = daily["usd_5c"].mean()
    # Conservative: 20-30% of within-2c depth to avoid material slippage
    conservative_daily = avg_usd_2c * 0.25
    aggressive_daily = avg_usd_5c * 0.25
    print(f"Avg daily depth within 2¢:  ${avg_usd_2c:>7.0f}")
    print(f"Avg daily depth within 5¢:  ${avg_usd_5c:>7.0f}")
    print()
    print(f"Conservative (25% of <=2¢ depth): ~${conservative_daily:.0f}/day capital")
    print(f"Expected daily PnL at this scale: ~${conservative_daily * 0.083 / (t.no_ask.mean()):.2f}")
    print(f"  (= capital × backtest_edge/no_price)")
    print()
    print(f"Aggressive (25% of <=5¢ depth): ~${aggressive_daily:.0f}/day capital")
    print(f"Expected daily PnL at this scale: ~${aggressive_daily * 0.083 / (t.no_ask.mean()):.2f}")


if __name__ == "__main__":
    main()

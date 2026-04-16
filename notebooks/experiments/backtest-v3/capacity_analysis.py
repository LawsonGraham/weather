"""Real capacity analysis for Strategy C' using book JSONL.

Compute: at a typical +1 offset NO entry, how many shares can we fill
- at the best NO ask
- within 1¢ of best ask
- within 2¢ of best ask
- and what's the next ~5 price levels of depth

Key identity: NO-ask at price p comes from YES-bid at (1-p).
So buying NO at $0.85 matches against YES-bid at $0.15.
The YES book's BID side depth IS our NO-ask depth (at 1-price).

We want to measure:
1. Per-slug: how much YES-bid depth exists at entry time?
2. Daily aggregate: on a typical trading day (3-4 qualifying slugs),
   what's the total $ capacity?
3. Realistic: with slippage tolerance of 1-2¢, what's the safe fill size?
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
BOOK_DIR = REPO / "data" / "raw" / "polymarket_book"
V3 = REPO / "data" / "processed" / "backtest_v3"


def parse_book_snapshots(slug_dir: Path):
    """Yield (snap_ts, asset_id, bids, asks) from every book msg in slug dir."""
    for f in sorted(slug_dir.glob("*.jsonl")):
        try:
            for line in f.read_text().splitlines():
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = m.get("event_type") or ""
                if et != "book":
                    continue
                ts = m.get("_received_at", "")
                asset_id = m.get("asset_id")
                bids = sorted([(float(b["price"]), float(b["size"]))
                              for b in (m.get("bids") or [])], reverse=True)
                asks = sorted([(float(a["price"]), float(a["size"]))
                              for a in (m.get("asks") or [])])
                yield ts, asset_id, bids, asks
        except Exception:
            continue


def nearest_book(slug_dir: Path, target_ts: datetime, yes_token_id: str):
    """Find the book snapshot with asset_id matching yes_token_id nearest to target_ts.

    Returns (bids, asks, snap_ts, delta_seconds).
    """
    best = None
    best_delta = timedelta(days=999)
    for ts_str, asset_id, bids, asks in parse_book_snapshots(slug_dir):
        if asset_id != yes_token_id:
            continue
        try:
            snap_ts = datetime.strptime(
                ts_str.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S.%f%z"
            )
        except Exception:
            continue
        delta = abs(snap_ts - target_ts)
        if delta < best_delta:
            best_delta = delta
            best = (bids, asks, snap_ts, delta.total_seconds())
    return best


def main():
    # 1. Identify which slugs are +1 offset buckets
    con = duckdb.connect()
    markets = con.execute(f"""
        SELECT slug, city, yes_token_id, no_token_id,
               group_item_threshold AS bucket_idx,
               group_item_title,
               DATE(end_date) AS market_date
        FROM '{REPO}/data/processed/polymarket_weather/markets.parquet'
        WHERE weather_tags ILIKE '%Daily Temperature%'
          AND DATE(end_date) BETWEEN '2026-04-13' AND '2026-04-15'
    """).fetch_df()
    print(f"Total slugs Apr 13-15: {len(markets)}")

    # Load features for NBS forecast + consensus spread
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat = feat.dropna(subset=["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"])
    feat["consensus_spread"] = (
        feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].max(axis=1)
        - feat[["nbs_pred_max_f", "gfs_pred_max_f", "hrrr_max_t_f"]].min(axis=1)
    )
    CITY_TO_STATION = {
        "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
        "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
        "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
    }
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)

    # Parse bucket
    import re
    def parse_bucket(title):
        m = re.match(r"^(\d+)-(\d+)°F$", title)
        if m: return (float(m[1]), float(m[2]), (int(m[1])+int(m[2]))/2)
        m = re.match(r"^(\d+)°F or below$", title)
        if m: return (float("-inf"), float(m[1]), float(int(m[1])-1))
        m = re.match(r"^(\d+)°F or higher$", title)
        if m: return (float(m[1]), float("inf"), float(int(m[1])+1))
        return (None, None, None)
    parsed = markets["group_item_title"].apply(parse_bucket)
    markets["bucket_center"] = parsed.apply(lambda t: t[2])
    markets = markets.dropna(subset=["bucket_center"])

    # For each (city, market_date) compute NBS_fav bucket and +1 bucket
    targets = []
    for (city, md), grp in markets.groupby(["city", "market_date"]):
        nbs_row = feat[(feat.city == city)
                      & (feat.local_date == pd.Timestamp(md))]
        if nbs_row.empty:
            continue
        nbs_pred = float(nbs_row["nbs_pred_max_f"].iloc[0])
        cs = float(nbs_row["consensus_spread"].iloc[0])
        if cs > 3.0:
            continue  # not a tradeable day per Strategy C'
        diffs = (grp["bucket_center"] - nbs_pred).abs()
        fav_idx = int(grp.loc[diffs.idxmin(), "bucket_idx"])
        plus1 = grp[grp["bucket_idx"] == fav_idx + 1]
        if plus1.empty:
            continue
        r = plus1.iloc[0]
        targets.append({
            "slug": r["slug"],
            "city": city,
            "market_date": md,
            "nbs_pred": nbs_pred,
            "consensus_spread": cs,
            "fav_idx": fav_idx,
            "plus1_idx": fav_idx + 1,
            "plus1_title": r["group_item_title"],
            "yes_token_id": r["yes_token_id"],
            "no_token_id": r["no_token_id"],
        })
    targets_df = pd.DataFrame(targets)
    print(f"Qualifying +1 offset slugs (cs ≤ 3°F, Apr 13-15): {len(targets_df)}")
    print(targets_df[["city", "market_date", "consensus_spread", "plus1_title"]].to_string())

    # For each target, look up book data at 20 UTC of market_date
    print()
    print("=== Book depth analysis per qualifying +1 slug ===")
    print(f"{'date':<11} {'city':<15} {'bucket':<13} {'NO_ask':>7} {'depth_at_ask':>13} "
          f"{'depth_<=1c':>12} {'depth_<=2c':>12} {'5lvl_depth':>11} {'$cap_2c':>8}")
    print("-" * 110)

    totals = []
    for _, t in targets_df.iterrows():
        slug_dir = BOOK_DIR / t["slug"]
        if not slug_dir.exists():
            continue
        target_ts = datetime.combine(t["market_date"], datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
        snap = nearest_book(slug_dir, target_ts, t["yes_token_id"])
        if snap is None:
            continue
        bids, asks, snap_ts, delta_s = snap
        if not bids:
            continue
        # YES bids → NO asks (at 1-price)
        # To buy NO at best NO ask = 1 - best_yes_bid
        yes_top_bid, yes_top_bid_size = bids[0]
        no_ask_best = 1 - yes_top_bid

        # Depth: for each YES bid level, compute NO-ask price + cumulative size
        depth_at_best = yes_top_bid_size
        depth_1c = sum(s for p, s in bids if p >= yes_top_bid - 0.01)
        depth_2c = sum(s for p, s in bids if p >= yes_top_bid - 0.02)
        depth_5lvl = sum(s for p, s in bids[:5])

        cap_2c_usd = depth_2c * no_ask_best  # capital needed to fill this many shares

        totals.append({
            "date": str(t["market_date"]),
            "city": t["city"],
            "bucket": t["plus1_title"],
            "no_ask": no_ask_best,
            "depth_at_best": depth_at_best,
            "depth_1c": depth_1c,
            "depth_2c": depth_2c,
            "depth_5lvl": depth_5lvl,
            "cap_2c_usd": cap_2c_usd,
            "snap_delta_s": delta_s,
        })
        print(f"{t['market_date']!s:<11} {t['city']:<15} {t['plus1_title']:<13} "
              f"{no_ask_best:>7.3f} {depth_at_best:>13.0f} {depth_1c:>12.0f} "
              f"{depth_2c:>12.0f} {depth_5lvl:>11.0f} ${cap_2c_usd:>7.0f}")

    # Daily aggregate
    if totals:
        t = pd.DataFrame(totals)
        print()
        print("=== Per-day aggregate capacity (cs ≤ 3°F qualifying days) ===")
        for date_, g in t.groupby("date"):
            tot_depth_best = g["depth_at_best"].sum()
            tot_depth_2c = g["depth_2c"].sum()
            tot_cap = g["cap_2c_usd"].sum()
            print(f"  {date_}: {len(g)} qualifying trades, "
                  f"total depth at best: {tot_depth_best:.0f} shares, "
                  f"within 2¢: {tot_depth_2c:.0f} shares, "
                  f"${tot_cap:.0f} capital")

        print()
        print("=== Summary across all qualifying slugs ===")
        print(f"Mean depth at best NO ask: {t.depth_at_best.mean():.0f} shares (${t.depth_at_best.mean() * t.no_ask.mean():.0f})")
        print(f"Median depth at best: {t.depth_at_best.median():.0f} shares")
        print(f"Mean depth within 2¢: {t.depth_2c.mean():.0f} shares (${t.cap_2c_usd.mean():.0f})")
        print(f"Median depth within 2¢: {t.depth_2c.median():.0f} shares")
        print(f"Mean top-5-level depth: {t.depth_5lvl.mean():.0f} shares")


if __name__ == "__main__":
    main()

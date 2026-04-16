"""Measure typical YES spread from book data to estimate real-ask impact on edge.

Apr 11 is the only resolved day with both book data and outcomes. But even
that's limited. Let me measure the SPREAD distribution on all book data
(Apr 13-14) and estimate how much the backtest's midpoint assumption overstates.

For +1 offset bucket: YES typically priced $0.05-0.20.
If YES spread is ~2¢, then:
- YES_mid = $0.15
- YES_bid = $0.14, YES_ask = $0.16
- NO_mid = $0.85
- NO_bid = $0.84 (1-YES_ask)
- NO_ask = $0.86 (1-YES_bid)

So buying NO at real ask ($0.86) vs mid ($0.85) costs 1¢ extra.
Per-trade PnL would drop by ~$0.01 from our $0.055 backtest.
Still positive by $0.045/trade.
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import defaultdict
import statistics

REPO = Path("/Users/lawsongraham/git/weather")
BOOK_DIR = REPO / "data" / "raw" / "polymarket_book"


def collect_spreads(slug_dir: Path, limit_snapshots: int = 20):
    """Sample book snapshots from this slug, extract spreads."""
    spreads = []
    bids_tops = []
    asks_tops = []
    snap_count = 0
    for f in sorted(slug_dir.glob("*.jsonl")):
        if snap_count >= limit_snapshots:
            break
        try:
            lines = f.read_text().splitlines()
            for line in lines[:500]:  # sample from each hour file
                if snap_count >= limit_snapshots:
                    break
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = m.get("event_type") or m.get("type") or ""
                if et != "book":
                    continue
                bids = m.get("bids") or []
                asks = m.get("asks") or []
                if not bids or not asks:
                    continue
                bid_prices = sorted([float(b["price"]) for b in bids if "price" in b], reverse=True)
                ask_prices = sorted([float(a["price"]) for a in asks if "price" in a])
                if not bid_prices or not ask_prices:
                    continue
                top_bid = bid_prices[0]
                top_ask = ask_prices[0]
                spread = top_ask - top_bid
                # Skip nonsense
                if spread < 0 or spread > 0.99:
                    continue
                spreads.append(spread)
                bids_tops.append(top_bid)
                asks_tops.append(top_ask)
                snap_count += 1
        except Exception:
            continue
    return spreads, bids_tops, asks_tops


def main():
    # Classify slugs by approximate price level based on bucket naming
    # Very rough: "70-71f" style are 2F ranges, "or higher" / "or below" are tails
    slug_dirs = [d for d in BOOK_DIR.iterdir() if d.is_dir()]
    print(f"Slugs with book data: {len(slug_dirs)}")

    # For each slug, collect a few snapshots
    all_data = []
    for i, d in enumerate(slug_dirs):
        spreads, bids, asks = collect_spreads(d, limit_snapshots=30)
        if not spreads:
            continue
        all_data.append({
            "slug": d.name,
            "n_snaps": len(spreads),
            "mean_bid": statistics.mean(bids),
            "mean_ask": statistics.mean(asks),
            "mean_spread": statistics.mean(spreads),
            "median_spread": statistics.median(spreads),
        })

    print(f"Slugs analyzed: {len(all_data)}")
    print()

    # Group by approximate price level
    by_bucket_class = defaultdict(list)
    for d in all_data:
        mid = (d["mean_bid"] + d["mean_ask"]) / 2
        if mid < 0.05:
            cls = "deep_tail (<0.05)"
        elif mid < 0.15:
            cls = "near_tail (0.05-0.15)"
        elif mid < 0.30:
            cls = "fav-plus-1 (0.15-0.30)"
        elif mid < 0.50:
            cls = "adjacent-fav (0.30-0.50)"
        elif mid < 0.80:
            cls = "favorite (0.50-0.80)"
        else:
            cls = "deep_fav (>0.80)"
        by_bucket_class[cls].append(d)

    print("=== Spread statistics by price tier ===")
    print(f"{'tier':<24} {'n_slugs':>8} {'mean_spread':>13} {'median_spread':>15} "
          f"{'typical_mid':>12}")
    for cls in ("deep_tail (<0.05)", "near_tail (0.05-0.15)",
                "fav-plus-1 (0.15-0.30)", "adjacent-fav (0.30-0.50)",
                "favorite (0.50-0.80)", "deep_fav (>0.80)"):
        items = by_bucket_class[cls]
        if not items:
            continue
        spreads = [d["mean_spread"] for d in items]
        mids = [(d["mean_bid"] + d["mean_ask"]) / 2 for d in items]
        print(f"  {cls:<22} {len(items):>8} {statistics.mean(spreads):>13.4f} "
              f"{statistics.median(spreads):>15.4f} {statistics.mean(mids):>12.3f}")

    # For the +1 offset strategy: typical YES_mid ~ $0.15 (from iter 5 backtest data)
    print()
    print("=== Estimated impact on +1 offset NO strategy ===")
    relevant = by_bucket_class["fav-plus-1 (0.15-0.30)"] + by_bucket_class["near_tail (0.05-0.15)"]
    if relevant:
        spreads = [d["mean_spread"] for d in relevant]
        mean_spread = statistics.mean(spreads)
        median_spread = statistics.median(spreads)
        print(f"  Typical YES spread at +1 offset prices: mean={mean_spread:.4f}, "
              f"median={median_spread:.4f}")

        # Original backtest: +$0.055/trade at mid
        # If we pay spread/2 extra on buy, PnL drops by spread/2
        backtest_pnl = 0.0546
        realistic_pnl = backtest_pnl - mean_spread / 2
        print(f"  Backtest per-trade PnL (midpoint): ${backtest_pnl:+.4f}")
        print(f"  Realistic per-trade PnL (pay half spread): ${realistic_pnl:+.4f}")
        print(f"  If pay FULL spread (worst case): ${backtest_pnl - mean_spread:+.4f}")

    # Also for favorite (strategy A)
    print()
    print("=== Estimated impact on Strategy A (buy fav YES) ===")
    relevant = by_bucket_class["favorite (0.50-0.80)"] + by_bucket_class["adjacent-fav (0.30-0.50)"]
    if relevant:
        spreads = [d["mean_spread"] for d in relevant]
        mean_spread = statistics.mean(spreads)
        backtest_pnl = 0.1257
        realistic_pnl = backtest_pnl - mean_spread / 2
        print(f"  Typical spread at favorite prices: {mean_spread:.4f}")
        print(f"  Backtest per-trade PnL (mid): ${backtest_pnl:+.4f}")
        print(f"  Realistic (pay half-spread): ${realistic_pnl:+.4f}")

    # Best / worst slugs
    print()
    print("=== Tightest-spread slugs (top 5) ===")
    all_data.sort(key=lambda d: d["mean_spread"])
    for d in all_data[:5]:
        print(f"  {d['slug'][:70]:<70}  spread=${d['mean_spread']:.4f}  mid=${(d['mean_bid']+d['mean_ask'])/2:.3f}")

    print()
    print("=== Widest-spread slugs (top 5) ===")
    for d in all_data[-5:]:
        print(f"  {d['slug'][:70]:<70}  spread=${d['mean_spread']:.4f}  mid=${(d['mean_bid']+d['mean_ask'])/2:.3f}")


if __name__ == "__main__":
    main()

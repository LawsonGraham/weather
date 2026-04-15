"""Market depth estimation from book JSONL recorder.

Caveat: book recorder started 2026-04-13, which is AFTER the backtest
OOS window (Apr 1-10). Cannot directly estimate depth for trades in
the IS/OOS period. This analysis reports typical depth on Apr 13-14
slugs at the kinds of price levels our strategies would trade at.
Results are indicative, not definitive.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

BOOK_DIR = Path("/Users/lawsongraham/git/weather/data/raw/polymarket_book")


def analyze_slug_depth(slug_dir: Path) -> dict:
    """Extract L2 book snapshots + last_trade_price events from one slug's
    JSONL files. Return per-price-bucket stats.
    """
    books = []
    fills = []
    for f in sorted(slug_dir.glob("*.jsonl")):
        try:
            for line in f.read_text().splitlines():
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = m.get("event_type") or m.get("type") or ""
                if et == "book":
                    bids = m.get("bids") or []
                    asks = m.get("asks") or []
                    # asks sorted ascending by price
                    ask_prices = sorted([(float(a["price"]), float(a["size"])) for a in asks])
                    if not ask_prices:
                        continue
                    best_ask = ask_prices[0][0]
                    # Depth within 2c of best_ask (simulated taker impact)
                    depth_2c = sum(s for p, s in ask_prices if p <= best_ask + 0.02)
                    depth_total = sum(s for _, s in ask_prices)
                    books.append({
                        "ts": m.get("_received_at"),
                        "best_ask": best_ask,
                        "depth_at_ask": ask_prices[0][1],
                        "depth_within_2c": depth_2c,
                        "depth_total_ask": depth_total,
                    })
                elif et == "last_trade_price":
                    sz = m.get("size")
                    if sz is not None:
                        fills.append({
                            "ts": m.get("_received_at"),
                            "price": float(m.get("price", 0)),
                            "size": float(sz),
                            "side": m.get("side"),
                        })
        except Exception:
            continue
    return {"books": books, "fills": fills, "n_hours": len(list(slug_dir.glob("*.jsonl")))}


def main():
    # Focus on buckets at various price levels
    slug_dirs = [d for d in BOOK_DIR.iterdir() if d.is_dir()]
    print(f"Slugs recorded: {len(slug_dirs)}")

    # Analyze 1 representative slug from each price tier
    # Price tiers: tail (bucket 0/10), near-tail (1/9), mid (3-7), favorite
    # Pick slugs from various cities on Apr 13
    sample_slugs = [
        "highest-temperature-in-nyc-on-april-13-2026-60-61f",  # potential fav
        "highest-temperature-in-nyc-on-april-13-2026-62-63f",  # fav+1
        "highest-temperature-in-nyc-on-april-13-2026-64-65f",  # fav+2
        "highest-temperature-in-atlanta-on-april-13-2026-82-83f",
        "highest-temperature-in-atlanta-on-april-13-2026-84-85f",
        "highest-temperature-in-dallas-on-april-13-2026-81-82f",
    ]
    by_tier = defaultdict(list)
    for slug in sample_slugs:
        d = BOOK_DIR / slug
        if not d.exists():
            continue
        info = analyze_slug_depth(d)
        books = info["books"]
        fills = info["fills"]
        if not books:
            continue
        avg_ask = sum(b["best_ask"] for b in books) / len(books)
        avg_depth_ask = sum(b["depth_at_ask"] for b in books) / len(books)
        avg_depth_2c = sum(b["depth_within_2c"] for b in books) / len(books)
        avg_depth_tot = sum(b["depth_total_ask"] for b in books) / len(books)
        print(f"\n{slug}")
        print(f"  hours: {info['n_hours']}, book snaps: {len(books)}, fills: {len(fills)}")
        print(f"  avg best_ask: ${avg_ask:.4f}")
        print(f"  avg depth at best_ask: {avg_depth_ask:.0f} shares")
        print(f"  avg depth within 2¢: {avg_depth_2c:.0f} shares")
        print(f"  avg total ask depth: {avg_depth_tot:.0f} shares")
        # Fill sizes
        if fills:
            import statistics
            sizes = [f["size"] for f in fills]
            print(f"  fill sizes: n={len(sizes)}, median={statistics.median(sizes):.0f}, "
                  f"p75={statistics.quantiles(sizes, n=4)[2]:.0f}, max={max(sizes):.0f}")

        tier = "fav" if avg_ask > 0.3 else "fav+1" if avg_ask > 0.15 else "fav+2" if avg_ask > 0.05 else "tail"
        by_tier[tier].append(avg_depth_2c)

    print("\n=== Capacity estimate per price tier (median across sampled slugs) ===")
    import statistics
    for tier in ("fav", "fav+1", "fav+2", "tail"):
        if by_tier[tier]:
            med = statistics.median(by_tier[tier])
            print(f"  {tier:<6}: median depth within 2¢ = {med:.0f} shares")


if __name__ == "__main__":
    main()

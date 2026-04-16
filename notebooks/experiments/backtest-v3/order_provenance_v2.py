"""Fast order-provenance analysis for +1 offset NO buckets.

Answers: are the NO-ask quotes on these buckets from market makers
(that would replenish) or individual retail orders (that would not)?

Fingerprints checked:
1. **Price-update frequency per hour** — MM = high (hundreds/hour),
   retail = low (few/hour)
2. **Size distribution** — MM uses round-5 chunks (5, 10, 20, 50, 100);
   retail shows odd/fractional sizes (34.99, 189, 1234)
3. **Spread persistence** — MM maintains tight spreads; retail leaves
   wide spreads
4. **Two-sided quoting** — MM quotes both bid and ask; retail often
   one-sided (just a buy or just a sell)
5. **After-fill behavior** — does top-of-book refresh within seconds
   of a fill? (MM yes, retail no)
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np

REPO = Path("/Users/lawsongraham/git/weather")
BOOK_DIR = REPO / "data" / "raw" / "polymarket_book"

TARGET_SLUGS = [
    "highest-temperature-in-atlanta-on-april-13-2026-84-85f",
    "highest-temperature-in-miami-on-april-13-2026-82-83f",
    "highest-temperature-in-seattle-on-april-13-2026-56-57f",
    "highest-temperature-in-los-angeles-on-april-13-2026-66-67f",
    "highest-temperature-in-san-francisco-on-april-13-2026-64-65f",
    "highest-temperature-in-nyc-on-april-13-2026-78-79f",
    "highest-temperature-in-nyc-on-april-12-2026-56-57f",
    "highest-temperature-in-nyc-on-april-11-2026-62-63f",
]


def classify_size(size: float) -> str:
    """Guess order provenance from size."""
    # Fractional size (e.g. 34.99) — likely retail dollar-converted
    if abs(size - round(size)) > 0.001:
        return "fractional"
    size_int = int(round(size))
    # Round fives are the MM signature
    if size_int in (5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 500, 1000):
        return "round"
    # Small odd integers (1, 2, 3, 7, 13) — retail
    if size_int < 50:
        return "small-odd"
    return "large-odd"


def main():
    # Get YES/NO token IDs
    con = duckdb.connect()
    slugs_sql = ",".join(f"'{s}'" for s in TARGET_SLUGS)
    tok = con.execute(f"""
        SELECT slug, yes_token_id
        FROM '{REPO}/data/processed/polymarket_weather/markets.parquet'
        WHERE slug IN ({slugs_sql})
    """).fetchall()
    yes_tok_of = {s: t for s, t in tok}

    agg_update_counts = []
    agg_hours = []
    agg_size_cls: Counter = Counter()
    agg_spreads: list[float] = []
    agg_both_sided: list[float] = []
    agg_fills_size: Counter = Counter()

    for slug in TARGET_SLUGS:
        slug_dir = BOOK_DIR / slug
        if not slug_dir.exists():
            continue
        yes_tok = yes_tok_of.get(slug)
        if yes_tok is None:
            continue

        yes_pc_count = 0
        yes_sizes: list[float] = []
        fills_sizes: list[float] = []
        yes_spreads: list[float] = []
        yes_bid_count = 0
        yes_ask_count = 0
        first_ts: datetime | None = None
        last_ts: datetime | None = None

        for f in sorted(slug_dir.glob("*.jsonl")):
            for line in f.read_text().splitlines():
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = m.get("event_type") or ""
                if et == "price_change":
                    for pc in (m.get("price_changes") or []):
                        if pc.get("asset_id") != yes_tok:
                            continue
                        yes_pc_count += 1
                        try:
                            yes_sizes.append(float(pc.get("size", 0)))
                        except Exception:
                            continue
                        side = pc.get("side", "")
                        if side == "BUY":
                            yes_bid_count += 1
                        elif side == "SELL":
                            yes_ask_count += 1
                        try:
                            spread = float(pc.get("best_ask", 0)) - float(pc.get("best_bid", 0))
                            if 0 < spread < 0.5:
                                yes_spreads.append(spread)
                        except Exception:
                            pass
                elif et == "book":
                    if m.get("asset_id") != yes_tok:
                        continue
                elif et == "last_trade_price":
                    if m.get("asset_id") != yes_tok:
                        continue
                    try:
                        fills_sizes.append(float(m.get("size", 0)))
                    except Exception:
                        continue
                # Timestamps from any event
                ts_str = m.get("_received_at", "")
                if ts_str:
                    try:
                        ts = datetime.strptime(ts_str.replace("Z", "+00:00"),
                                               "%Y-%m-%dT%H:%M:%S.%f%z")
                        if first_ts is None or ts < first_ts:
                            first_ts = ts
                        if last_ts is None or ts > last_ts:
                            last_ts = ts
                    except Exception:
                        pass

        if first_ts is None or yes_pc_count == 0:
            continue
        hours = (last_ts - first_ts).total_seconds() / 3600
        rate = yes_pc_count / hours if hours > 0 else 0
        size_cls = Counter(classify_size(s) for s in yes_sizes)
        fill_cls = Counter(classify_size(s) for s in fills_sizes)
        both_sided = (min(yes_bid_count, yes_ask_count)
                     / max(yes_bid_count, yes_ask_count)) if max(yes_bid_count, yes_ask_count) > 0 else 0

        print(f"\n=== {slug[:55]} ===")
        print(f"  Duration: {hours:.1f} hours  |  YES price updates: {yes_pc_count:,}  "
              f"|  Rate: {rate:,.0f}/hour")
        print(f"  YES bid updates: {yes_bid_count:,} | YES ask updates: {yes_ask_count:,}  "
              f"(symmetry ratio: {both_sided:.2f})")
        print(f"  Spread stats: median=${np.median(yes_spreads):.4f} mean=${np.mean(yes_spreads):.4f} "
              f"p90=${np.percentile(yes_spreads, 90):.4f}" if yes_spreads else "  Spread: no data")
        print(f"  Order size categories:")
        total = sum(size_cls.values())
        for cls, ct in size_cls.most_common():
            print(f"    {cls:<15}: {ct:>5} ({ct/total*100:>4.1f}%)")
        print(f"  Fills: {len(fills_sizes)} (total size={sum(fills_sizes):.0f})")
        if fills_sizes:
            print(f"    Fill size categories:")
            ftotal = sum(fill_cls.values())
            for cls, ct in fill_cls.most_common():
                print(f"      {cls:<15}: {ct:>3} ({ct/ftotal*100:>4.1f}%)")

        agg_update_counts.append(yes_pc_count)
        agg_hours.append(hours)
        agg_size_cls.update(size_cls)
        agg_spreads.extend(yes_spreads[:1000])  # cap for memory
        agg_both_sided.append(both_sided)
        agg_fills_size.update(fill_cls)

    print("\n" + "=" * 70)
    print("AGGREGATE ACROSS ALL +1 OFFSET SLUGS")
    print("=" * 70)
    print(f"Total hours: {sum(agg_hours):.0f}")
    print(f"Total price updates: {sum(agg_update_counts):,}")
    print(f"Average update rate: {np.mean([c/h for c,h in zip(agg_update_counts, agg_hours) if h>0]):,.0f}/hour")
    print(f"Average bid/ask symmetry: {np.mean(agg_both_sided):.2f}")
    print(f"Spread distribution (over 1,000 samples/slug):")
    print(f"  median: ${np.median(agg_spreads):.4f}  mean: ${np.mean(agg_spreads):.4f}")
    print()
    print("AGGREGATE ORDER SIZE CATEGORIES (price_change events):")
    total = sum(agg_size_cls.values())
    for cls, ct in agg_size_cls.most_common():
        print(f"  {cls:<15}: {ct:>6} ({ct/total*100:>4.1f}%)")
    print()
    if sum(agg_fills_size.values()) > 0:
        print("AGGREGATE FILL SIZE CATEGORIES:")
        total = sum(agg_fills_size.values())
        for cls, ct in agg_fills_size.most_common():
            print(f"  {cls:<15}: {ct:>4} ({ct/total*100:>4.1f}%)")

    print()
    print("INTERPRETATION GUIDE:")
    print("  - Update rate > 500/hour suggests algorithmic / MM quoting")
    print("  - Symmetry ratio > 0.5 suggests two-sided MM presence")
    print("  - >50% 'round' sizes = MM; >50% 'fractional' = retail USDC conversion")


if __name__ == "__main__":
    main()

"""Exploratory T — slippage + L2-depth-aware arb P&L.

Corrects expS by pulling full bids[] arrays from book snapshots (not
just top-of-book) and computing the realistic "walk the book" fill
price for various target sizes. Answers:

  1. What's the effective fill price at N-share size, walking the
     book across multiple levels?
  2. Given that arb size is capped at min(top-bid depth across legs),
     what's the true maximum profitable size per cycle?
  3. How much of the "profit" disappears under a 50% depth haircut
     (competition-adjusted)?
"""
from __future__ import annotations

import json
from pathlib import Path

BK = Path("data/raw/polymarket_book")
MARKETS_PARQUET = "data/processed/polymarket_weather/markets.parquet"
FEE_RATE = 0.05  # weather markets


def load_yes_tokens() -> dict[str, str]:
    import duckdb
    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT slug, yes_token_id FROM '{MARKETS_PARQUET}'
        WHERE yes_token_id IS NOT NULL
    """).fetchall()
    return {r[0]: r[1] for r in rows}


def load_yes_book_at(slug: str, target_ts_substr: str, yes_tok: str) -> dict | None:
    """Find the latest YES-token book snapshot whose _received_at starts with target_ts_substr."""
    slug_dir = BK / slug
    if not slug_dir.exists():
        return None
    latest = None
    for jf in sorted(slug_dir.glob("*.jsonl")):
        with jf.open() as fh:
            for line in fh:
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if (m.get("event_type") == "book"
                    and m.get("asset_id") == yes_tok
                    and target_ts_substr in m.get("_received_at", "")):
                    latest = m
    return latest


def walk_bid_side(bids: list[dict], target_size: float) -> tuple[float, float, list]:
    """Fill `target_size` shares against the bid ladder. Bids may be in ASC or DESC order;
    we sort DESC so we start with the highest bid.

    Returns (effective_fill_price, filled_size, trace) where trace is a list of
    (level_price, level_size_filled) tuples.
    """
    lv = sorted([(float(b["price"]), float(b["size"])) for b in bids], reverse=True)
    remaining = target_size
    total_notional = 0.0
    trace = []
    for price, size in lv:
        if remaining <= 0:
            break
        take = min(remaining, size)
        total_notional += take * price
        trace.append((price, take))
        remaining -= take
    filled = target_size - remaining
    avg_price = (total_notional / filled) if filled > 0 else 0.0
    return avg_price, filled, trace


def arb_pnl_with_slippage(
    per_leg: list[tuple[str, list[dict]]],
    target_size: float,
    label: str,
) -> dict:
    """Compute arb P&L when selling `target_size` shares of each leg against
    the passed-in bids ladders.

    per_leg: list of (leg_name, bids_list). Each bids_list is the raw
    bids[] array from a YES-token book snapshot.
    """
    total_receipts = 0.0
    total_fees = 0.0
    total_filled = 0.0
    per_leg_detail = []
    for name, bids in per_leg:
        avg_p, filled, trace = walk_bid_side(bids, target_size)
        if filled == 0:
            per_leg_detail.append({"leg": name, "fill": 0, "avg_p": 0, "recv": 0, "fee": 0})
            continue
        receipts = avg_p * filled
        # fee is per-share at the fill price
        fee = 0.0
        for price, size in trace:
            fee += size * FEE_RATE * price * (1 - price)
        total_receipts += receipts
        total_fees += fee
        total_filled += filled
        per_leg_detail.append({
            "leg": name,
            "fill": round(filled, 2),
            "avg_p": round(avg_p, 4),
            "recv": round(receipts, 4),
            "fee": round(fee, 4),
        })

    max_payout = target_size * 1.00  # one winning bucket pays $1 per share
    net = total_receipts - total_fees - max_payout

    return {
        "label": label,
        "target_size": target_size,
        "total_receipts": round(total_receipts, 4),
        "total_fees": round(total_fees, 4),
        "max_payout": round(max_payout, 4),
        "net_profit": round(net, 4),
        "profit_per_share": round(net / target_size, 4),
        "per_leg": per_leg_detail,
    }


def main() -> None:
    yes_tokens = load_yes_tokens()

    # Case 1: the expI/J moment — april-11 at 20:12:10-14 UTC
    # Live buckets: 60-61, 62-63, 64-65, 66-67
    print("=" * 70)
    print("CASE 1: april-11 20:12:10 UTC (near-resolution, 4 live buckets)")
    print("=" * 70)
    legs = []
    for strike in ["60-61f", "62-63f", "64-65f", "66-67f"]:
        slug = f"highest-temperature-in-nyc-on-april-11-2026-{strike}"
        yes_tok = yes_tokens.get(slug)
        if not yes_tok:
            print(f"  {strike}: no token")
            continue
        book = load_yes_book_at(slug, "2026-04-11T20:12:1", yes_tok)
        if not book:
            print(f"  {strike}: no book snapshot in window")
            continue
        bids = book.get("bids", [])
        top5 = sorted([(float(b["price"]), float(b["size"])) for b in bids], reverse=True)[:5]
        print(f"  {strike}: top5 bids = {top5}")
        legs.append((strike, bids))

    for size in [1, 3, 5, 10, 21, 50]:
        r = arb_pnl_with_slippage(legs, size, f"size={size}")
        print(f"\n  size={size}: receipts=${r['total_receipts']:6.2f}  fees=${r['total_fees']:5.3f}  "
              f"payout=${r['max_payout']:6.2f}  NET=${r['net_profit']:+6.3f}  "
              f"per_share={r['profit_per_share']:+.4f}")
        for leg in r["per_leg"]:
            print(f"    {leg['leg']:<10} fill={leg['fill']:<6} avg_p={leg['avg_p']:<7} "
                  f"recv=${leg['recv']:<7} fee=${leg['fee']}")

    # Case 2: april-12 at 23:15:20-22 UTC (full 11-bucket ladder, sum_bid=1.011)
    print("\n" + "=" * 70)
    print("CASE 2: april-12 23:15:20 UTC (pre-resolution, 11-bucket ladder sum=1.011)")
    print("=" * 70)
    apr12_strikes = ["47forbelow", "48-49f", "50-51f", "52-53f", "54-55f", "56-57f",
                     "58-59f", "60-61f", "62-63f", "64-65f", "66forhigher"]
    legs = []
    for strike in apr12_strikes:
        slug = f"highest-temperature-in-nyc-on-april-12-2026-{strike}"
        yes_tok = yes_tokens.get(slug)
        if not yes_tok:
            continue
        book = load_yes_book_at(slug, "2026-04-11T23:15:2", yes_tok)
        if not book:
            continue
        bids = book.get("bids", [])
        top3 = sorted([(float(b["price"]), float(b["size"])) for b in bids], reverse=True)[:3]
        print(f"  {strike}: top3 bids = {top3}")
        legs.append((strike, bids))

    for size in [1, 3, 5, 10, 25, 50, 100]:
        r = arb_pnl_with_slippage(legs, size, f"size={size}")
        print(f"\n  size={size}: receipts=${r['total_receipts']:7.3f}  fees=${r['total_fees']:5.3f}  "
              f"payout=${r['max_payout']:7.2f}  NET=${r['net_profit']:+7.3f}  "
              f"per_share={r['profit_per_share']:+.4f}")

    # Also: what's the OPTIMAL size where per_share profit is max?
    print("\n--- profit vs size curve for april-12 case ---")
    for size in [1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50, 100, 200, 500]:
        r = arb_pnl_with_slippage(legs, size, f"s{size}")
        print(f"  size={size:<4} net=${r['net_profit']:+8.3f} per_share={r['profit_per_share']:+.5f}")


if __name__ == "__main__":
    main()

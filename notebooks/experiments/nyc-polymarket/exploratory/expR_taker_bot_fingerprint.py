"""Exploratory R — fingerprint the existing arb taker bot.

Exp O observed a multi-leg 10-sell burst at 23:15:21.089 UTC on
april-12, all with 7-share size, within a 50ms window. That's a
signature. This script scans ALL `last_trade_price` events in the
raw JSONL, clusters them by time window + uniform size, and asks:

  1. How often does a multi-leg arb fire?
  2. What's the typical leg size distribution?
  3. Which hours are most active?
  4. Is it always the same bot, or multiple?
  5. Who fires last — us or them?

A "multi-leg arb execution" is defined as:
  - >= 8 last_trade_price events
  - within a 200ms window
  - all same side (all SELL or all BUY)
  - identical size
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from statistics import median

BK = Path("data/raw/polymarket_book")


def parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def collect_trades() -> list[dict]:
    """Walk every JSONL, extract last_trade_price events."""
    trades = []
    for sd in sorted(BK.iterdir()):
        if not sd.is_dir():
            continue
        for jf in sorted(sd.glob("*.jsonl")):
            with jf.open() as fh:
                for line in fh:
                    try:
                        m = json.loads(line)
                    except Exception:
                        continue
                    if m.get("event_type") != "last_trade_price":
                        continue
                    try:
                        ts = parse_ts(m["_received_at"])
                    except Exception:
                        continue
                    trades.append({
                        "ts": ts,
                        "slug": sd.name,
                        "asset_id": m.get("asset_id", ""),
                        "price": float(m.get("price", 0)),
                        "size": float(m.get("size", 0)),
                        "side": m.get("side", ""),
                    })
    return trades


def cluster_multi_leg(trades: list[dict], window_ms: int = 200, min_legs: int = 8) -> list[dict]:
    """Group trades into clusters within `window_ms` of each other with same size.

    Returns list of cluster summaries with n_legs, duration, size, side, total_receipts,
    and per-cluster market_dates touched.
    """
    # Sort by timestamp
    trades = sorted(trades, key=lambda t: t["ts"])
    clusters: list[dict] = []
    i = 0
    n = len(trades)
    while i < n:
        j = i
        while j < n and (trades[j]["ts"] - trades[i]["ts"]).total_seconds() * 1000 <= window_ms:
            j += 1
        group = trades[i:j]
        if len(group) >= min_legs:
            # Within the group, find sub-groups that share (size, side) — that's the arb pattern
            by_sig: dict[tuple[float, str], list[dict]] = defaultdict(list)
            for t in group:
                by_sig[(t["size"], t["side"])].append(t)
            for (size, side), legs in by_sig.items():
                if len(legs) >= min_legs:
                    slugs_touched = {t["slug"] for t in legs}
                    mds = {t["slug"].split("-on-")[1].rsplit("-", 1)[0] for t in legs if "-on-" in t["slug"]}
                    clusters.append({
                        "t0": legs[0]["ts"],
                        "duration_ms": round((legs[-1]["ts"] - legs[0]["ts"]).total_seconds() * 1000, 1),
                        "n_legs": len(legs),
                        "size_per_leg": size,
                        "side": side,
                        "total_notional_recv": round(sum(t["price"] * size for t in legs), 4),
                        "n_slugs": len(slugs_touched),
                        "market_dates": sorted(mds),
                    })
            i = j
        else:
            i += 1
    return clusters


def main() -> None:
    print("collecting trades from raw JSONL...")
    trades = collect_trades()
    print(f"total last_trade_price events: {len(trades):,}")

    # Global stats
    if not trades:
        return
    sides = defaultdict(int)
    sizes = []
    for t in trades:
        sides[t["side"]] += 1
        sizes.append(t["size"])
    print(f"sides: {dict(sides)}")
    print(f"size distribution: median={median(sizes)} min={min(sizes)} max={max(sizes)}")

    # Per-slug trade count
    per_slug = defaultdict(int)
    for t in trades:
        per_slug[t["slug"]] += 1
    print(f"unique slugs with trades: {len(per_slug)}")

    # Cluster multi-leg arbs
    print("\n--- clustering multi-leg arb executions (>=8 legs, 200ms window, same size/side) ---")
    clusters = cluster_multi_leg(trades, window_ms=200, min_legs=8)
    print(f"multi-leg clusters found: {len(clusters)}")

    # Size distribution of clusters
    sizes = [c["size_per_leg"] for c in clusters]
    legs = [c["n_legs"] for c in clusters]
    notionals = [c["total_notional_recv"] for c in clusters]
    print(f"size-per-leg distribution: median={median(sizes) if sizes else '-'} "
          f"min={min(sizes) if sizes else '-'} max={max(sizes) if sizes else '-'}")
    print(f"n_legs distribution: median={median(legs) if legs else '-'} "
          f"min={min(legs) if legs else '-'} max={max(legs) if legs else '-'}")
    print(f"notional per cluster: median={median(notionals) if notionals else '-'} "
          f"min={min(notionals) if notionals else '-'} max={max(notionals) if notionals else '-'}")

    # Hour-of-day distribution
    hours = defaultdict(int)
    for c in clusters:
        hours[c["t0"].hour] += 1
    print(f"clusters by UTC hour: {dict(sorted(hours.items()))}")

    # Side distribution
    sides_c = defaultdict(int)
    for c in clusters:
        sides_c[c["side"]] += 1
    print(f"cluster sides: {dict(sides_c)}")

    # Per-market distribution
    md_c = defaultdict(int)
    for c in clusters:
        for md in c["market_dates"]:
            md_c[md] += 1
    print(f"clusters by market-date: {dict(md_c)}")

    # Show first 10 clusters
    print("\n--- sample clusters (first 10) ---")
    for c in clusters[:10]:
        print(f"  {c['t0'].strftime('%H:%M:%S.%f')[:-3]}  "
              f"legs={c['n_legs']:2d} size={c['size_per_leg']:<8} "
              f"side={c['side']:<4} dur={c['duration_ms']:6.1f}ms "
              f"notional=${c['total_notional_recv']:<8} md={','.join(c['market_dates'])}")

    # Timing: are clusters clustered in time themselves?
    # i.e. do we see bursts of multi-leg arbs within minutes of each other?
    if len(clusters) >= 2:
        gaps = []
        for i in range(1, len(clusters)):
            g = (clusters[i]["t0"] - clusters[i-1]["t0"]).total_seconds()
            gaps.append(g)
        print(f"\ngaps between clusters (sec): median={median(gaps):.1f} "
              f"min={min(gaps):.1f} max={max(gaps):.1f}")
        # Very-short-gap cluster rate
        short_gaps = sum(1 for g in gaps if g < 60)
        print(f"  clusters within 60s of another: {short_gaps}/{len(gaps)}")


if __name__ == "__main__":
    main()

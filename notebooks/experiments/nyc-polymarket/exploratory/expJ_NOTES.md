# Exp J — Ladder arb Phase 1 validation + live watchman + competitive analysis

**Scripts**:
- `scripts/polymarket_book/watchman.py` — new live WS ladder-arb alerter
- Inline duckdb queries against `data/processed/polymarket_book/tob/`

**Date**: 2026-04-11
**Status**: Phase 1 (L2 depth + watchman) landed. Arb confirmed via raw
L2 depth spot-checks at real YES asset_ids. Competitive analysis shows
mixed evidence — some arbs eaten in 1-2s, some linger 30+s — we have
execution room.

## Watchman (Phase 2 of the priority-0 roadmap)

New script `scripts/polymarket_book/watchman.py` — thin, stateless, WS-
driven observer. Subscribes to all open NYC daily-temp slugs' YES+NO
tokens, maintains a per-YES-token `(best_bid, best_ask, last_update)`
state, and on every message re-evaluates `sum(live bids)` grouped by
market-date. If sum > 1.005 with all ≥10 slugs present and none stale
(> 30s old), emits an alert to `data/processed/polymarket_book_watchman/
alerts.jsonl` with full per-bucket bid/ask state + the triggering
timestamp.

Design notes:
- **Purely observational.** No trade placement. Unblocks paper-execution
  Phase 3 without risk.
- **Sub-second dedupe** on `last_alert_at[md]` — if the same market-
  date fires twice within 1 second, only the first alert lands. Avoids
  spamming during the 3-4 second linger clusters.
- **Staleness gate**: if any bucket's last quote is > 30s old, skip
  the evaluation entirely. This is more generous than exp I's 2s
  filter but catches the practical arbs observed in the captured data.
- **Completeness gate**: require ≥ 10 buckets present (out of 11) before
  evaluating. Partial ladders get skipped.

### Smoke test result

60-second smoke test (2026-04-11 21:24:20-21:25:19 UTC): **0 alerts, 1487
msgs processed.** Not a bug — validated that the april-11 market had
already exited its arb window by 21:25 UTC (see temporal analysis below).

## Temporal distribution of arbs during the resolution day

Hour-by-hour on april-11 (65 min of captured data spanning 19:24-21:25 UTC):

| UTC hour (EDT) | total seconds | arb seconds | max sum |
|----------------|---------------|-------------|---------|
| 19 (15 EDT)    | 1904          | 5           | 1.042   |
| **20 (16 EDT)** | 2890         | **31**      | **1.043** |
| 21 (17 EDT)    | 1182          | 3           | 1.004 (barely) |

**Peak window is hour 20 UTC (4 PM EDT)**. By hour 21, the favorite has
consolidated past ~0.95 and the overround naturally compresses to near 1.0.
The arb is concentrated in a ~1.5 hour window where the favorite is
at ~0.85-0.92 and 3-5 buckets are still "live" while 6-8 are walked away.

**Implication for the watchman**: it needs to be running DURING the arb
window, not catch-up after resolution. Currently (21:25 UTC) the window
has closed for april-11. april-12 will enter its arb window ~24 h from
now (16 EDT tomorrow = 20 UTC 2026-04-12).

## Arb persistence — are we racing other bots?

For each arb second, traced forward 5 and 30 seconds to see when sum_bid
returned below 1.0. Mixed results:

| arb sec         | peak  | max_p5 | min_p5 | max_p30 | min_p30 | lingers? |
|-----------------|-------|--------|--------|---------|---------|----------|
| 19:54:16        | 1.042 | 1.042  | 0.942  | 1.042   | 0.922   | 3 sec    |
| 20:08:23        | 1.026 | 1.026  | 1.026  | 1.026   | 0.986   | ~3 sec   |
| 20:12:12        | 1.043 | 1.043  | 0.987  | 1.043   | 0.977   | 2 sec    |
| **20:13:42**    | 1.006 | 1.006  | 1.005  | 1.006   | **1.001** | **30+s** |
| 20:24:52        | 1.025 | 1.025  | 1.015  | 1.025   | 0.004   | 2 sec    |

**The 20:13:42 cluster stayed above 1.00 for 30+ consecutive seconds**.
Nobody was watching it. If our bot had been running, we'd have had
~30 seconds of free execution time.

Other clusters disappear in 1-3 seconds — either because a market order
took the top-of-bid (someone else arbing) or because the bid on one
live bucket was canceled (natural decay, not arbing).

**Competitive conclusion**: **the NYC ladder-bid arb is NOT consistently
beaten by other bots.** At least one observed cluster ran for 30+ seconds
without a taker. Some may be eaten quickly, but the market has capacity.

## L2 depth spot-check — 20:13:42-45 cluster

Raw YES-token `book` snapshots around 20:13:45-48 UTC:

| bucket  | top bid   | top bid size |
|---------|-----------|--------------|
| 60-61°F | $0.88     | **21.33 shares** |
| 62-63°F | $0.12     | **38.51 shares** |
| 64-65°F | $0.001    | 759 shares (floor) |
| 66-67°F | $0.004    | 254 shares   |
| (others) | $0.000   | —           |

**Arb sizing** (limited by 60-61's 21 shares at the top bid):

- 21 shares per leg on the 4 live buckets
- Receipts: 21 × (0.88 + 0.12 + 0.001 + 0.004) = 21 × **1.005** = **$21.105**
- Max payout if a winner lands in one of the 4 sold: 21 × $1 = $21.00
- **Net profit: $0.105 per cycle at 21-share scale ($21 capital)**
- ROI per cycle: 0.5%

The earlier (exp I) 20:12:13 arb was denser (sum = 1.043) but thinner
(60-61 top bid was only 5 shares). Profit per cycle:
- Receipts: 5 × 1.043 = $5.215
- Payout: 5 × $1 = $5.00
- **Profit: $0.215 on $5 capital** → 4.3% ROI per cycle

So the arb window has a trade-off: **early in the window (higher sum,
thinner size), later (tighter sum, larger size)**. Combined estimate:
$0.10-0.22 per cycle × 30 cycles/hour × 2h window = **$6-13/day per city**.

## Going to deeper L2 on 60-61 hurts the edge

At the 20:13:45 arb, 60-61's L2 is:
```
level 1: $0.88 × 21.33 shares
level 2: $0.85 × 6.39 shares
level 3: $0.82 × 8.00 shares
```

If we tried to size at 27 shares (21 + 6 level-2), the blended bid on
60-61 drops to (21×0.88 + 6×0.85)/27 = $0.8733. New arb:

- Receipts: 27 × (0.8733 + 0.12 + 0.001 + 0.004) = 27 × 0.9983 = $26.96
- Payout: 27 × $1 = $27.00
- **Loss: -$0.04** ← arb gone

**The 21-share cap is a hard limit on THIS particular arb cluster.**
Going deeper on any leg turns the edge negative. Cross-arb size
variation suggests the avg per-leg cap is 5-25 shares depending on
book shape at that moment.

## Takeaways + updates to the vault synthesis

1. **Watchman is live and tested** — ready to fire as soon as april-12
   enters its arb window tomorrow (20-21 UTC). Running it during the
   actual window is queued for next iteration's loop check.
2. **Arb cap scales with top-bid size** — 5-25 shares per leg depending
   on the specific arb instance. Going deeper flips the edge negative
   on the thinnest leg.
3. **Not actively contested** — at least one observed 30-second linger
   proves the market isn't being fully arbed. A taker bot with 500ms
   latency should catch meaningful share of opportunities.
4. **Temporal concentration**: hour 20 UTC (4 PM EDT) is the peak; must
   run the watchman during this window or miss everything.

## Queued for next iteration

- **Run the watchman across april-12's 20 UTC peak window** (in ~24 h)
  and count live alerts. If it fires ~30 times in the hour, that
  matches exp I's historical estimate.
- **Paper execution simulator**: on each alert, simulate 4 concurrent
  sell-limits at top-of-bid sizes. Track hypothetical PnL if filled.
- **Strategy D V1 replay against real asks** (exp K, still queued from
  iteration 3).
- **L2-depth-aware transformer**: emit full bids/asks ladders alongside
  top-of-book, making future size-constrained queries a single SQL.

---
tags: [synthesis, polymarket, arbitrage, edge, near-resolution, verified]
date: 2026-04-11
related: "[[2026-04-11 Real-book replay invalidates sell-pop edge]], [[2026-04-11 First pass 1-min price data exploration]], [[Polymarket CLOB WebSocket]], [[Polymarket]]"
---

# Near-resolution ladder-BID arbitrage (VERIFIED, 2026-04-11)

**Status**: **first real tradable edge identified in this project.**
Risk-free arb on the bid side of NYC daily-temp ladders in the final
~2 hours before resolution. 22 distinct occurrences captured in 65
minutes of live WS data on april-11. Verified with freshness filter
(every bid quoted within 2 seconds) and spot-checked with raw L2 depth.
Estimated scale: $75-150/day NYC-only at 5-10 shares per leg, 8× across
cities. **Queued for Phase-1 implementation in the next iteration.**

## The pattern in one paragraph

In the final 1-2 hours before a NYC daily-temp market resolves, the
realized temperature trajectory has made it clear which bucket (or
small neighborhood of buckets) will win. Polymarket market-makers walk
away from the "dead" buckets — the ones that cannot possibly win —
leaving their posted bids at exactly $0.00. Meanwhile the "live" 3-4
buckets still have active bids. When the live side's individual
probabilities are slightly overpriced (standard midpoint overround
from spread width), the sum of live bids crosses $1.00. At that point,
selling 1 YES token of each live bucket at the posted bids receives
more than the $1.00 maximum payout liability, guaranteeing a risk-free
profit.

## The canonical example

**2026-04-11 20:12:13 UTC, april-11 market** (~1h45m before resolution):

```
60-61°F bid $0.890 × 5.0 shares  ← eventual winner
62-63°F bid $0.140 × 50 shares
64-65°F bid $0.008 × N
66-67°F bid $0.005 × N
59°F-    bid $0.000 × 0         ← dead (quoted at floor, no size)
68-69°F  bid $0.000 × 0         ← dead
70-71°F  bid $0.000 × 0         ← dead
72-73°F  bid $0.000 × 0         ← dead
74-75°F  bid $0.000 × 0         ← dead
76-77°F  bid $0.000 × 0         ← dead
78°F+    bid $0.000 × 0         ← dead

sum(live bids) = 1.043
```

**Trade**: sell 5 YES on each of the 4 live buckets at their top bids.

**Receipts**: 5 × (0.89 + 0.14 + 0.008 + 0.005) = **$5.215**

**Resolution payout**:

| if winner is… | obligation | net profit |
|---------------|------------|------------|
| 60-61°F (mid ≈ 0.89, actual winner) | 5 × $1 = $5 | **+$0.215** |
| 62-63°F (mid ≈ 0.14) | 5 × $1 = $5 | **+$0.215** |
| 64-65°F (mid ≈ 0.01) | 5 × $1 = $5 | **+$0.215** |
| 66-67°F (mid ≈ 0.01) | 5 × $1 = $5 | **+$0.215** |
| any of the 7 dead buckets | $0  | **+$5.215** |

Every possible outcome is strictly profitable. This is super-arbitrage.

## Why it exists

Near-resolution dynamics:

1. **Temperature trajectory is mostly known** — by 16:00 EDT the LGA
   max for the day is within a 2-3°F band. Makers know which buckets
   are dead.
2. **Dead-bucket MMs walk away** — posting a $0.001 bid on a bucket
   that cannot win costs the MM zero but they don't bother. The bid
   drops to zero entirely.
3. **Live-bucket midpoints stay overround** — the remaining 3-4 buckets
   still carry the standard midpoint overround from spread width (see
   [[2026-04-11 Real-book replay invalidates sell-pop edge]]). Without
   the offsetting "drag" from dead-bucket bids, the live-bucket sum
   crosses $1.00.

**This ONLY happens near resolution.** april-12 (tomorrow) and april-13
(two days out) show max fresh bid sums of 0.981 and 0.976 respectively
— both buckets still in the dispersed regime, all bids present, no arb.

## Rate and scale

### Per-market rate

- **22 arb seconds / 65 min of observation** during the 19:24-20:28 UTC
  window (~1h to ~40min before resolution on april-11)
- **~20 arb events per hour** during the final 2 hours
- Average cluster is 1-2 consecutive seconds per event

### Per-cycle profit at 5-share scale

- $0.215 per arb cycle (limited by top-bid size on the thinnest bucket,
  typically 60-61 at 5 shares in the canonical example)
- Scaling to 10 shares requires deeper L2 ($0.85 × 1.39 shares at level 2
  for the canonical case, so level-2 arb is net negative — cap at 5)

### Daily P&L estimates

| scale            | NYC only | 8 cities |
|------------------|----------|----------|
| 1 share/leg      | $2/day   | $16/day  |
| 5 shares/leg     | $9.50/day| $75/day  |
| 10 shares/leg*   | $19/day  | $150/day |

(\*10 shares/leg requires deeper L2 or better execution timing to capture
subsequent refills.)

**Capital requirement**: $5-10 per arb cycle (max loss bound). Even at
100 concurrent arbs across cities, capital is < $1000. This scales
freely — the only ceiling is per-cycle market-depth on the top bid.

## Execution risks

1. **Timing window is tight**: 1-2 seconds per arb cluster. Need a WS-
   driven bot that can fire 4 concurrent sell orders in < 500ms. Human
   manual execution is impossible.
2. **Book moves during execution**: between placing sell #1 and sell
   #4, the bids may drop. Need to place all legs concurrently (async
   `asyncio.gather`) and accept partial-fill risk.
3. **Matchmaker latency**: Polymarket's CLOB matcher is off-chain with
   ~100-500ms confirm latency. Orders may not all confirm in the arb
   window. Mitigation: use limit orders at the current bid (postonly),
   accept no-fill on legs that miss.
4. **Fee surprise**: Polymarket's current fee is 0 bps (confirmed in
   WS message's `fee_rate_bps` field). If fees change, arb disappears
   above the new fee rate.
5. **Resolution edge cases**: if the market resolves by a rule we don't
   expect (revision after finalization, wx-underground data lag), the
   arb still pays but we need to be sure the "dead" buckets we didn't
   sell really don't win. 7°F misprint = worst case.

## What this changes

Prior syntheses:
- [[2026-04-11 First pass 1-min price data exploration]] Edge #3
  ("short the ladder when overround > 5c") is **partially validated**
  but with a specific structure: it's a near-resolution phenomenon, not
  general overround, and requires partial-ladder execution (can't sell
  what's not bid).
- [[2026-04-11 Real-book replay invalidates sell-pop edge]]: this
  synthesis discovered the arb while replaying the sell-pop. The sell-
  pop edge stays invalidated; the ladder-bid arb replaces it as the
  priority-0 edge.
- [[Polymarket CLOB WebSocket]]: should note that `best_bid = 0` is a
  meaningful signal (MM walkaway on dead buckets) and not just "no data".

## Priority-0 implementation roadmap

### Phase 1: L2-depth verification (NEXT ITERATION)
- Extend `scripts/polymarket_book/transform.py` to emit an L2 depth
  parquet from `book` snapshots (not just top-of-book)
- Re-run exp I with the constraint "available size ≥ N shares on each
  live bid" for N ∈ {1, 5, 10}
- Report n_arb retained at each size threshold

### Phase 2: live watchman
- Thin process subscribes to the WS, maintains cross-sectional
  per-slug state, alerts whenever sum(live bids) > 1.005
- Purely observational for now — log events to file
- Verify alert rate matches the exp I estimate (~20/hour during
  resolution window)
- Measure latency from book-change event to alert

### Phase 3: paper execution
- On alert, record a hypothetical set of N sell orders at the current
  bids and compute post-resolution PnL
- Target 30 logged trades, validate avg profit > $0.02 after slippage

### Phase 4: live @ small size
- Start 1 YES per leg ($4 capital per cycle)
- Scale iteratively after 30 successful live trades
- Monitor per-trade slippage vs paper prediction

### Phase 5: multi-city + sizing
- Generalize the watchman to N cities simultaneously
- Auto-scale per-leg size based on observed L2 depth
- Target: $100/day sustained across all NYC+8-city markets

## expN (2026-04-11 later): first live alert revises capacity DOWN — active MM found

The watchman caught its first live alert: **april-12-2026 at 23:04:28 UTC,
sum_bid = 1.010**, all 11 buckets present with live bids. But persistence
analysis tells a completely different story from the expJ april-11 findings:

```
23:04:24  sum_bid=0.499 (6/11 fresh)      ← sparse
23:04:25  sum_bid=0.820 (10/11)           
23:04:27  sum_bid=1.000 (11/11)           ← exactly 1.000 — BASELINE
23:04:28  sum_bid=1.010 (11/11)           ← ARB OPEN (watchman fires)
23:04:29  sum_bid=1.000 (11/11)           ← ARB CLOSED in 1 second
23:04:30…  sum_bid=1.000 (11/11)          ← stays at 1.000 for 30+ sec
```

**The arb was open for EXACTLY 1 SECOND.** Someone is actively holding
`sum(best_bid) = 1.000` and correcting deviations in <1 second.

### Two distinct regimes

| dimension              | april-11 (1h pre-resolution) | april-12 (25h pre-resolution) |
|------------------------|------------------------------|-------------------------------|
| Arb persistence        | 2-30 seconds                 | **1 second**                  |
| Longest linger         | 30+ seconds (20:13 cluster)  | 1 second                      |
| MM active correction?  | NO                           | **YES**                       |
| Dead-bucket bids       | $0.000 (walkaway)            | All live                      |
| Viable for taker?      | YES (with fast bot)          | NO (sub-second)               |

**April-12 (and presumably most pre-resolution hours) has an active MM
bot correcting deviations.** Taker execution is infeasible there.

**April-11 near-resolution (final 1-2 hours) is the ONLY window where
MMs walk away** — creating sustained lingers that a taker can capture.

### Revised capacity estimate (MAJOR)

Previous estimate (from expJ, based on 65-min april-11 observation):
- ~$75-150/day at 5-10 shares per leg, 8 cities

**Revised (from expN, accounting for the MM correction mechanism):**
- **Pre-resolution hours**: taker UNCATCHABLE (<1s correction window)
- **Near-resolution final 1h**: taker viable, ~20 arbs/hour × 50% catchable
  at 500ms latency × $0.10/arb = $1/day per city
- **NYC-only**: ~$5-15/day realistic
- **8 cities**: $40-120/day

**Down from $75-150/day → $5-15/day NYC (90% haircut).** Still positive,
still worth building, but in the "mechanical small edge" category rather
than "core strategy" category.

### Two execution models, both still plausible

1. **Fast near-resolution taker**: focus on the final hour of each city's
   resolution window. Requires <500ms order-placement latency. ~$1/day/city.
2. **Passive market-making**: post resting sells at `best_ask - 1c` across
   all buckets. Get filled during brief overround flashes. Captures the
   sub-second arbs the taker can't. Full MM architecture + capital + adverse
   selection risk. Would recapture most of the previous capacity estimate
   IF built correctly.

For a quick proof-of-concept: build option (1) first. Option (2) is a
multi-month build.

### Who is the active MM?

Unknown. Possibilities:
- Polymarket official MM incentive program participant
- Jane Street / Susquehanna-style sophisticated shop
- An individual bot operator with good latency

Could be identified by cross-referencing the `last_trade_price` flow
around alert moments — if there's a consistent taker-side pattern
correcting deviations, that's the MM.

---

## Phase 1-2 complete: L2-verified + live watchman landed (expJ update)

### Temporal distribution (hour-by-hour on april-11)

| UTC hour (EDT) | total seconds | arb seconds | max sum |
|----------------|---------------|-------------|---------|
| 19 (15 EDT)    | 1904          | 5           | 1.042   |
| **20 (16 EDT)** | 2890         | **31**      | **1.043** |
| 21 (17 EDT)    | 1182          | 3           | 1.004 (barely) |

**The arb is concentrated in a ~1.5-hour window starting ~4 PM EDT.** By
5 PM EDT the favorite has consolidated past 0.95 and the overround
naturally compresses. The watchman must run DURING hours 19-21 UTC (15-17
EDT) to catch anything. Post-5pm-EDT is too late.

### Competitive analysis — are we racing other bots?

Traced every arb-second forward 5 and 30 seconds:

| cluster         | peak  | linger | eaten? |
|-----------------|-------|--------|--------|
| 19:54:15-19     | 1.042 | 3 sec  | yes (10c drop in 1s) |
| 20:08:23-28     | 1.026 | 3 sec  | partial |
| 20:12:11-13     | 1.043 | 2 sec  | yes |
| **20:13:42-45** | 1.006 | **30+ sec** | **NO** — stayed above 1.00 for 30s |
| 20:24:52-53     | 1.025 | 2 sec  | yes |

**The 20:13:42 cluster stayed above 1.005 for 30 consecutive seconds with
nobody arbing.** Other clusters die in 1-3s (someone's eating them or
natural cancellation). Mixed evidence: the NYC ladder-bid arb is NOT
consistently beaten by fast bots. A 500ms-latency taker should capture a
meaningful share of opportunities.

### L2-verified arb size

Spot-check at 20:13:45 UTC (filtered to YES asset_ids):

| bucket  | top bid  | size           |
|---------|----------|----------------|
| 60-61°F | $0.88    | **21.3 shares** |
| 62-63°F | $0.12    | 38.5 shares    |
| 64-65°F | $0.001   | 759 (floor)    |
| 66-67°F | $0.004   | 254            |
| 7 others| $0.000   | —              |

At 21-share scale (cap = 60-61's top-bid depth):
- Receipts: 21 × (0.88+0.12+0.001+0.004) = **$21.105**
- Max obligation: $21.00
- **Net profit: $0.105 per cycle / $21 capital (0.5% ROI)**

Going to level 2 on 60-61 ($0.85 × 6 more shares) flips the edge
negative — the 21-share cap is hard. Size limit varies per arb
instance: 5 shares at 20:12:13 ($0.215 profit on $5) vs 21 shares at
20:13:45 ($0.105 on $21).

### Watchman Phase 2 — live observer launched

`scripts/polymarket_book/watchman.py` landed and running as a
caffeinate daemon alongside the recorder. Design:

- Stateless WS observer — one connection, same subscription set as
  the recorder (all open NYC slugs × 2 tokens)
- Per-YES-token top-of-book state + per-market-date evaluation on every
  message
- Sub-second dedupe to avoid spamming during 3-4 sec linger clusters
- Logs alerts to `data/processed/polymarket_book_watchman/alerts.jsonl`
- Smoke test (21:25 UTC): 0 alerts, 1487 msgs — validated zero-alert
  state during the post-arb window

**Will fire on april-12 tomorrow** during 20-21 UTC (16-17 EDT) when
that market enters its arb window. Live alert count tomorrow will
validate the 20-30/hour rate predicted from exp I.

## Negative checks still needed

1. **Is the $0.00 bid really "MM walkaway" or is it a data artifact?** Check
   if Polymarket's CLOB actually allows $0 bids, or if the recorder is
   normalizing empty bids to $0.
2. **Does selling YES on a NegRisk token actually work without pre-holding?**
   py-clob-client docs say yes, but verify on testnet first.
3. **Are there hidden per-trade fees at execution time?** The WS stream
   says `fee_rate_bps = 0`, but the CLOB may charge a maker rebate on the
   other side that we should account for.
4. **Is this arb being eaten by someone faster than us?** Measure: after
   the arb second, does the sum immediately revert to < 1.0? If yes,
   someone else is executing. If no (it lingers for another 5+ seconds
   without arb), the market just isn't watching.

## Related

- [[2026-04-11 First pass 1-min price data exploration]] — parent
  synthesis with the initial hypothesis
- [[2026-04-11 Real-book replay invalidates sell-pop edge]] — killed
  the sell-pop and discovered this
- [[2026-04-11 Asymmetric mean reversion edge]] — invalidated (midpoint
  artifact)
- [[Polymarket CLOB WebSocket]] — data source
- [[Polymarket]] — parent entity

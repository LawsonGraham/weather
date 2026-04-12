---
tags: [synthesis, polymarket, arbitrage, edge, near-resolution, verified]
date: 2026-04-11
related: "[[2026-04-11 Real-book replay invalidates sell-pop edge]], [[2026-04-11 First pass 1-min price data exploration]], [[Polymarket CLOB WebSocket]], [[Polymarket]]"
---

# Near-resolution ladder-BID arbitrage — RETRACTED 2026-04-11

## ⚠️ RETRACTION AT HEAD (expT slippage correction)

Slippage-aware + fee-aware re-check invalidates the edge. Two
compounding errors in every prior estimate:

1. **Fees were assumed zero.** Real fee on weather = `C × 0.05 × p × (1-p)`.
   Peaks at p=0.5 — exactly the active-bucket regime the arb targets.
2. **Prices were 1-second blips.** The "$0.89 × 5 shares on 60-61f" I
   cited in expI came from a sec_grid ASOF-last-within-2s aggregation
   that picked up a 1-second spike. The real stable top-of-bid 3 seconds
   earlier was $0.85 × 1.39 shares.

### Post-correction P&L on the canonical "profitable" arbs

**april-11 20:12:10** (near-resolution, 4 live buckets):

| size | receipts | fees   | payout | net        |
|------|----------|--------|--------|------------|
| 1    | $0.98    | $0.012 | $1.00  | **-$0.035**|
| 5    | $4.78    | $0.064 | $5.00  | **-$0.287**|
| 21   | $19.73   | $0.275 | $21.00 | **-$1.541**|

No profitable size exists. Size 21 walks 60-61f from $0.85 → $0.815
blended across four levels of depth.

**april-12 23:15:20** (11 buckets at sum_bid=1.011):

| size | net/share | note |
|------|-----------|------|
| 1    | -$0.0257  | smallest size still loses |
| 10   | -$0.0343  | |
| 500  | -$0.2249  | per-share loss gets WORSE as size grows |

The 54-55f leg has only 1.22 shares at $0.38 before walking to $0.37
then $0.36, so size compounds slippage on that leg.

**Break-even for a 5-share × 11-bucket full sell**: sum_bid ≥ ~1.046
after slippage + fees. From expP's 90-window histogram, **maybe 1-2
ever crossed this threshold.**

### Why the competitor bot exists if the taker arb doesn't pay

1. **Colocation / privileged API access** — the 0.4 ms batched burst
   we observed at 23:15:21 UTC suggests they catch 1-second blips a
   remote async executor cannot.
2. **Some observed "arbs" were directional trades**, not risk-free.
   Selling 10 of 11 april-12 buckets at sum_bid=0.701 is a bet that
   the excluded bucket wins — EV-positive if the bettor correctly
   identifies mispricing, NOT a risk-free arb.

### Retracted items from this synthesis

- "First verified edge" → **retracted**. Artifact of zero-fee + blip
  price assumptions.
- "$5-30/day NYC" / "$40-240/day at 8 cities" capacity → **retracted**.
  Realistic taker-arb net is near $0.
- "Priority-0 implementation" → **retracted**. Deprioritize build.
- "Phase 1-5 roadmap" → **retracted**.
- "Compete at 3-5 share size below existing 7-share bot" → **retracted**.
  Smaller sizes don't fix the structural fee + slippage issue.

### What actually remains

1. **Strategy D V1** (directional, buy +2 at 16 EDT): still works, and
   is actually MORE profitable than the backtest claimed because fees
   at p=0.15 are ~0.6% (not 2% as assumed). Per-trade PnL ~$3.40.
2. **Maker-rebate market-making**: 25% of taker fees redistributed
   daily. Rough estimate $60-150/day NYC. Multi-month build. See
   [[2026-04-11 Polymarket fee structure + maker rebate pivot]].
3. **Watchman + raw JSONL stream** are still valuable as data
   collection infrastructure for the maker strategy — just not as a
   signal-to-trade pipeline.

The historical analysis below is preserved for the record but is
**not actionable**.

---

# [HISTORICAL — DO NOT USE] Near-resolution ladder-BID arbitrage (pre-slippage)

**Status (at time of writing, before retraction)**: first real tradable edge identified in this project.
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

## expP (2026-04-11 latest): FINAL persistence distribution + honest capacity

90 distinct arb windows observed across ~4 hours of tob data. The
earlier expN/expO divergence was because they were sampling opposite
ends of a bimodal distribution. Clean picture:

| duration | n  | %   | cumulative |
|----------|----|-----|------------|
| 1s       | 30 | 33% | 33%        |
| 2-3s     | 25 | 28% | 61%        |
| 4-5s     | 14 | 16% | 77%        |
| 6-10s    | 13 | 14% | 92%        |
| 11-20s   |  5 | 6%  | 97%        |
| 21-40s   |  2 | 2%  | 99%        |
| 40s+     |  1 | 1%  | 100%       |

- **Median window: 2-3 seconds**
- **90% are ≤ 10 seconds**
- **7% persist 20 seconds+** (including one 79-second outlier)
- **Only 1% last 40+ seconds** — these are rare

**Both expN and expO were right about different subsets.** 33% of
windows are 1-second blips (the MM correcting fast — expN's observation),
67% are 2+ seconds (the heavy-tail regime — expO's observation). The
median is 2-3 seconds, comfortably above the latency floor for a fast
bot.

### Per-market comparison

| md       | n | p50 | p90 | max | avg peak | max peak | pattern |
|----------|---|-----|-----|-----|----------|----------|---------|
| april-11 |43 | 3   |  8  | 12  | 1.018    | 1.084    | MM-walkaway, high margins, tight windows |
| april-12 |47 | 2   | 15  | 79  | 1.012    | 1.031    | flow-driven, long tail, smaller margins |

Different regimes, different arb mechanisms, different execution windows.

### Hourly density — april-12 hour 23 UTC is the champion

| hour UTC | md     | pct arb time |
|----------|--------|--------------|
| 23       | apr-12 | **15.7%**    |
| 20       | apr-11 | 3.6%         |
| 19       | apr-11 | 1.1%         |
| 21       | apr-11 | 0.5%         |

**Hour 23 UTC on april-12 has 1 in 6 seconds in an arb state.** That
is NOT a near-resolution effect (24h pre-resolution). It's flow-driven
from an active-trading-hour / baseline-overround interaction.

### FINAL capacity number

With a realistic catch model:

- ~15-22 windows/hour during active flow
- 60% catchable at 500ms bot latency
- $0.02-0.08 per catch at 5-20 share sizes
- **Per city: $0.72 - $8.45/day**
- **8 cities total: $6-68/day**
- **Midpoint expectation: ~$30/day gross**

Competition haircut (expO shows at least one other taker eating the
largest margins):

- **Realistic NET slice: $5-30/day at 8 cities**

### Where this lands vs prior estimates

| estimate | source | $/day (8 cities) |
|----------|--------|--------------------|
| expJ optimistic (peak window, 5-10 share) | expJ | $75-150 |
| expN pessimistic (sub-MM blip) | expN | $40-120 → revised down |
| **expP honest mid-range** | **expP** | **$6-68 gross, $5-30 net** |

The honest answer is **$5-30/day in our realistic slice after competition**.
Small in absolute terms, but stacks with other strategies, needs minimal
capital (<$50 per cycle), and runs fully automated with no directional
risk.

### Rule-of-thumb going forward

**Wait for N ≥ 50 windows before committing to an arb-capacity number.**
Early estimates are dominated by whichever sample the observer saw first:
expJ saw the peak window, expN saw a blip, expO saw a linger. Only at
N=90 does the distribution become visible.

---

## expO (2026-04-11 even later): CORRECTS expN — 50-second arb + visible taker competition

expN was wrong. The 23:04:28 "1-second blip" was an outlier. The watchman
accumulated 65 alerts over the next 30 minutes (23:15-23:28 UTC) with a
very different persistence profile:

```
23:15:14  sum_bid=1.001
23:15:15-17  0.991       ← brief dip
23:15:18  1.011          ← ARB OPEN
23:15:19  1.011
...
23:16:07  1.011          ← 50 CONSECUTIVE seconds above 1.005
23:16:09  1.001          ← drops
```

**Arbs persist 30-50 seconds** during active flow periods. Not sub-
second blips.

### Direct evidence of a live taker running the arb

In the `last_trade_price` stream during the 50-sec window:

```
23:15:21.089  59f  SELL 7 @ 0.08    ← 10 SELL orders within 50ms
23:15:21.089  65f  SELL 7 @ 0.003
23:15:21.089  47-  SELL 7 @ 0.002
23:15:21.089  61f  SELL 7 @ 0.015
23:15:21.089  66+  SELL 7 @ 0.005
23:15:21.089  51f  SELL 7 @ 0.032
23:15:21.089  49f  SELL 7 @ 0.006
23:15:21.089  63f  SELL 7 @ 0.007
23:15:21.089  53f  SELL 7 @ 0.171
23:15:21.089  55f  SELL 7 @ 0.38
```

**Someone is already running the ladder-BID arb.** Multi-leg async
batched orders, 10 sells in 50ms, 7 shares per leg, total receipts
$4.907. Followed 7 seconds later by 11 NO-token buys at 0.62-0.998
(probably hedging or covering).

### Revised capacity estimate (CORRECTS expN)

expN's 90% downward revision was based on one 1-second blip. expO
shows the real pattern: **30-50 second arbs recur in bursts during
active flow**.

- **Active-flow bursts**: ~60-100 arbs/hour, 30-50s persistence
- **Quiet periods**: few/none
- **Average over 4-6 active hours per day**: 20-40 arbs/hour
- **Per city**: $5-30/day  
- **8 cities**: $40-240/day

Back in the expJ ballpark, not the expN haircut. **Competition is real**
— we'd share with the other bot(s) — but 30-50 second windows leave
plenty of room for a second taker at 3-5 share size.

### MM hypothesis is WRONG

expN's "an active MM holds sum_bid at exactly 1.000" was wrong. Actual
baseline is sum_bid ≈ 1.001 (1c of overround, which is the MM's spread
profit), and deviations to 1.011 persist for tens of seconds. The MM
isn't aggressively arbing their own book — they're letting the overround
stand.

### Implementation implications

- **Taker model VIABLE**: 30-50s windows are plenty for async multi-leg
  execution
- **Multi-leg batched async API is a hard requirement** (sequential won't
  work; the other bot does 10 sells in 50ms)
- **Size at 3-5 shares** to stay below the other taker's 7-share floor
  without competing for the same top-of-bid depth
- **Passive MM model** still higher theoretical capacity; can build
  later

### Open questions for next iteration

- Identity of the existing taker bot — batched trade pattern is
  fingerprintable; maybe traceable via Polygon addresses
- The 55f and 57f BUY trades at 23:15:20-21 (*before* the multi-leg
  sells) — are those the arb's entry legs or a separate directional
  trader?
- Rate in quiet periods — is 0 alerts/hour the norm for off-peak, or
  do small bursts happen regularly?

---

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

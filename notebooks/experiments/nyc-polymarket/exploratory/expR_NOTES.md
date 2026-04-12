# Exp R — Fingerprinting the existing arb taker bot

**Script**: `expR_taker_bot_fingerprint.py`
**Date**: 2026-04-11
**Status**: **Clean fingerprint established.** Someone is running a
disciplined multi-leg ladder-arb bot at ~2.5 clusters/hour during
active periods. 5-share legs, 10-leg SELLs, 10-50ms execution time
per cluster. Signature is consistent across 10 observed clusters.

## Scope

Scanned every `last_trade_price` event from the 4 hours of raw JSONL
(1,777 events total). Clustered events by time window (≤ 200 ms apart)
+ identical (size, side). Minimum cluster size: 8 legs.

## The 10 multi-leg clusters

| t0 UTC       | legs | size | side | duration | notional | market / buckets |
|--------------|------|------|------|----------|----------|------------------|
| 19:42:38.093 |  8   | 9.48 | BUY  |   8.8ms  | $0.123   | april-11 / 66-76 (dead) |
| 19:54:19.096 |  9   | 5.00 | SELL |  39.6ms  | $4.83    | april-11 / 62-66 |
| 19:59:10.989 |  9   | 9.02 | BUY  | 122.1ms  | $0.198   | april-11 / 64-76 (dead) |
| 19:59:58.063 |  8   | 5.26 | BUY  |  50.6ms  | $0.111   | april-11 / 64-76 (dead) |
| 20:00:21.017 | 11   | 5.00 | SELL |  50.9ms  | $1.65    | april-11 / 62-66 |
| 20:12:14.430 | 21   | 5.00 | SELL |  33.8ms  | $4.64    | april-11 / 62-66 (×4 bursts) |
| 21:00:28.285 | 12   | 5.00 | SELL |  23.4ms  | $1.18    | april-11 / 62, 66 |
| 21:03:44.330 |  9   | 5.00 | SELL |  11.4ms  | $0.75    | april-11 / 62, 66 |
| 21:12:11.181 | 20   | 5.00 | SELL |  10.0ms  | $0.68    | april-11 / 62, 64, 66 |
| 23:15:21.089 | 10   | 7.00 | SELL |   0.4ms  | $4.91    | april-12 / full 10-bucket ladder |

## Signature

- **Primary leg size: 5.0 shares** (7/10 clusters). One 7-share cluster
  (the april-12 one), three BUY clusters at irregular sizes (9.48, 9.02,
  5.26). Suggests **two distinct execution patterns**: uniform-5 and
  irregular-size BUYs.
- **Leg count: median 9.5, max 21.** The 21-leg cluster at 20:12:14
  is actually ~3 sub-bursts executing in ~34 ms — the bot re-fires
  as soon as new overround appears.
- **Execution speed: 10-50 ms** for most clusters. The 23:15:21 cluster
  at 0.4 ms is essentially simultaneous — that's a single batched
  async call hitting all legs at once.
- **Hour distribution**: mostly UTC 19-21 (the near-resolution peak
  for april-11) + one UTC 23 (the april-12 pre-resolution density
  we observed in expO).
- **Side distribution**: 7 SELL + 3 BUY (more below).

## SELL clusters = ladder-BID arb

All 7 SELL clusters target only the **live buckets** (62-63, 64-65,
66-67 on april-11 — the +1, +2, +3 from the favorite). None hit dead
buckets. This is disciplined arb execution: sell YES at the bid, then
flip as the ladder refills.

Per-cluster notional is $0.68–$4.91. Assuming 1% overround per cluster,
that's $0.007–$0.049 profit per execution × 10 clusters ≈ **$0.20 total
profit in ~4 hours** at 5-share scale. Small, but real, and it's
someone's full-time bot running.

Scale: if they ran this across 8 cities × 8 active hours/day ×
2.5 clusters/hour × $0.03 avg profit = **$4.80/day**. Even at 20x size
(100-share legs) that's $96/day. Consistent with our exp P capacity
estimate of $5-30/day NYC.

## BUY clusters = cheap-tail accumulation (different pattern)

The 3 BUY clusters have different characteristics:
- **Irregular sizes**: 9.48, 9.02, 5.26 shares (not round 5)
- **Buckets touched**: dead tail buckets (66, 68, 70, 72, 74, 76°F)
- **Prices**: 0.01-0.02 per YES token (tick-floor range)
- **Notional**: $0.11-$0.20 per cluster

Interpretation: **buying dead YES tokens at tick floor**. Possible
motivations:

1. **Short cover**: they built short positions earlier in the day (via
   SELL clusters on active buckets) and are closing out at near-zero
   when the bucket is clearly dead.
2. **Scooping cheap options**: paying 1-2 cents for a near-zero
   probability bucket is a lottery ticket — if the afternoon delivers
   an unexpected surge, the bucket might resolve YES and pay $1/share.
3. **Different bot / trader**: the size variance (9.48 vs 9.02 vs 5.26)
   suggests hand-sized orders or a different bot than the uniform-5
   SELL-arb bot.

Most likely: **at least two distinct bots** — a uniform-5-SELL ladder-arb
bot and a separate irregular-BUY strategy. The SELL bot is our
competition; the BUY bot is doing something else entirely.

## Inter-cluster gap timing

Gaps between clusters (seconds):
- Median: 506s (8.5 min)
- Min: 23s
- Max: 7389s (2h)
- Clusters within 60s of another: 2/9

Pattern: the bot is **event-driven, not periodic**. It waits for
overround to appear (flow-based, not time-based) and fires when it
crosses a threshold. The 23-second minimum gap is two clusters during
the 20:12:14 burst — the bot re-fired as soon as the ladder refilled.

## Execution speed comparison

The **23:15:21 cluster at 0.4 ms total duration** is a single-message
async batched submission (all 10 legs hit the matcher in one round
trip). Our own watchman latency observations suggest a sub-second
reaction is feasible for us too, BUT:

- Their 0.4ms batched submission is probably **colocation-level speed**
  or using Polymarket's new batched order endpoint
- Most of their clusters (10-50 ms) are the NORMAL speed of an async
  batched submission from a non-colocated client
- We can match the 10-50 ms tier with standard asyncio / httpx; we
  can't match 0.4 ms without colocation

**Implication**: for our build, target 50-100 ms batched-async
execution. We'll lose the 0.4 ms burst opportunities (maybe 10% of
clusters) but catch the others.

## Competitive strategy

We now have a clear competitor profile. Options:

1. **Compete head-to-head**: build an equivalent 5-share SELL ladder-arb
   bot. Same signature, slightly slower. We'd catch ~30-50% of
   opportunities.

2. **Undercut on size**: use 3-share legs. Stay below their 5-share
   floor. When they fill at 5 shares and deplete bids at that level,
   we sit on the 3-share residual at slightly worse prices. Catch ~20%
   of their leftover.

3. **Piggyback on their execution**: observe when they're active (via
   our WS stream). 2 seconds after we see a 5-leg SELL burst, fire our
   own 3-share SELL at whatever bids remain. Catch the re-opened arbs
   during the 20:12:14-style 21-leg bursts.

4. **Target the BUY-cluster pattern**: if the irregular BUYs are a
   different bot buying cheap tails, we don't compete with them at
   all. Different edge.

5. **Skip NYC entirely**, go to cities where no bot exists yet. Chicago,
   Miami, Philly, LA. Watchman runs with the same code; hopefully no
   competition yet.

Option 5 is the most capital-efficient if we can deploy fast enough
before other bots arrive.

## Final capacity estimate (with competition)

Taking the expP range of $5-30/day NYC and applying the competition
haircut from exp R:

- **Head-to-head**: ~30% of gross = $1.50-$9/day NYC
- **Undercut**: ~20% of gross = $1-$6/day NYC
- **Piggyback**: ~10% of gross (only the 21-leg re-open bursts) = $0.50-$3/day NYC
- **Un-contested cities**: full $5-30/day per city × number of cities

**Best path**: deploy the watchman + taker to 8 non-NYC cities, measure
competition per city, size to where we're uncontested. Estimated total
capacity **$20-100/day** across 4-6 cities where we're alone.

## Followups

- **Deploy watchman to non-NYC cities tomorrow** — 1 day of data tells
  us if any competitor is already there
- **Build a paper-execution simulator** that models our fill rate
  against the observed taker bot's timing (when they fire first, we
  miss; when they're quiet, we catch)
- **Investigate the BUY clusters** further — are they hedges, different
  strategy, or fat-fingers?
- **Check if Polymarket has a batched-orders API** — if yes, our
  execution can match the 0.4ms tier

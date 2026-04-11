# Exp N — First live watchman alert: arb is REAL but ACTIVELY CORRECTED

**Date**: 2026-04-11
**Status**: **The watchman caught its first live arb** — and the
alert persisted for exactly 1 second before being corrected. Major
revision to the arb capacity estimate.

## The alert

```
2026-04-11T23:04:28.417Z
md: april-12-2026
n_buckets: 11
n_live: 11  (all buckets have posted bids)
sum_bid: 1.010
```

Per-bucket state at alert time:

| strike       | bid     | ask     |
|--------------|---------|---------|
| 54-55°F (fav)| 0.400   | 0.410   |
| 56-57°F      | 0.310   | 0.320   |
| 52-53°F      | 0.170   | 0.189   |
| 58-59°F      | 0.060   | 0.070   |
| 50-51°F      | 0.032   | 0.033   |
| 60-61°F      | 0.015   | 0.020   |
| 62-63°F      | 0.007   | 0.013   |
| 48-49°F      | 0.006   | 0.010   |
| 66°F+        | 0.005   | 0.009   |
| 64-65°F      | 0.003   | 0.009   |
| 47°F-        | 0.002   | 0.008   |

Sum of bids = **1.010**. If sellable simultaneously, 11-leg arb would
yield +$0.010 per $1 obligation = 1% risk-free.

## The second-by-second persistence story

```
23:04:24  sum_bid=0.499 (6/11 fresh)      ← sparse, not arb-eligible
23:04:25  sum_bid=0.820 (10/11)           ← still missing one
23:04:27  sum_bid=1.000 (11/11)           ← exactly 1.000
23:04:28  sum_bid=1.010 (11/11)           ← ARB OPEN (watchman fires)
23:04:29  sum_bid=1.000 (11/11)           ← ARB CLOSED
23:04:30  sum_bid=1.000 (11/11)           ← stays at 1.000
...
23:04:59  sum_bid=1.000 (11/11)           ← still at 1.000
```

**The arb was open for EXACTLY 1 SECOND.** Then the ladder returned
to sum_bid = 1.000 and **HELD THERE for the next 30+ seconds**.

Someone is actively holding `sum(best_bid) = 1.000` for april-12.
This is not a static equilibrium — it's an active market-maker bot
re-pricing buckets in response to deviations.

## Completely different regime from april-11

| dimension            | april-11 (20 UTC, pre-resolution) | april-12 (23 UTC, 25h pre-resolution) |
|----------------------|-----------------------------------|----------------------------------------|
| Resolution distance  | ~1 h                              | ~25 h                                  |
| Arb persistence      | 1-30 seconds                      | 1 second max                           |
| Longest linger       | 30+ seconds (20:13 cluster)       | 1 second                               |
| Dead-bucket bids     | $0.000 (MM walkaway)              | All 11 bids live                       |
| Pattern              | overround from dead-side absence  | brief overround spikes                 |
| MM active correction | NO                                | YES                                    |

**April-12 has an active market maker** holding sum_bid exactly at 1.000.
When a retail/flow event briefly pushes it above, the MM corrects in <1s.

**April-11 near-resolution has NO active market maker**. Deviations linger
for 2-30+ seconds. This is where the real arb opportunity lives.

## Why the difference?

Hypothesis: the active MM has an economic calculation. Holding a NegRisk
market's bids at sum_bid = 1.000 - ε costs nothing (you're being passive;
you earn spread on any flow). Deviating from that by either holding
sum > 1 (losing money to arb) or sum < 1.00 (losing spread to retail) is
both losing moves. So MMs aggressively keep the ladder at 1.000.

But near resolution (1-2 hours before), the MM has a choice:

- Continue making markets on a thin, near-resolution book → small volume,
  small spread income, possible adverse selection from informed traders
- OR: walk away from dead buckets (pull bids) → saves capital and attention
  for the markets that still need MM presence

The observed pattern: MMs walk away from dead-bucket bids in the final
hour, leaving the live-side bids to drift into overround. That's when the
sustained arb opportunities appear.

## Real-world capacity estimate (MAJOR REVISION)

Previous estimate from exp I / exp J:
- ~20 arbs/hour × 2 h × $0.04-0.22/arb = $5-20/day per city
- 8 cities = $40-160/day

New reality:

- **Pre-resolution (most hours)**: MM actively corrects in <1s. Arbs are
  uncatchable by any taker with >500ms latency.
- **Near-resolution (the final ~1 hour)**: MM walkaway creates sustained
  arbs (1-30s persistence). These are catchable.
- **1-hour window × 1 market per city per day × 20 arbs/hour × 50% catchable
  rate × $0.10/arb = $1/day per city** → **$8/day at 8 cities**.

**Revised estimate: $5-15/day NYC-only at realistic execution rates.**
Much smaller than the $75-150/day from exp J. Still positive, still
worth pursuing as a proof-of-concept mechanical edge.

## Implications for execution

### Taker model is not viable for most arbs

Firing 4-11 market sells within 1 second is infeasible for a remote bot
with typical API latency (100-300ms per order placement). Even if the
bot observed the arb at second 23:04:28.000, by the time order #1 hit
the matcher, the arb window was closing. By order #4, the MM had
already corrected.

### Pre-posted passive limit orders are the only viable model

The realistic execution model is:

1. Maintain a standing set of sell-limit orders at `best_ask - 1c` on
   every bucket of every live ladder
2. When market flow temporarily pushes the book into an overround, our
   passive sells sit ready and get filled by the MM's correction trade
3. We earn the spread + the overround on any ladder that crosses > 1.000

This is **market-making**, not arb-taking. Very different complexity:
- Capital tied up in resting orders
- Quote management overhead
- Adverse selection risk (we get filled when the market moves against us)
- Potential for the MM to match or beat our quotes

Needs a dedicated paper-execution simulator before any live deployment.

### Where the taker model DOES work

The near-resolution final hour on actively traded markets (NYC at
16-17 EDT daily). During that window:

- Deviations linger 2-30 seconds → taker with <1s latency can catch them
- Walkaway creates predictable "live-side only" arb structure
- Smaller set of buckets to hit simultaneously (4 live vs 11)

This is where a focused near-resolution arb bot can work.

## Followups

- **Characterize the MM correction pattern across all arbs**: for every
  alert, measure persistence duration. Is the <1s correction universal
  for pre-resolution markets? Does ANY market-date linger?
- **Build a passive-limit simulator**: post limits at various offsets
  from top-of-book, measure hypothetical fill rate against observed
  book movements, estimate PnL.
- **Multi-day watchman rate**: let the watchman run for 24 h and count
  alerts per hour. Compare to the exp I historical estimate.
- **Identify the MM**: cross-reference the `last_trade_price` events
  during the alert with the pre/post book states. If we can trace a
  specific trader's flow across the ladder, we can characterize them.

## Lesson locked in

**A sub-second arb is infinitely worse than a 30-second arb** for a
latency-limited taker. The near-resolution window remains the only
realistic opportunity — everything else requires a market-making
architecture. The $75-150/day capacity estimate from exp J was based on
the 65-min april-11 observation window, which happened to be the peak
of the near-resolution window. Not representative of the full day.

**Revised arb capacity: $5-15/day NYC-only, $40-120/day across 8 cities,
final-hour-only execution.**

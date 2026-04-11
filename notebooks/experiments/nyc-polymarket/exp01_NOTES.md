# Exp 01 — Temperature → price response function

**Script**: `exp01_temp_to_price_response.py`
**Date**: 2026-04-11
**Status**: first pass complete; ready for iter 2 follow-ups

## Setup

For every 1-min LGA `tmpf` change (|Δ| ≥ 1°F) during the local "hot window" (10-21 EDT), match every NYC daily-temp strike whose `local_day` = that tick's local date AND whose threshold is within ±4°F of current `tmpf`. Look up the slug's `yes_price` at the tick minute and at t+10s, t+30s, t+1m, t+3m, t+5m.

This is the **response function**: given a 1-min temp move, what does the neighborhood of strike prices do?

**Event count**: 11,855 tick×strike events, 241 slugs, 38 days.

## Key findings

### 1. Aggregate response is near-zero

Mean Δprice by (direction × magnitude):

| dir | \|Δtemp\| | n    | dp_5m    |
|-----|----------|------|----------|
| +1F | 1        | 3393 | +0.0001  |
| +1F | 2        | 115  | -0.0011  |
| -1F | 1        | 2911 | +0.0011  |
| -1F | ≥3       | 5    | +0.0544  |

At the aggregate, +1°F and -1°F moves produce essentially no price response. **But** that masks the real structure — reactions exist, they're just paired and symmetric.

### 2. Real structure lives in distance-to-strike × kind

`or_higher` strikes, warming events only:

| dist (°F) | n   | p0    | dp_5m_warm |
|-----------|-----|-------|------------|
| -4        | 37  | 0.22  | +0.003     |
| -3        | 45  | 0.26  | +0.005     |
| -2        | 29  | 0.46  | -0.006     |
| **-1**    | 41  | 0.69  | **+0.015** |
| 0         | 27  | 0.68  | +0.007     |
| +1        | 19  | 0.99  | +0.002 (locked) |

**The only robust single-bucket signal: `or_higher` strikes with tmpf 1°F below threshold reacting to a warming tick — +1.5¢ over 5 minutes.** That's the bucket right at the knife's edge. n=41 is marginal but directionally clear.

Range strikes show tiny mean moves everywhere: ±1¢ max. The canceling is brutal — every boundary crossing is a zero-sum pair (winner +X¢, neighbor -X¢, mean ≈ 0).

### 3. The raw reactions are huge (and fast)

Top-15 single-tick reactions (5-min window):

| ts (UTC) | strike | prev→tmpf | dp_5m |
|----------|--------|-----------|-------|
| 2026-03-29 19:53 | 52-53°F | 52→51 | **-0.489** |
| 2026-03-29 19:53 | 50-51°F | 52→51 | **+0.520** |
| 2026-04-06 20:27 | 54-55°F | 54→55 | +0.440 |
| 2026-03-04 18:54 | 48°F or higher | 48→47 | +0.419 |
| 2026-04-06 20:41 | 54-55°F | 54→53 | +0.413 |
| 2026-03-04 18:54 | 46-47°F | 48→47 | -0.399 |
| 2026-03-21 18:58 | 56-57°F | 56→55 | -0.357 |

Beautiful pair at 2026-03-29 19:53: tmpf dropped 52→51. The 52-53 strike lost 49¢, the 50-51 strike gained 52¢ — near-perfect zero-sum book rebalance in under 5 minutes. The market is actively recomputing the running-max distribution on every tick; no latency for a human to exploit.

### 4. Why the aggregate is zero but raw is large

Boundary crossings produce paired moves. Summing across all strikes at a given minute nets to ~0 (the probability mass just redistributes between buckets). So the mean "response" is small, but the *conditional-on-boundary* response is 30-50¢.

To extract edge, you'd need to **pick the winning SIDE** of a redistribution before it happens. That means a predictive signal about tomorrow's peak direction, not a sniping signal about today's tick.

## Edge candidates surfaced (not yet tested)

1. **`or_higher` dist=-1 warming**: +1.5¢ avg over 5m; could be profitable if the position entry cost is <1¢ in fees/slippage. Needs a bigger sample to confirm.

2. **Boundary-crossing directional bet**: when running max = X and tmpf starts climbing with strong trend (3+ consecutive up minutes), buy the X+1 to X+2 range strike ahead of the market fully rebalancing.

3. **"Follow the running max"**: at 14:00 EDT, check the running max so far. That's the *provisional* winner. Compare to market favorite. If they disagree, trade toward the running max.

## Next iteration (iter 2) angle

**"Follow the running max"** — test the naive strategy where at snapshots through the day (12, 14, 16, 18 EDT) we take the running-max-whole-F as our predicted day-max and check whether the corresponding range strike is mispriced relative to a uniform prior over ±2 buckets. This is the simplest possible predictive strategy and gives a floor for "can any simple temperature-derived rule beat the market?"

## Data usage notes

- ASOS 1-min LGA coverage is gappy: many days have only 500-1200 valid minutes out of 1440. The hot-window (10-21 EDT) filter partially mitigates this.
- Subsetting to |Δt|≥1°F and |dist|≤4°F keeps the event space tractable — 11k events instead of millions.
- Per-event price lookups via correlated subqueries run ~30s on this machine. Acceptable for exploration; if we go bigger, consider ASOF JOIN.

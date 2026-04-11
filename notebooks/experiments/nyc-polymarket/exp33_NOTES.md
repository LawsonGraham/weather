# Exp 33 — V6 movement filter (mixed result, drop the rule)

**Script**: `exp33_v6_movement_filter.py`
**Date**: 2026-04-11
**Status**: V6 (skip flat favorites at entry hour) does NOT cleanly
improve over V1 baseline. Drop the rule from the deployable set.

## Setup

Exp32 found that 15 days (27% of 55) had favorites that stayed within
±5¢ of their 12 EDT price all day, with 0% hit rate. Hypothesis: skip
those days at entry to boost Strategy D's hit rate.

Implemented as a real-time check: at the entry hour (14 or 16 EDT),
check the favorite's price movement since 12 EDT. If `|drift| < 5¢`,
skip. Otherwise enter the +2 bucket.

## Results

| variant                                  | n  | hit_rate | cum_pnl |
|------------------------------------------|----|----------|---------|
| 14 EDT V6 (|drift| ≥ 5¢)                | 25 | 36%      | +38.06  |
| 14 EDT V6 (|drift| ≥ 10¢)               | 14 | 29%      | +8.92   |
| **14 EDT V1 baseline (no skip)**         | 34 | 32%      | +35.56  |
| 16 EDT V6 (|drift| ≥ 5¢)                | 21 | 43%      | +74.04  |
| 16 EDT V6 (|drift| ≥ 10¢)               | 19 | 42%      | +69.91  |
| **16 EDT V1 baseline (no skip)**         | 24 | 42%      | **+81.93** |

**At 14 EDT**: V6 marginally helps (cum 38 vs 36, hit 36 vs 32%). Not
significant.

**At 16 EDT**: V6 marginally HURTS (cum 74 vs 82). The skip rule throws
away 3 winning days.

## Why V6 doesn't translate

Exp32 measured drift over the FULL 12→18 EDT window (6 hours).
Real-time entry can only see drift over 2 hours (12→14) or 4 hours
(12→16). At those shorter windows, fewer days look "flat" — by 14 EDT
only 9 days are <5¢-flat, by 16 EDT only 3.

The "flat favorite" pattern from exp32 only crystallizes when you've
seen the full afternoon. At that point you can't act on it (you'd
already need to be in the trade). It's a postdiction, not a prediction.

## Decision

**Drop V6 from the deployable set.** It's not worse than V1 at 14 EDT
but it's worse at 16 EDT, and the cleaner V1 rule is easier to execute.

The exp32 flat-favorite finding is still informative — it tells us
something about the structure of losing days (cheap favorites with no
volume) — but it's not directly tradeable as an entry filter.

## What does help

The deployable set remains:
- **V1 @ 18 EDT** (best risk-adjusted, n=15, 53% hit, positive median)
- **V1 @ 16 EDT** (best base-case, n=24-28, 42-46% hit, large cum)
- **V5 @ 12 EDT** (morning entry with skip-dry rule, n=19, 42% hit)

V6 is dropped. Move to operational work.

## Queued

- **Exp 34**: Saturday deep-dive (75% hit rate is suspiciously high
  on n=4)
- **Exp 35**: HRRR comparison (still blocked, ~80% complete)
- **Exp 36**: paper-trade JSON ledger
- **Live data refresh script** (Gamma + METAR pull)

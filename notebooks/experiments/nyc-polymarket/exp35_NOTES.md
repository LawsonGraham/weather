# Exp 35 — Winning bucket emergence (the market is structurally wrong)

**Script**: `exp35_winning_bucket_emergence.py`
**Date**: 2026-04-11
**Status**: Major structural finding. The market never reliably identifies
the eventual winning bucket even after the peak has occurred. This
validates Strategy D's "spread your bets" implicit logic and points at a
multi-bucket basket as the next refinement.

## Setup

For each of 34 scoring days, identify the eventual WINNING bucket
(the strike whose [lo, hi] contains day_max). Then:

1. At each hour 06/08/10/12/14/16/18 EDT, was the market's argmax
   favorite the actual winning bucket?
2. What's the average price of the eventual winner at each hour?
3. When does the winner first emerge as fav?

## Result 1 — Market's hit rate as a winner-picker

| hour     | n  | winner is fav | pct |
|----------|----|---------------|-----|
| 06 EDT   | 34 | 11            | 32% |
| 08 EDT   | 34 | 13            | 38% |
| **10 EDT** | 34 | **15**          | **44%** ← peak |
| 12 EDT   | 34 | 10            | 29% |
| 14 EDT   | 34 | 12            | 35% |
| 16 EDT   | 34 | 10            | 29% |
| 18 EDT   | 34 | 13            | 38% |

**Even at 18 EDT after the peak has passed, the market's favorite is
the actual winning bucket only 38% of the time.** On 62% of days,
the winning bucket is somewhere else in the ladder, undervalued.

The peak market accuracy is **44% at 10 EDT**, then it DROPS in the
afternoon as traders update their views (sometimes wrongly).

This is consistent with the universal upward bias: the market keeps
picking the wrong bucket because it keeps under-weighting the
afternoon rise.

## Result 2 — Winner price trajectory

Mean price of the eventual winner at each snapshot:

| hour     | mean winner price |
|----------|-------------------|
| 06 EDT   | 0.248             |
| 08 EDT   | 0.264             |
| 10 EDT   | 0.273             |
| 12 EDT   | 0.266             |
| 14 EDT   | 0.294             |
| 16 EDT   | 0.342             |
| **18 EDT** | **0.379**       |

**The winner climbs from 25¢ to 38¢ but never crosses 50%.** The
market gets gradually more confident in the winner over the day, but
even after the peak has occurred, it only assigns 38¢ probability.

**Implication**: a trader who could identify the winner at 18 EDT
could buy at 38¢ and earn 1/0.38 - 1 = **+1.6x ROI**. Better than
Strategy D's per-bet payoff (~5x but at 31% hit rate, expected
return ~1.55x).

## Result 3 — Winner emergence trajectory

| hour winner first becomes fav | n  |
|-------------------------------|----|
| **06 EDT** (already fav at open) | **11** |
| 08 EDT                        | 3  |
| 10 EDT                        | 2  |
| 14 EDT                        | 3  |
| 16 EDT                        | 2  |
| 18 EDT                        | 3  |
| **never** (winner never the fav) | **10** |

Three regimes:
- **32% easy days** (11): the market gets it right from the start.
  Strategy D doesn't help here — winner = fav, +2 bucket misses.
- **29% impossible days** (10): the market NEVER converges on the
  winner. Strategy D might catch some by buying +2.
- **38% gradual days** (13): winner emerges through the day. This
  is where late-hour entries add value.

## Key insight: Strategy D rides a "spread bet" pattern

Even at 18 EDT, no single bucket has > 50% probability. The market
spreads its mass across 4-6 plausible buckets. Strategy D explicitly
picks ONE of those buckets (the +2 offset). When that pick happens to
be the eventual winner, we win big.

This is structurally different from "fade the favorite" — we're not
betting against the market, we're betting with it on a specific
candidate that's getting under-weighted by ~60% of probability mass.

## Refinement: targeted multi-bucket basket?

If the winner is at most 38¢ even after the peak, and there are ~5
plausible candidates per day, a targeted basket could improve:

**Strategy E (revised)**: at 14 EDT, identify the running max temp
±2°F. Buy YES on every range strike whose `lo` is in
[running_max-2, running_max+3]. ~5 strikes, total cost ~40¢, payoff
$1 if any hits.

Expected math (back-of-envelope):
- Hit rate of basket: probably ~70-80% (some bucket in the band wins)
- Payoff per win: $1 / $0.40 = 2.5x
- EV per bet: 0.75 × 2.5 - 1 = +0.875

Strategy D V1 EV: 0.31 × (1/0.16) - 1 ≈ +0.94 (similar)

The basket might offer LOWER variance (more frequent smaller wins)
vs Strategy D's higher-variance lottery. Worth testing.

Queue as exp36.

## Implication for the deployment

Strategy D V1 is the simplest tradeable rule given the data. The
basket variant (exp36) is more complex but potentially smoother.
Both ride the same underlying truth: **the market never crosses 50%
confidence on the actual winner, even after the peak has happened**.

For the live deployment plan: keep V1 as the primary, paper-trade
14 days, then (depending on results) test the basket variant.

## Counter-narrative — could 62% be correct?

The 62% "winner is not fav" stat seems shocking. Sanity check: with
~10 buckets per day and base rate 10% per bucket, a perfect predictor
would have hit rate 100% on the favorite. A random predictor would
have ~10% hit rate. The market's 44% (peak) is between these — it's
better than random but far from perfect.

The reason: the day_max rounds to a specific integer that's hard to
predict to ±1°F precision. The market's "favorite" is a 4-bucket
window that's "approximately right" but rarely "exactly right."

Strategy D rides this slop systematically.

## Queued

- **exp36**: targeted multi-bucket basket strategy
- **exp37**: HRRR (still blocked at ~87%)

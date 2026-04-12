# NBS Uncertainty as a Day-Selection Filter

**Date**: 2026-04-12

## Hypothesis

When NBS forecast uncertainty (txn_spread_f) is high, the market is
more likely to misprice → Strategy D should have a bigger edge.

## Results — +2°F offset (Strategy D V1)

| NBS spread | n | hit rate | cum PnL | per trade |
|---|---|---|---|---|
| low (≤1°F) | 80 | 35.0% | +$171 | $2.14 |
| med (1-2°F) | 79 | 29.1% | +$58 | $0.74 |
| **high (2-3°F)** | **28** | **42.9%** | **+$153** | **$5.45** |
| very high (>3°F) | 11 | 9.1% | +$3 | $0.26 |

**High uncertainty (2-3°F) is the sweet spot**: 42.9% hit rate and
$5.45/trade — **2.8× the unfiltered $1.94/trade.** The market is most
mispriced when NBS is moderately uncertain.

Very high uncertainty (>3°F) is bad — when the forecast is confused,
so is everyone, and the noise dominates.

## Results — +4°F offset (Strategy D V2)

| NBS spread | n | hit rate | cum PnL | per trade |
|---|---|---|---|---|
| low (≤1°F) | 74 | 6.8% | +$76 | $1.03 |
| **med (1-2°F)** | **76** | **11.8%** | **+$339** | **$4.45** |
| high (2-3°F) | 26 | 7.7% | +$43 | $1.66 |
| very high (>3°F) | 3 | 0% | -$3 | -$1.00 |

**Medium uncertainty (1-2°F) is the sweet spot for +4F**: $339 of the
$455 total comes from this regime alone.

## Optimal filtered strategies

| strategy | filter | n | hit | per trade | total |
|---|---|---|---|---|---|
| +2°F, spread 2-3°F | high uncertainty | 28 | 42.9% | **$5.45** | $153 |
| +4°F, spread 1-2°F | medium uncertainty | 76 | 11.8% | **$4.45** | $339 |
| +2°F, unfiltered | — | 198 | 32.3% | $1.94 | $385 |

The filtered strategies trade fewer times but with dramatically higher
per-trade returns. The unfiltered version still has higher TOTAL PnL
because it catches more opportunities.

## Why the uncertainty filter works

When NBS spread is 2-3°F:
- The model is saying "I'm not sure if the high will be 70°F or 74°F"
- The market crowd, which doesn't look at NBS, tends to anchor on the
  current reading (which is typically lower than the peak)
- The +2 bucket (covering the higher end of the uncertainty range) wins
  more often because NBS's uncertainty range brackets the actual peak

When NBS spread is ≤1°F:
- High confidence → the market is also confident → less mispricing
- Still profitable but smaller edge

When NBS spread is >3°F:
- The model doesn't know what's going to happen
- Neither does anyone else
- No informational advantage

## Caveat

Sample sizes are small (11-80 per uncertainty bucket). The 42.9% hit
rate at high uncertainty has a 95% CI of [24%, 63%] on 28 observations.
Need more data before deploying a filtered strategy with confidence.

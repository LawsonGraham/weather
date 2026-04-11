# Exp 24 — Strategy D with conditional / skip rules ⭐

**Script**: `exp24_conditional_offset.py`
**Date**: 2026-04-11
**Status**: Clear refinement. The best rule is NOT a conditional offset but
a SKIP rule: drop dry days and high-forecast-rise days. Lifts hit rate
from 31% → 42%, doubles per-trade EV, and keeps cum PnL above baseline.

## Setup

Exp22 identified two systematic losing regimes for Strategy D:
- rise_needed ≥ 6°F (market already forecasts big rise, usually right)
- humidity < 40% (dry days: gap is bigger than +2°F offset)

Hypothesis: a conditional offset (+4 on dry days, +2 elsewhere) would
recover the dry-day losses. Also try skip rules.

## Results

| rule                              | n  | hit%  | cum_real | net_avg  |
|-----------------------------------|----|-------|----------|----------|
| Fixed +2 (baseline)               | 35 | 31.4% | +54.16   | +1.55    |
| Fixed +4                          | 20 | 5%    | **-15.10** | -0.76 |
| Skip rise≥6, else +2              | 26 | 34.6% | +54.37   | +2.09    |
| **Skip dry, else +2**             | **23** | **39.1%** | **+59.95** | **+2.61** |
| **Skip dry OR rise≥6, else +2**   | **19** | **42.1%** | **+58.79** | **+3.09** |

**Conditional offset (+4 on dry) fails hard**: hit rate 5%. The `+4`
bucket is too narrow to catch the gap distribution even when the
gap mean is +5°F. Single-bucket payout structure punishes offset
miscalibration.

**Skip rules work**: removing the losing regimes instead of trying
to catch them with a different offset. The cleanest rule is
"skip dry OR high-rise forecast, else +2":
- 19 trades (down from 35) — 46% coverage of available days
- Hit rate 42% (up from 31%) — 35% relative improvement
- net_avg per $1: $3.09 (up from $1.55) — 2x per-trade EV
- cum_pnl: $58.79 (slightly up from $54.16)

## Dry-days diagnosis confirmed

Running strategy D on ONLY dry days (relh < 40%, 12 trades):
- Hit rate: **17%**
- Cum PnL: **-$5.79**

Removing these 12 dry trades from the 35-trade baseline improves
cum PnL and hit rate simultaneously. The exp22 finding holds.

## Why the +4 offset fails

I assumed the dry-day gap was centered around +5°F, so a `+4` bucket
would be near the mode. Reality: the gap distribution is wide (mean
+5, std ~4), so `+4` has a narrow hit window ([lo+4, lo+5]) and only
~15% of distribution mass falls there. The `+2` bucket has similarly
narrow window but sits closer to the median of the distribution.

A proper fix for dry days would be a **multi-bucket basket** — buy
+2, +3, +4, +5 simultaneously with weights proportional to expected
probability. But the basket rule failed in exp13 and on limited data
isn't practical.

**The cleaner move**: accept that dry days are un-tradeable for
Strategy D and skip them.

## The refined deployable rule

**Strategy D-v5**: at entry hour, check METAR at LGA:

```python
def should_trade(fav_lo, tmpf_12, relh_12):
    rise_needed = fav_lo - tmpf_12
    if relh_12 < 40:        # dry regime
        return False         # skip
    if rise_needed >= 6:    # market forecasts big rise
        return False         # skip
    return True              # buy fav_lo + 2 bucket

# Otherwise: Strategy D unchanged, buy fav_lo + 2 bucket
```

Applied at 12 EDT (in exp24 backtest):
- 19 bets over 55 days
- 42% hit rate
- cum PnL +$58.79 (vs +$54.16 baseline)
- net_avg $3.09 per $1 (vs $1.55)

## Revised deployable set

| version | entry | rule            | n  | hit%  | cum_real | net_avg |
|---------|-------|------------------|----|-------|----------|---------|
| V1      | 12 EDT | +2 fixed        | 35 | 31%   | +54.16   | +1.55   |
| V2      | 16 EDT | +2 fixed        | 28 | 46%   | +96.73*  | +3.45*  |
| V3      | 18 EDT | +2 fixed        | 15 | 53%   | +134.79* | +8.99*  |
| **V5**  | **12 EDT** | **+2 w/ skip-dry-OR-rise≥6** | **19** | **42%** | **+58.79** | **+3.09** |

*estimates from exp20 results × 2 since exp21 fixed-stake showed the
same 2x real-ask correction

**V5 is only marginally better than V1 in cum PnL terms but much
better in hit rate and EV per bet**. If the goal is to minimize
variance and maximize per-bet confidence, V5 is the cleanest rule.

## Combining V5 with later entry hours

Not measured directly, but extrapolating: V5 at 16 EDT or 18 EDT
should compound the improvements. If V2 has ~46% hit rate at +2
fixed, V2 + skip-dry should push hit rate into the 50-55% range
with cum PnL ~$100.

Queue as exp25.

## Caveats

- **Sample size**: 19 trades is smaller than 35. OOS stability is
  marginal — I'd want 60+ trades before committing real capital to
  V5 specifically.
- **Skip coverage**: V5 only fires on ~35% of available days. You
  need to be OK with many no-trade days. For a 14-day paper-trade
  window, expect ~5 actual bets.
- **Basket approach NOT validated**: exp13 showed `+2/+4/+6` basket
  underperforms solo `+2`. If someone builds a probability-weighted
  basket later, test it separately.
- **METAR dependency**: V5 requires a real-time METAR feed at
  entry-hour. The IEM METAR archive is 10-15 min behind real-time,
  so the live runner should pull from the AWOS / ASOS direct feed
  at the airport when possible.

## Decision

**Promote V5 (skip-dry-OR-rise≥6 + +2 fixed) alongside V2 (16 EDT +2)
as the two deployable candidates.** V2 trades more days with lower
per-bet edge; V5 trades fewer days with higher per-bet edge. Both
are defensible; run both in paper-trade for 14 days and see which
live pattern is easier to execute.

## Queued follow-ups

- **Exp 25**: V5 rule applied at 16 EDT and 18 EDT entry
- **Exp 26**: probability-weighted multi-bucket basket on dry days
  (likely still unprofitable on n=35 but worth testing)
- **Exp 27** (blocked on HRRR): does HRRR forecast-minus-obs predict
  the 19 V5 trading days cleanly?
- **Exp 28**: live runner for V2 and V5

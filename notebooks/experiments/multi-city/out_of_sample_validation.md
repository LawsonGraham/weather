# Out-of-Sample Validation: Geographic Holdout

**Date**: 2026-04-12

## Design

- **Train**: NYC only (where Strategy D V1 was discovered)
- **Test**: 10 other US cities (Atlanta, Austin, Chicago, Dallas, Denver, Houston, LA, Miami, SF, Seattle)
- **Strategy**: buy the favorite+2 bucket at 16 EDT, 0.6% fee

## Results

| set | n | hit rate | cum PnL | per trade | warm bias |
|---|---|---|---|---|---|
| Train (NYC) | 16 | 37.5% | +$7.39 | $0.46 | +4.0°F avg, 70% above |
| **Test (10 cities)** | **182** | **31.9%** | **+$377.33** | **$2.07** | **+3.8°F avg, 60% above** |

## Interpretation

The Strategy D V1 edge **transfers geographically**:

1. **Warm bias replicates**: +3.8°F on test vs +4.0°F on NYC. 60% of
   test days resolve above the favorite (vs 70% NYC).

2. **Hit rate stays above breakeven**: 31.9% on test vs 37.5% on NYC.
   Slight degradation but well above the ~14% average implied probability
   of the +2 bucket.

3. **Per-trade PnL is HIGHER on test**: $2.07 vs $0.46. Less-traded
   cities have wider mispricing → more alpha per trade.

4. **The edge is structural, not city-specific**: driven by the universal
   pattern of markets underestimating afternoon temperature peaks.

## Caveats

- Both train and test use the same time period (Mar-Apr 2026). A proper
  temporal holdout wasn't possible because hourly prices_history data
  only covers recent months.
- Sample sizes are small (16 train, 182 test). 95% CI on test hit rate:
  [25.1%, 38.7%].
- Miami dominates test PnL (+$192 of $377). Remove Miami and test PnL
  drops to +$185 — still solidly positive.
- Strategy D V1 is a heuristic, not an optimized model. It was discovered
  as a structural observation, not fit to training data.

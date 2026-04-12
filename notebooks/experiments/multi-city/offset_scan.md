# Strategy D Offset Scan: +4°F Beats +2°F

**Date**: 2026-04-12

## Full offset scan across 11 US cities

| offset | position | n | hit rate | cum PnL | per trade |
|---|---|---|---|---|---|
| -4°F | fav-2 buckets | 87 | 0.0% | -$87 | -$1.00 |
| -2°F | fav-1 bucket | 156 | 10.9% | -$95 | -$0.61 |
| +0°F | **favorite** | 224 | 35.3% | **-$71** | -$0.32 |
| **+2°F** | **fav+1 bucket (Strategy D V1)** | 198 | **32.3%** | **+$385** | **+$1.94** |
| **+4°F** | **fav+2 buckets** | 179 | **8.9%** | **+$455** | **+$2.54** |
| +6°F | fav+3 buckets | 124 | 4.8% | +$276 | +$2.22 |

**The +4°F offset wins on total PnL ($455 vs $385) AND per-trade PnL
($2.54 vs $1.94).** Hit rate is much lower (8.9% vs 32.3%) but the
cheap entries ($0.03-0.08 avg) provide massive upside multipliers
when the bucket wins.

## Key findings

**Below the favorite LOSES MONEY at every offset.** The -2 bucket's
"underpricing" I found in the calibration analysis was wrong when
tested against METAR ground truth. Buying below the favorite is a
losing strategy.

**The favorite itself loses money** despite a 35% hit rate — you pay
the highest price for a bucket that wins only 1/3 of the time.

**The sweet spot is +2°F to +6°F above the favorite.** All three
offsets are profitable. The optimal depends on risk tolerance:
- +2°F: most consistent (32% hit rate), moderate returns
- +4°F: highest total PnL, larger returns per trade, high variance
- +6°F: even higher variance, smaller total (fewer trades)

## Robustness without Miami

| strategy | ex-Miami n | ex-Miami PnL | ex-Miami per trade |
|---|---|---|---|
| +2°F (Strategy D V1) | 169 | +$193 | $1.14 |
| **+4°F** | **153** | **+$222** | **$1.45** |

**+4°F still wins excluding Miami** ($222 vs $193, $1.45 vs $1.14).
The edge is driven by Chicago (+$67), Seattle (+$136), and SF (+$82).

## Per-city at +4°F offset

| city | n | hit | PnL | avg entry |
|---|---|---|---|---|
| Miami | 26 | 7.7% | +$233 | $0.024 |
| Seattle | 24 | 16.7% | +$136 | $0.028 |
| SF | 7 | 42.9% | +$82 | $0.054 |
| Chicago | 16 | 18.8% | +$67 | $0.046 |
| NYC | 13 | 15.4% | +$16 | $0.080 |
| LA | 14 | 7.1% | -$6 | $0.077 |
| Denver | 12 | 0.0% | -$12 | $0.055 |
| Austin | 13 | 0.0% | -$13 | $0.047 |
| Houston | 14 | 0.0% | -$14 | $0.064 |
| Dallas | 22 | 4.5% | -$16 | $0.059 |
| Atlanta | 18 | 0.0% | -$18 | $0.033 |

+4°F works best in volatile-weather cities (Chicago, Seattle, Denver
region) and warm-bias cities (Miami). It loses in stable-weather
cities (Atlanta, Houston, Austin) where the temperature rarely
deviates 4+°F from the forecast.

## Combined portfolio

Buying BOTH +2°F and +4°F on every tradeable day:
- +2°F: +$385 on 198 trades
- +4°F: +$455 on 179 trades
- **Combined: +$840 on ~377 trades**

These are NOT independent — winning days tend to win both. But the
diversification lowers variance (a +2°F win partially offsets a +4°F
loss on the same day).

## Implication

Strategy D V2 should be the +4°F offset (fav+2 buckets), not +2°F
(fav+1 bucket). Or even better: buy both for a combined portfolio.

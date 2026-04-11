# Exp 02 — Follow the running max (naive strategy floor)

**Script**: `exp02_follow_running_max.py`
**Date**: 2026-04-11
**Status**: first pass complete; caveats listed below; seeded exp03

## Setup

At four afternoon snapshots (12 / 14 / 16 / 18 EDT), compute 1-min LGA `running_max` in whole °F. Map to the range strike whose `[lo, hi]` contains that running max. Backtest: "buy the running-max bucket at snapshot price; score `realized_yes/p - 1` per $1." Baseline: "follow the market's argmax range strike at the same snapshot."

Scored against METAR hourly + 6-hr RMK daily max (truth).

## Headline numbers

Running-max strategy (range strikes only):

| Snap    | n_bets | avg_entry | hit_rate | avg_return | cum_pnl  |
|---------|--------|-----------|----------|------------|----------|
| 12 EDT  | 30     | 0.116     | 10%      | -0.18      | -5.3     |
| 14 EDT  | 29     | 0.238     | 17%      | -0.13      | -3.7     |
| 16 EDT  | 29     | 0.320     | 31%      | **+4.90**  | **+142** |
| 18 EDT  | 30     | 0.341     | 37%      | **+52.4**  | **+1573**|

Market-favorite baseline (argmax range strike by price):

| Snap    | n  | avg_entry | hit_rate | avg_return | cum_pnl  |
|---------|----|-----------|----------|------------|----------|
| 12 EDT  | 55 | 0.40      | 18%      | **-0.63**  | **-34.5**|
| 14 EDT  | 55 | 0.41      | 22%      | +5.46      | +300     |
| 16 EDT  | 55 | 0.55      | 18%      | +5.24      | +288     |
| 18 EDT  | 55 | 0.64      | 24%      | +5.29      | +291     |

## Big caveats

1. **1-min running max is systematically undercounted.** Distribution of `day_max - running_max` per snapshot:

   | Snap    | Δ=0 | Δ=1 | Δ≥2 |
   |---------|-----|-----|-----|
   | 14 EDT  | 7   | 4   | 31  |
   | 16 EDT  | 13  | 6   | 17  |
   | 18 EDT  | 12  | 5   | 15  |

   33% of 18 EDT snapshots have `day_max - rmax ≥ 2°F` — impossible if 1-min running max were accurate (by 18 EDT the day is past peak). This is the **ASOS 1-min gap issue** at work: many days have 500-1200 valid minutes out of 1440, and entire afternoon hours can be missing.

2. **The "cum_pnl 1573 at 18 EDT" is a distorted outlier.** A handful of sub-1¢ winners explode the cumulative return. Median return per bet is almost certainly negative. Means are dominated by skew.

3. **Market favorite at 12 EDT loses 63¢ per $1.** This is the genuine finding — early-afternoon market favorites are systematically wrong. The market is still learning from HRRR / morning observations and its point estimate is biased. **This is where a model has room.**

4. **Market favorite at 14+ EDT earns 5x per $1 on a mean basis** — skew-dominated, same caveat as running-max. The hit rate is only ~20%, so these are also a-few-big-wins-amongst-many-losses strategies, not durable edge.

## What "follow the running max" actually tells us

**It does NOT give clean alpha on this data** — returns are dominated by a few outliers where 1-min happened to see a peak minute cheap AND the market missed it. That's not a durable signal, that's lottery.

**But it confirms the qualitative story:** the market is mispricing something at 12 EDT, and by 14-18 EDT it's converged. If we want model-driven edge, we want to be entering positions around 09-12 EDT, not later. Consistent with exp01 and the earlier pre-cross efficiency curve.

## Disagreement table — surprising rows

At 14 EDT (the hour the model-driven edge would mostly fire):

```
local_day   actual_max  rmax  fav          fav_p
2026-03-04   50          47    46-47°F       ?    (1-min saw 47, actual 50)
2026-03-11   70          70    44-45°F       0.002
2026-03-16   55          55    58-59°F       0.33   ← market thought 58, was 55
2026-03-27   68          68    66-67°F       0.998  ← market right; rmax right
2026-03-31   80          78    56-57°F       0.001  ← gap day; 1-min badly trailing
2026-04-07   53          54    52-53°F       0.60   ← 1-min spike above actual
```

The 2026-03-16 case is interesting: 1-min rmax and actual both 55, but market has 58-59 at 33%. The actual max was 55 (in 54-55 bucket), market's favorite was wrong. **That's alpha.**

## Seeds for next experiments

- **Exp 03: rebuild this test with METAR-derived running max** (hourly tmpf + 6-hr RMK), which is reliably complete. That removes the 1-min gap confound and makes the strategy numbers trustworthy. Then decide if there's durable edge.

- **Exp 04: morning market-favorite fade.** The finding that 12 EDT favorites lose 63¢ per $1 is the most concrete unfilled gap. Make a NON-directional version: at 12 EDT, short the market favorite (sell YES). Score. If it wins, the market has a systematic 12 EDT mispricing that fades.

- **Exp 05: HRRR-based morning signal** (once HRRR backfill finishes). That's the "real" version of the morning alpha thesis.

## Cost/value summary

- 1 script + 1 notes file. ~40s end-to-end DuckDB.
- Exp02 result: 1-min data is too gappy to be a trustworthy live-temp signal source. Either wait for a better data feed (Synoptic, Wunderground, whatever Polymarket uses) or switch to METAR hourly.
- Directional finding worth remembering: **12 EDT market favorites are systematically wrong.** This shows up in exp02 baseline (-0.63/$1) and is consistent with the pre-cross efficiency curve from the main backtest.

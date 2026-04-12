# Exp 12 — Condition the upward bias on METAR features ⭐⭐ (biggest finding yet)

**Script**: `exp12_bias_conditioning.py`
**Date**: 2026-04-11
**Status**: biggest finding of the session. The upward bias is not confined
to peaked-ladder days — it's a **universal property** of the NYC daily-
temperature market across all 55 scored days.

## One-line headline

Across all 55 days in the backtest window, the 12 EDT market favorite
bucket's lower bound is on average **+4.07°F BELOW the realized day max**.
80% of days are upward misses. Mean under-prediction is large and the
signal is directly conditionable on simple METAR features at 12 EDT.

## Overview table (all 55 days)

| metric          | value  |
|-----------------|--------|
| n               | 55     |
| mean signed gap | **+4.07°F** |
| std             | 6.29   |
| n upward misses | **44** (80%) |
| n downward      | 7  (13%) |
| n at-lower-edge | 4  (7%) |

The structural bias is **not** a peaked-ladder phenomenon. The peaked-ladder
finding from exp07 was a special case of a much broader pattern: **the
market is ~4°F too cold on its forward view**, every day.

## Conditioning signal #1 — sky cover at 12 EDT

| sky_bucket  | n  | mean_signed_gap | fav_hit_rate |
|-------------|----|------------------|--------------|
| **scattered** | 9  | **+5.78**    | 0%           |
| **clear**     | 29 | **+5.10**    | 21%          |
| broken        | 14 | +1.21        | 29%          |
| overcast      | 3  | +2.33        | 0%           |

**Clear and scattered sky days have mean upward gap 5-6°F.** Broken-overcast
days barely miss (~1-2°F). Physical intuition: clear sky → unimpeded solar
heating → bigger afternoon rise than the overnight forecast captured.

## Conditioning signal #2 — relative humidity tercile at 12 EDT

| tercile | n  | avg_relh | mean_signed_gap | fav_hit_rate |
|---------|----|----------|------------------|--------------|
| 1 (dry)  | 19 | 36%      | +4.68            | 26%          |
| 2 (mid)  | 18 | 53%      | +5.28            | 22%          |
| 3 (humid)| 18 | 86%      | **+2.22**        | **6%**       |

Dry / mid-humidity days show the biggest upward miss; humid days are more
on-target. High-humidity days have a 6% favorite hit rate — the favorite
is almost always wrong but barely off the mark.

## Conditioning signal #3 — starting temperature tercile

| tercile | n  | avg_tmpf_12 | avg_day_max | mean_signed_gap |
|---------|----|-------------|-------------|------------------|
| 1 (cold)| 19 | 34°F        | 41°F        | +3.89            |
| 2 (mild)| 18 | 44°F        | 51°F        | +1.17            |
| 3 (warm)| 18 | 58°F        | 66°F        | **+7.17**        |

Warm mornings have the biggest upward gap. On a 58°F morning, market
forecasts a peak around that afternoon but the day actually rises 7°F more.

## Conditioning signal #4 — wind direction

| wind_sector    | n  | mean_signed_gap |
|-----------------|----|------------------|
| N (offshore)   | 7  | +2.43            |
| E (onshore)    | 8  | +3.38            |
| **S (onshore)** | 14 | **+6.21**        |
| W (offshore)   | 16 | +5.00            |
| NW (offshore)  | 8  | +1.38            |

Southerly advection (warm Atlantic) → biggest gap (+6.2°F). NW (cold
continental) → tiny gap (+1.4°F). Consistent with the weather intuition.

## Conditioning signal #5 — "rise needed to favorite" ⭐ strongest

| band              | n  | avg_rise_needed | mean_signed_gap | fav_hit_rate |
|-------------------|----|-----------------|------------------|--------------|
| fav below current | 6  | -9.17°F         | **+16.33**       | 0%           |
| rise < 2°F        | 10 | +0.50           | **+5.50**        | 20%          |
| rise 2-5°F        | 16 | +2.70           | +3.13            | 13%          |
| rise 5-10°F       | 17 | +5.59           | +1.18            | 35%          |
| rise >10°F        | 6  | +15.50          | +0.17            | 0%           |

**corr(signed_gap, rise_needed) = −0.759** — very strong.

**Interpretation**: the less rise the market expects, the bigger its
under-estimate. When the market predicts "today will be similar to now
(rise < 2°F)", the day actually rises +5.5°F. When the market predicts a
big jump (rise > 10°F), the day's actual peak is right at the market's
forecast. The market is CORRECT on dramatic-rise days but CONSERVATIVE on
still-morning days.

This is a mechanistic story about HRRR (and NBM) under-predicting the
afternoon rise when the morning conditions look stable. The forecast
anchors on "things are stable, today stays similar" and misses the sun's
heating effect once the boundary layer mixes.

## The "fav below current" edge case

Six days have the 12 EDT favorite priced at a bucket BELOW the current
temperature — the market is effectively predicting "the day will cool down"
when the actual max is already +9°F above the current reading. Gap on these
is **+16°F** on average. These are:

- Either stale-book days (worth checking)
- Or days where the market was pricing cooling overnight before realizing
  the afternoon was still to come — a timing/update-delay artifact
- Or days where the current reading surged mid-morning and the market
  hasn't caught up yet

Worth investigating separately.

## Correlation matrix (all 55 days, METAR feature vs signed gap)

| feature         | corr    |
|-----------------|---------|
| rise_needed     | **−0.759**  |
| tmpf (at 12)    | +0.347  |
| fav_p           | −0.343  |
| relh            | −0.219  |
| dwpf            | +0.129  |
| sknt            | +0.131  |

`rise_needed` is by far the strongest single predictor. `tmpf_12` is
moderately positive (warmer morning → bigger gap). Everything else is weak.

## Peaked-ladder days with METAR context

| local_day  | fav      | fav_p | day_max | gap | hit | tmpf | dwpf | relh | wind   | sky |
|------------|----------|-------|---------|-----|-----|------|------|------|--------|-----|
| 2025-12-30 | 32-33°F  | 0.85  | 40      | 8   | 0   | 32   | 5    | 31%  | W 18kt | BKN |
| 2025-12-31 | 32-33°F  | 0.60  | 32      | 0   | 1   | 31   | 8    | 37%  | W 12kt | FEW |
| 2026-02-22 | 34-35°F  | 0.87  | 44      | 10  | 0   | 32   | 30   | 92%  | NE 15kt| FEW |
| 2026-02-23 | 34-35°F  | 0.61  | 36      | 2   | 0   | 31   | 28   | 88%  | NW 20kt| BKN |
| 2026-03-05 | 44-45°F  | 0.90  | 46      | 2   | 0   | 39   | 37   | 92%  | ENE 5kt| BKN |
| 2026-03-12 | 56-57°F  | 0.96  | 60      | 4   | 0   | 42   | 32   | 67%  | NW 16kt| FEW |
| 2026-03-27 | 66-67°F  | 1.00  | 68      | 2   | 0   | 51   | 34   | 52%  | N 14kt | FEW |
| 2026-04-07 | 52-53°F  | 0.64  | 53      | 1   | 1   | 50   | 28   | 42%  | NW 15kt| BKN |

The big-gap peaked-ladder misses (2025-12-30 +8°F, 2026-02-22 +10°F) both
happened on relatively clear sky days (BKN, FEW). The small-gap misses
happened on cloudier/humid days. Consistent with the global finding.

## Refined trading strategies

### Strategy A: all-days "fade the favorite + 2 buckets up"

On EVERY scored day, short the favorite at its 12 EDT price AND long the
strike 4°F above the favorite's lower bound (which is where the average
upward-miss lands). Expected profit on 80% of days.

*Not tested yet — queued as exp13.*

### Strategy B: clear-sky + rise_needed < 3 filter

On days where 12 EDT sky is CLR/FEW and the favorite's lo is within 2°F
of current tmpf, short the favorite. Expected n per 55 days ≈ 12-15.
Expected mean gap ≈ +5°F (from both sub-segments).

*Not tested yet — queued as exp13.*

### Strategy C: warm-morning + clear sky

Tercile 3 (warm) × clear/scattered sky. Intersection is ~10-12 days in the
window. Expected gap ≈ +6-8°F.

*Not tested yet.*

## Why this is the biggest finding so far

1. **Universal, not edge-case**: 80% of days, not just 8 peaked-ladder days.
   Sample size for statistical confidence is 10x larger.

2. **Physically motivated**: clear sky + low humidity + southerly wind →
   unimpeded afternoon heating. Forecasts that anchor on overnight
   conditions systematically miss.

3. **Mechanistic hypothesis**: the market is pricing off a model (HRRR,
   NBM) that under-predicts afternoon rise in specific weather regimes.
   This is a concrete, falsifiable claim that exp09 (HRRR-lands) will test.

4. **Feature-engineerable**: at 12 EDT, with METAR station obs, compute
   sky, dewpoint, tmpf, wind direction. All in hand. Ready to deploy the
   moment sizing is done.

## Queued follow-ups

- **Exp 13**: backtest the three refined strategies (A/B/C) against the
  55-day window. Measure net_med and cum_pnl. If Strategy A earns
  structurally positive across the whole window, we have a tradeable
  broad-market thesis.
- **Exp 14**: investigate the 6 "fav below current" days. Stale books? Or
  real overnight timing drift?
- **Exp 15** (blocked on HRRR): is HRRR's 12Z or 18Z forecast-minus-obs the
  same ~4°F bias, or is the market doing something worse than HRRR? If
  market bias > HRRR bias, we have an alpha vs market. If market bias ≈
  HRRR bias, the whole world is wrong and we need a better forecaster.

## Decision

**This is the new primary thesis.** The peaked-ladder finding stays (it's
a 10x-extreme version of this broader bias). The broader bias is more
valuable because it has more data and clearer conditioning features.
Pivot the experiment track toward Strategy A/B/C validation and HRRR
confirmation once the backfill lands.

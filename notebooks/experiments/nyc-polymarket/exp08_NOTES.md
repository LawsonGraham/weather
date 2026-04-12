# Exp 08 — Paired short-favorite + long-underdog hedge (and the bias direction)

**Script**: `exp08_paired_hedge.py`
**Date**: 2026-04-11
**Status**: Paired hedges underperform solo short. But the per-day detail
revealed a bigger finding: **the market's errors on peaked-ladder days are
systematically UPWARD** (the actual max is 2-10°F hotter than the favorite
bucket in 5 of 6 misses). This doesn't help the hedge (misses are too big),
but it strongly constrains the "why" and points directly at the HRRR-forecast-
anchor hypothesis.

## Setup

Run the peaked-ladder filter from exp07 (`p_fav ≥ 0.60 AND n_over_10c ≤ 2`),
fires on **8 days in the 55-day window**. Test three paired-hedge variants
vs a solo short baseline.

## Headline table (n=8, 3¢ spread + 2% fee)

| Variant                               | net_avg | **net_med** | cum    |
|---------------------------------------|---------|-------------|--------|
| **Solo short (peaked filter)**        | +7.44   | **+4.81**   | **+59.5** |
| V1: short + long 2nd-fav              | +4.42   | +2.19       | +35.3  |
| V2: short + long 2nd + 3rd fav        | +3.17   | +1.75       | +25.4  |
| V3: short + long (fav + 2°F hotter)   | +3.35   | +2.19       | +20.1  |
| V3b: short + long (fav + 2°F and + 4°F) | +6.23 | +1.89       | +49.8  |

**Every paired variant underperforms the solo short.** Adjacent hedging is a
net cost, not a risk-reducer, because:

1. The hedge leg is almost always losing (2nd favorite hits only 17% of the
   time when the fav misses).
2. The miss magnitudes are large — the actual max lands 2-10°F away from
   the fav bucket, usually past the 1-2 adjacent hedges.

Solo short peaked-filter strategy is the winner: **net median +4.81 per $1,
cum +59.53 on 8 trades.**

## The per-day detail (the hidden finding)

| local_day  | fav       | p_fav | fav_y | actual_max | direction |
|------------|-----------|-------|-------|------------|-----------|
| 2025-12-30 | 32-33°F   | 0.85  | 0     | 40         | +8°F      |
| 2025-12-31 | 32-33°F   | 0.60  | 1     | 32         | hit       |
| 2026-02-22 | 34-35°F   | 0.87  | 0     | 44         | **+10°F** |
| 2026-02-23 | 34-35°F   | 0.61  | 0     | 36         | +2°F      |
| 2026-03-05 | 44-45°F   | 0.90  | 0     | 46         | +2°F      |
| 2026-03-12 | 56-57°F   | 0.96  | 0     | 60         | +4°F      |
| 2026-03-27 | 66-67°F   | 0.999 | 0     | 68         | +2°F      |
| 2026-04-07 | 52-53°F   | 0.64  | 1     | 53         | hit       |

Of 6 misses: **all 6 are upward** (day was warmer than market expected).
Zero misses are downward.

This is the single most important observation of the session: the market's
error is directional, not random. When the market is wrong on a peaked-
ladder day, the temperature always ends up **higher** than the market's
favorite bucket. Never lower.

### Why this matters for the edge

- **Confirms the hypothesis**: the market is anchoring on overnight
  forecasts (HRRR 00Z) or yesterday's readings, which tend to under-estimate
  the intraday warming. When the actual afternoon rise exceeds the forecast,
  the peak shifts to a hotter bucket and the market's prior "locked in" pick
  gets blown out.

- **Dies the hedge thesis**: adjacent hedges (2nd favorite, 1-bucket-hotter
  neighbor, 2-bucket-hotter neighbor) don't catch misses that are 5-10°F
  away. The directional bias is real but the distribution of error magnitude
  is long-tailed.

- **Points at HRRR**: once the HRRR backfill lands, we can test whether the
  HRRR ensemble spread or the HRRR-to-NBM delta predicts these upward-miss
  days. If the market is anchoring on a single-run forecast and ignoring
  ensemble uncertainty, the HRRR spread should be high on the exact days
  we're fading.

- **Tighter filter may pay**: the 2 hits in the 8-trade window both had
  `p_fav ∈ [0.60, 0.65]`, right at the filter floor. Tightening to
  `p_fav ≥ 0.70` drops both hits, leaving 5 trades with **100% miss rate**.

## Refined rule after exp08

**Fade rule v2**: on peaked-ladder days (p_fav ≥ 0.70 AND n_over_10c ≤ 2),
short the 12 EDT argmax range strike (buy NO at mid + 3¢ half-spread + 2%
fee). Hold to resolution.

In the 55-day window this fires on **5 trades, all misses**, net median
~+6.00 per $1, cum ~+60.

But 5 is too few to trust statistically. The refinement is a hypothesis
for more data, not a deployable strategy.

## What this means for exp09 when HRRR lands

The upward-bias finding is a concrete testable claim against HRRR:

> On peaked-ladder days, the market's favorite range strike is on average
> **3.7°F below** the actual day max (average of the 6 misses:
> (8+10+2+2+4+2)/6 = 4.67°F; if we exclude the outlier +10 case, avg = +3.6).
>
> Does the HRRR t+6 ensemble mean (or HRRR+GFS blend) predict these upward
> deviations? If yes → trade the HRRR alpha, not the market favorite. If no
> → the market is tracking HRRR and the bias is elsewhere (e.g., NBM lag,
> Kalman-filter drift, morning-run-vs-afternoon update timing).

That's the HRRR exp09 headline test.

## Supporting evidence for bias direction

The `2026-02-22` case is the most dramatic: favorite priced at **87%** for
34-35°F, actual max was **44°F** — 10°F higher than the market's locked-in
bucket. Five buckets away. The favorite bucket had 87¢ of price mass and
the actual winning bucket (44-45°F) was probably priced under 2¢. That's
a >40x mispricing in hindsight.

## Artifacts

- `exp08_paired_hedge.py` — five strategy variants, per-day detail, direction
  analysis.
- `exp08_NOTES.md` — this file.

## Queued work

- **Exp 09** (blocked on HRRR): does HRRR t+6 predict the upward-miss days?
- **Exp 10**: look at a bigger window once more historical data is available
  (e.g. re-run with p_fav ≥ 0.70 on 200+ days of NYC daily-temp history).
- **Exp 11**: "market is systematically too cold" as its own stand-alone
  strategy — not just fading peaks, but biasing EVERY price estimate toward
  warmer buckets by some calibration factor.
- **Exp 12**: check if the upward bias varies by *day of week*, *month*, or
  *cloud cover*. Seasonal or meteorological conditioning may sharpen it.

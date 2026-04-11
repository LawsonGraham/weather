# Exp 21 + 22 — Honest multiples + win-regime conditioning

**Scripts**: `exp21_notional_cap_sim.py`, `exp22_win_regime_conditioning.py`
**Date**: 2026-04-11
**Status**: Two important refinements. Exp21 gives honest dollar PnL numbers
that replace the compounding fiction from exp20. Exp22 reveals that dry/
clear-sky days LOSE Strategy D at the +2 offset — the bias is too big for
the +2 bucket to catch. New working thesis: CONDITIONAL OFFSET by METAR
regime.

## Exp21 — Fixed-stake honest numbers

Backtests Strategy D with a fixed $100 per-bet stake (NO Kelly
compounding) to strip out sequence dependence. All three entry hours
with real-ask costs from exp06b:

| entry    | n  | hit_rate | mean_pnl | **median_pnl** | cum_pnl    | ROC%   |
|----------|----|----------|----------|----------------|------------|--------|
| 12 EDT   | 35 | 31.4%    | +$155    | -$100          | +$5,438    | +155%  |
| 16 EDT   | 28 | 46.4%    | +$333    | -$100          | +$9,335    | +333%  |
| **18 EDT**| 15 | **53.3%**| +$773    | **+$261**      | +$11,590   | **+773%** |

**18 EDT has the first POSITIVE median PnL** we've seen on Strategy D
across the entire session. That means more than half of individual
trades at 18 EDT clear a profit — not just outliers carrying the mean.

Return on capital deployed (n × stake, across all three hours combined
= 78 trades × $100 = $7,800 of capital): **$26,363 total PnL → 338% ROC.**

This is the HONEST deployable number:
- 12 EDT alone: ~1.55x
- 16 EDT alone: ~3.33x
- 18 EDT alone: ~7.7x
- All three combined: ~3.38x (averaged)

The exp20 "120x at 18 EDT" was compounding fiction. The real late-day
number is ~7.7x RoC over 15 trades, which is still excellent but not
cartoonish.

## Exp22 — Win regime conditioning

Conditioned Strategy D's 35 conservative-entry trades on METAR features
at 12 EDT. Looking for a filter that separates 11 winners from 24 losers.

### Mean feature values by outcome

| outcome | n  | tmpf | dwpf | relh | wind | rise_needed | fav_p | d_p    |
|---------|----|------|------|------|------|-------------|-------|--------|
| loss    | 24 | 40°  | 23°  | 55%  | 10.3 | +3.79       | 0.511 | 0.196  |
| WIN     | 11 | 45°  | 30°  | 58%  | 8.8  | +4.56       | 0.514 | 0.224  |

Winners run slightly warmer, higher dewpoint, calmer wind, and marginally
higher rise_needed. Subtle differences, no single clean separator.

### Hit rate by sky cover

| sky      | n  | wins | hit_rate | net_avg |
|----------|----|------|----------|---------|
| clear    | 21 | 7    | 33%      | +1.54   |
| broken   | 13 | 3    | 23%      | +1.57   |
| overcast | 1  | 1    | 100%     | +1.51   |

Clear-sky hit rate is 33% (above the 31% average). Not a huge boost.

### Hit rate by rise_needed band — the FIRST real signal

| rise_needed | n  | wins | hit_rate | net_avg  |
|-------------|----|------|----------|----------|
| 0-3°F       | 13 | 4    | 31%      | +2.05    |
| 3-6°F       | 13 | 5    | **39%**  | **+2.13** |
| **6-10°F**  | 8  | 1    | **13%**  | **-0.55** |
| 10+°F       | 1  | 1    | 100%     | +4.16    |

**The 6-10°F band is a systematic loser** (net_avg -0.55). Drop those 8
trades from Strategy D and the remaining cum improves.

Interpretation: when the market ALREADY expects a 6-10°F afternoon rise,
the forecast is usually right — the bias doesn't fire. Strategy D's edge
lives in the "stable morning → unexpected rise" cases, not the
"forecasted big-rise" cases.

### Hit rate by humidity tercile — the SECOND signal

| tercile | n  | wins | avg_relh | hit_rate | net_avg |
|---------|----|------|----------|----------|---------|
| 1 dry   | 12 | 2    | 35%      | **17%**  | **-0.48** |
| 2 mid   | 12 | 6    | 50%      | **50%**  | **+3.23** |
| 3 humid | 11 | 3    | 85%      | 27%      | +1.93   |

**Dry days (RH ≤ 40%) LOSE Strategy D** on average (net_avg -0.48).

This contradicts the exp12 finding where dry days had the BIGGEST mean
upward gap. Resolution: on dry days the gap is ~5°F — so the actual
winner is the `+4` or `+5` bucket, NOT the `+2` bucket. Strategy D at
`+2` is too conservative for dry days; it should be at `+4` or `+5`
instead.

**The offset should be condition-dependent**:
- Humid / moderate: `+2` bucket (adjacent to the fav, small gap)
- Dry / clear: `+4` or `+5` bucket (larger gap, winner is further out)

This is the next refinement (exp24).

### Filter combinations tested

| filter           | n  | hit_rate | cum_pnl |
|------------------|----|----------|---------|
| all (no filter)  | 35 | 31%      | +54.16  |
| clear only       | 21 | 33%      | +32.31  |
| rise<6 only      | 26 | 35%      | +54.37  |
| clear + rise<6   | 14 | 36%      | +30.51  |

Filtering reduces sample size faster than it improves hit rate — the
final cum_pnl is worse even when the per-trade edge is better, because
we throw out profitable trades along with the losing ones.

**Conclusion**: filters alone don't save the strategy. What's needed is
a *dynamic offset rule* — still fire on every day, but choose the
correct offset based on the regime.

## New deployment option — CONDITIONAL OFFSET

**Strategy D-regime (working name)**:

```python
rise_needed = fav_lo - tmpf_12
if humidity_relh < 40:          # dry regime
    offset = 4                  # go further out
elif rise_needed >= 6:          # market expects big rise
    skip = True                 # don't fire
else:                           # normal
    offset = 2                  # default
```

Expected effect: removes the 8 losing 6-10°F trades, shifts dry-day
trades to a higher offset where the `+4` bucket catches the mean +5°F
gap. Needs backtesting against the 35-trade sample + ideally new data.

Queue as exp24.

## Revised deployable set

**V1 (unchanged)**: Strategy D at 12 EDT, fixed `+2` offset, real-ask
costs. 35 trades, 31% hit, $5,438 on $100/bet capital. Simplest rule,
worst returns.

**V2 (newly supported by exp21)**: Strategy D at 16 EDT, fixed `+2`
offset, real-ask costs. 28 trades, 46% hit, $9,335 on $100/bet. Better
returns, similar complexity.

**V3 (newly supported by exp21)**: Strategy D at 18 EDT, fixed `+2`
offset. 15 trades, 53% hit, $11,590 on $100/bet. **First positive
median bet**. Best ROC but smallest sample.

**V4 (queued, exp24)**: Conditional offset by METAR regime. Dry days
use `+4` offset; moderate days use `+2`; skip days where market already
forecasts 6-10°F rise. Expected to lift hit rate from 31% to 40-45%.

## Decision

Keep V2 (16 EDT, `+2` offset, real cost) as the primary paper-trade
target. It's the sweet spot: more trades than V3 (28 vs 15), cleaner
edge than V1 (46% vs 31%), simpler than V4 (fixed offset, no
conditional logic).

Paper-trade V2 for 14 live days; meanwhile run exp24 (conditional
offset) in the backtest to see if it unlocks another 10-20% hit rate.

## Queued follow-ups

- **Exp 23**: investigate the 6 "fav below current" days from exp12
  (mean gap +16°F, 0% hit rate)
- **Exp 24**: conditional offset by humidity / sky cover
- **Exp 25**: V2 live runner — script that pulls Gamma ladder at 16 EDT,
  computes +2 bucket, outputs a trade recommendation with size
- **Exp 26 (HRRR)**: HRRR-based forecast comparison once backfill lands

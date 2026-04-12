# Exp M — Strategy D V1 vs V3 vs V4 multi-day backtest (hourly data)

**Script**: `expM_stratD_V3_hourly_backtest.py`
**Date**: 2026-04-11
**Status**: Strong directional signal on a small sample (~15 days).
**Later entry time dominates earlier entry time** on cumulative PnL
per trade, though hit rate declines with delay. V4 at 18 EDT may be
optimal; needs more data to verify.

## Method

Use `data/processed/polymarket_prices_history/hourly/` — full lifetime
hourly midpoints for every closed NYC daily-temp slug. For each day's
closed ladder, compute:

- **V1**: favorite at 16 EDT (20 UTC), buy fav+2 at p16 × 1.02
- **V3**: favorite at 17 EDT (21 UTC), buy fav+2 at p17 × 1.02
- **V4**: favorite at 17 EDT, buy fav+2 at 18 EDT (22 UTC) price p18

METAR LGA daily max determines outcome. Hit = 1 if metar_max in
[lo_f, hi_f].

Filter: `p × >= 0.005` and `p < 0.97` to ensure tradable book.

## Headline

| strat   | n  | avg_entry | hit_rate | net_avg | **cum_pnl** |
|---------|----|-----------|----------|---------|-------------|
| V1-16   | 15 | 0.095     | **53.3%**| +8.87   | **+$133**   |
| V3-17   | 13 | 0.093     | 38.5%    | +14.95  | **+$194**   |
| V4-18   |  9 | 0.112     | 33.3%    | **+26.61**| **+$240** |

**Later entry → higher cumulative PnL**, even though:
- Hit rate DECLINES: 53% → 39% → 33%
- Avg entry cost is roughly flat (0.09-0.11)

**Per-trade net return EXPLODES** with delay: 8.87 → 14.95 → 26.61.

## Why later entry wins

### (1) Selection effect on "tradeable" trades

At later entries, the "still tradeable" +2 bucket population is a
different (smaller) subset of days. At 18 EDT, only 9 of the 15 V1
days still have a tradeable +2 bucket. The 6 that dropped out are
days where the +2 bucket collapsed to the floor between 16 and 18
EDT — these are days the market has decided the +2 bucket won't win.

- **V1 trades on ALL tradeable days** (including the ones that'll
  collapse). Many of those are losers.
- **V4 trades only on days where the market is STILL uncertain
  about the +2 bucket at 18 EDT**. These are days where there's
  genuine probability that the afternoon peak might hit the +2
  bucket. Smaller sample but higher quality.

### (2) Cheap entry ROI multiplier on winning trades

On days where the +2 bucket declines from 16 → 17 → 18 EDT
(market becoming more confident it won't win), the entry cost drops
too. When the bucket DOES win (surprise afternoon surge), the ROI is
massive:

Per-day delta example from the comparison table:

| day          | p16    | p17    | delta   | won |
|--------------|--------|--------|---------|-----|
| 2026-03-20   | 0.200  | 0.018  | -0.183  | **1** |
| 2026-03-24   | 0.090  | 0.015  | -0.076  | **1** |
| 2026-03-25   | 0.025  | 0.012  | -0.013  | **1** |
| 2026-03-28   | 0.095  | 0.035  | -0.060  | **1** |

**On 2026-03-20, V3 bought the +2 bucket for 0.018 while V1 bought
it for 0.200.** Both won — V1 earned ~4× on the $1 payout, V3 earned
~55×. That single trade gives V3 +$54 while V1 only gets +$4.

### (3) Information advantage

By 17-18 EDT, the afternoon temperature is mostly realized. The 16:51
UTC METAR reading (arrives around 17:00 EDT) is the critical update
that tells you whether the peak is likely past or still climbing. A
trader using that information can avoid the days where the +2 bucket
is clearly dead and concentrate on days where there's residual upside.

## Sample-size caveat

**15-day sample is tiny.** The p50 per-strategy hit rate is not
precisely measured — confidence interval on 53% / 38% / 33% at n=15/13/9
is wide. The cum PnL could be dominated by 1-2 outlier trades (like
2026-03-20's +55× win).

The hourly endpoint returns ~30-110 points per slug over the full market
lifetime, which means only ~15-30% of closed days have a clean 16/17/18
EDT timestamp. We need either a finer-grain dataset or more days to
verify robustly.

## Why is sample size so small from hourly data?

The `/prices-history interval=max fidelity=60` endpoint returns hourly
midpoints, but only ~30-110 points per slug across the FULL market
lifetime (weeks). This means on any given day, only ~1-4 hours have a
recorded price point per slug. Many days don't have a price at exactly
16:00, 17:00, 18:00 EDT.

**Mitigation**: use 1-min data when available. We have 24-h 1-min
coverage for ~42 open slugs now. As they resolve over the next days,
we'll accumulate a larger 1-min resolved-day population.

## Strategy D V3 provisional recommendation

On this sample, V3 at 17 EDT is a 46% PnL improvement over V1 (+$194
vs +$133), at the cost of a 2-trade sample reduction. V4 at 18 EDT is
a further 24% improvement ($240) on an even smaller sample (9).

**Provisional deployment plan** (pending larger sample):
1. **Keep V1 at 16 EDT as the base strategy** for days where the +2
   bucket is already at 0.20+ (liquid, high-implied-probability — the
   "favorite doesn't yet think it's over" regime).
2. **Add V3 at 17 EDT as an overlay** for days where the +2 bucket is
   below 0.10 at 17 EDT (market thinks it's dead, but sometimes
   surprises) — small size, high ROI on wins.
3. **Avoid V4 at 18 EDT** for now — sample too small and 18 EDT is
   near the resolution window, with potential liquidity risk.

## Followups

- **Re-run expM on fresh 1-min resolved-day data** once we have more
  resolved days recorded. Target: ≥ 30 days in each V1/V3/V4 bucket.
- **METAR-conditioned filter**: combine V3 with "only fire if the
  16:51 UTC METAR reading is within 3°F of the +2 bucket's lower
  bound". Should bump hit rate significantly on the restricted
  population.
- **Investigate the 2026-03-20 outlier** (+55× V3 win at p17=0.018).
  What was the favorite at 16 EDT, what was the actual peak, and did
  the market suffer a late-day surprise? The edge case that drives
  most of V3's PnL.
- **Find out how the hourly data aligns to specific hours** — is the
  endpoint returning *exactly* at :00 or is there jitter that's
  causing us to miss data?

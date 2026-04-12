# Exp 05 — fade-morning-favorite with realistic execution costs

**Script**: `exp05_fade_with_costs.py`
**Date**: 2026-04-11
**Status**: validation gate PASSED (conditionally). Signal survives realistic
costs and partial OOS split. Small sample, concentration risk. Proceed to
larger data before sizing up.

## Cost + overround facts

1. **Overround at 12 EDT is essentially 0.** Ladder sum across all 10-11 strikes per day is 1.000 ± 0.065. 41 of 51 days fall in [0.95, 1.05]. Not a single day >1.15. **The Polymarket NYC daily-temperature book is prob-normalized**, meaning the fade PnL math is clean — there's no systematic overround haircut eating the edge.

2. **Cost sensitivity for the 12 EDT fade of the argmax range strike**:

   | haircut  | n  | net_avg | **net_med** | cum    |
   |----------|----|---------|-------------|--------|
   | 0 + 0    | 44 | 24.1    | **+0.739**  | 1060   |
   | 1¢ + 1%  | 43 | 3.28    | **+0.707**  | 141    |
   | 2¢ + 1%  | 42 | 2.19    | **+0.678**  | 91.9   |
   | 3¢ + 2%  | 42 | 1.69    | **+0.634**  | 70.8   |
   | 5¢ + 2%  | 42 | 1.22    | **+0.581**  | 51.2   |
   | 7¢ + 3%  | 42 | 0.94    | **+0.517**  | 39.5   |

   The median is almost invariant to cost assumptions. At 7¢ of spread + 3% of fee (deliberately conservative), median still +0.52 per $1. **The fade edge is robust to realistic execution.**

3. **Bug correction note**: my first pass of exp05 had the spread sign flipped (subtracted spread from entry cost instead of adding). Fixing it slightly *reduced* both the mean and the median, as expected. All numbers above are post-fix.

## Out-of-sample split (60/40 by date)

42 scorable days available at 12 EDT with p_fav > 0.04:

| split | n  | avg_p_fav | miss_rate | net_med  | net_cum |
|-------|----|-----------|-----------|----------|---------|
| train | 25 | 0.54      | **88%**   | **+0.729** | +40.65  |
| test  | 17 | 0.49      | 59%       | **+0.362** | +30.16  |

- Train miss rate 88% was unusually high — that's likely the outlier batch.
- **Test still has positive median (+0.36).** Not as extreme as train, but the sign holds.
- Test cum +30 over 17 trades = +1.77 avg per trade. Still lottery-distributed but directionally correct.

**Conclusion**: the signal partially survives out-of-sample. Not an overfit.

## Concentration risk — per-trade distribution (42 bets, 3¢+2% costs)

| percentile | return |
|-----------|--------|
| min       | -1.00  |
| p10       | -1.00  |
| **p25**   | **+0.13** |
| **p50**   | **+0.63** |
| p75       | +1.00  |
| p90       | +4.17  |
| max       | +30.63 |

- **Bottom quartile (p25) is still +0.13** — majority of individual trades net positive even in the bottom tier.
- Top 5 bets (12% of sample): +30.6, +13.4, +6.5, +5.2, +4.4 = **+60 units = 85% of cum PnL**.
- Losing trades (p10–min) lose 100% each (favorite hit, fade's NO paid zero).
- **This is partly lottery-distributed edge**: outliers matter a lot, but the bottom quartile still wins. Not pure lottery — not pure skill.

## Top 5 outlier trades — what do they look like?

| local_day  | fav strike   | day_max | p_fav | net_ret |
|------------|--------------|---------|-------|---------|
| 2026-03-27 | 66-67°F      | 68      | 0.999 | **+30.6** |
| 2026-03-12 | 56-57°F      | 60      | 0.962 | +13.4   |
| 2026-03-05 | 44-45°F      | 46      | 0.900 | +6.5    |
| 2026-02-22 | 34-35°F      | 44      | 0.871 | +5.2    |
| 2025-12-30 | 32-33°F      | 40      | 0.850 | +4.4    |

Pattern: these are all cases where the 12 EDT market was **>85% confident** in a bucket that turned out **2-10°F away** from the realized max. The market was anchoring on a very-confident wrong answer, and the actual day's max drifted way above the pricing bucket. Plausible causes:

- **Cold-morning anchoring**: on cold mornings, the market prices the current temp range as likely, but forecasts a much bigger afternoon rise than the market is factoring in. HRRR should catch this.
- **Forecast update timing**: market was priced before the 12Z HRRR refresh came through, traders hadn't updated yet.
- **Thin book**: high-confidence days may have minimal afternoon trading volume, so early-morning bids freeze.

Three of five top-outliers are in early March. If HRRR says "big warm front" and the market hasn't digested it, the whole strike distribution can get caught offsides.

## Top-3 fade variant (92 trades, 3¢+2%)

| n_bets | avg_p  | miss_rate | net_avg | **net_med** | net_cum |
|--------|--------|-----------|---------|-------------|---------|
| 92     | 0.38   | 75%       | 0.74    | **+0.297**  | +68.1   |

Fading the top-3 priced strikes (not just argmax) gives more trades but lower per-bet return (0.30 vs 0.63). Roughly the same cum PnL because of the count-up. The over-commitment pathology is broader than argmax-only but the net-per-bet is weaker.

## Read of the overall edge

**Net assessment**: there IS a real directional bias. The 12 EDT market is systematically over-confident in its argmax range strike for NYC daily-temperature markets, by ~2x its own pricing (avg p_fav 0.52 vs actual hit 24%). Fading it at realistic cost shows median +0.63/$1 with top-5 outliers carrying 85% of cum PnL.

**Caveats to keep flagged**:

- **n=42 is small.** Every number above has wide confidence intervals. A 17-day OOS cut has n=17. Summer data will probably look quite different — HRRR forecast quality and afternoon-max variance both change seasonally.
- **Concentration = lottery-adjacent.** The top 5 outliers are real trades, but if you stop-loss them or skip high-p-fav days the median drops fast.
- **No Polymarket fee data verified.** The 2% fee is a placeholder — actual NegRisk fees may be higher or tiered by size, and maker rebates may change the math.
- **No bid/ask from prices parquet** — the 3¢ haircut is a proxy. If actual NO-side spreads during 12 EDT liquidity windows are 5-7¢ we're at median +0.52 (still profitable but less impressive).
- **Signal decay over time.** Train cum +40 vs test cum +30 suggests the edge might be fading as the market gets more efficient. 17-day test is not long enough to say.

## Next experiments

- **Exp 06**: per-trade fee/spread lookup from actual fills. Instead of flat 3¢, reconstruct the 12 EDT bid/ask from recent fills +/- 5 minutes. If most days have tight spreads, the 3¢ is conservative. If a few days have 10¢+, the edge is thinner than advertised.
- **Exp 07**: ladder-shape analysis. Compute skewness/kurtosis/FWHM of the price distribution per day. Test whether fade works better on days with narrower or wider ladders. If narrow-ladder days fade better, that's a filter that can boost net_med.
- **Exp 08**: pair the fade with a long leg — "fade the argmax, buy the 2nd/3rd favorite". If the whole distribution is over-committed, buying the underdog at the same time hedges the worst outcome (temperature actually DOES land in the favorite bucket) while paying less total than pure short.
- **Exp 09**: once HRRR backfill lands, compute `HRRR_implied_prob - market_price` per strike at 12 EDT. If the overlap with current fade candidates is high, we've localized WHY the market is wrong and can stop guessing.

## Decision so far

- **Keep the fade-morning-favorite thesis on the shortlist.** It's the first signal that clears median-positive with costs and partially survives OOS.
- **Do not deploy.** Sample too small, concentration too high, one-regime backtest.
- **Invest effort in exp06/07/08 before exp09**, because those sharpen the signal; HRRR will confirm the *why*, but exp06-08 work on the data we already have.

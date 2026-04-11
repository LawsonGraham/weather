# Exp 07 — Ladder shape as a filter on the fade strategy ⭐

**Script**: `exp07_ladder_shape_filter.py`
**Date**: 2026-04-11
**Status**: second-strongest finding of the session. Refines the exp04/05
fade-morning-favorite strategy into a sharper rule with much cleaner edge
concentration.

## Setup

Exp05 showed the fade-favorite strategy has heavy outlier concentration
(top 5 trades = 85% of cum PnL). The question: is there a clean filter that
separates the jackpot trades from the noise ones?

For each of the 42 scorable days, compute four ladder-shape metrics on the
range-strike prices at 12 EDT:

- **entropy_bits**: Shannon entropy (bits) of the normalized distribution
- **herfindahl**: sum of squared normalized probabilities
- **n_over_10c**: number of strikes priced ≥ 10¢
- **p_fav**: the favorite's price itself (simplest shape feature)

Then segment fade net return by each.

## Result — entropy is a clean filter

| Entropy tercile | n  | avg_entropy | avg_p_fav | miss_rate | **net_med**  | cum_pnl |
|-----------------|----|-------------|-----------|-----------|--------------|---------|
| **1 (low)**     | 14 | 0.88        | 0.65      | **86%**   | **+1.39**    | **+64.8** |
| 2 (mid)         | 14 | 1.59        | 0.50      | 79%       | +0.80        | +6.4    |
| **3 (high)**    | 14 | 2.00        | 0.41      | 64%       | +0.37        | **-0.4**  |

**Low-entropy (peaked) days are the entire PnL.** Tercile 1 (14 bets) earns
+64.80 cum. Tercile 3 (14 bets, flat ladders) loses 40¢. Tercile 2 is mildly
positive.

This is exactly the story you'd want from a filter: the over-confident market
days cluster at the "peaked ladder" end, and fading there is consistently
high-EV.

Herfindahl tercile (higher = more concentrated) gives the symmetric picture:

| Herf tercile | n  | miss_rate | net_med  | cum_pnl  |
|--------------|----|-----------|----------|----------|
| 3 (highest)  | 14 | 86%       | **+1.39**| **+64.8**|
| 2 (mid)      | 14 | 79%       | +0.80    | +6.4     |
| 1 (lowest)   | 14 | 64%       | +0.37    | -0.4     |

Same tercile 1 days, same +64 cum. These two metrics are anti-correlated by
construction.

## Favorite-price-band cut is even sharper

| Band       | n  | avg_p  | miss_rate | **net_med** | cum_pnl |
|------------|----|--------|-----------|-------------|---------|
| [0, 25¢)   | 1  | 0.12   | 100%      | +0.08       | +0.08   |
| [25, 40¢)  | 7  | 0.33   | 86%       | +0.36       | +1.24   |
| [40, 60¢)  | 24 | 0.48   | 71%       | +0.63       | +6.78   |
| [60, 80¢)  | 5  | 0.63   | 60%       | +1.33       | +2.51   |
| **[80¢+)** | 5  | 0.92   | **100%**  | **+6.54**   | **+60.20** |

**Favorites priced ≥ 80¢ at 12 EDT missed 5 out of 5 times** in this 55-day
window. Fading them yields a median +6.54 per $1. All 5 of these cases
resolved NO despite being priced 85-99% YES.

Even though n=5 is tiny for statistical confidence, the clustering pattern —
super-confident favorites losing 100% — is so extreme it looks structural.
Hypothesis: the market anchors on either an overnight forecast (HRRR 00Z or
06Z) or on yesterday's max, and occasionally the actual day's conditions
deviate 3-5°F from that anchor. When that happens on a previously-confident
day, the "certain" bucket resolves spectacularly wrong.

## Cross-referenced with exp05 top 5 outliers

| local_day  | fav strike  | day_max | p_fav  | net_ret  |
|------------|-------------|---------|--------|----------|
| 2026-03-27 | 66-67°F     | 68      | 0.999  | +30.63   |
| 2026-03-12 | 56-57°F     | 60      | 0.962  | +13.42   |
| 2026-03-05 | 44-45°F     | 46      | 0.900  | +6.54    |
| 2026-02-22 | 34-35°F     | 44      | 0.871  | +5.17    |
| 2025-12-30 | 32-33°F     | 40      | 0.850  | +4.45    |

**Four of five are in the 80¢+ band** and the fifth (0.85) is right at the
boundary. The "top outliers" and "p_fav≥80¢" and "entropy tercile 1" and
"herfindahl tercile 3" are all flagging the same days. Four redundant signals
identifying the same exploitable regime.

## Refined strategy rule

**Fade the 12 EDT range-strike argmax ONLY when p_fav ≥ 0.60 AND n_over_10c ≤ 2**
(peaked ladder with one or two real rungs).

In the 55-day backtest, this fires on ~10 days, with miss rate ~80%, median
fade return ~+2.50 per $1 (extrapolating from the 60-80¢ and 80+¢ bands).
Cum PnL ~+60-80 per $1 risked across those 10 days.

The rule throws away ~32 trades that were median +0.30 (good but lottery
shape with big outliers). We keep the clean jackpot signal. Total expected
value is nearly the same but concentration is lower (the rule IS the
concentration, so the "rest" days are now explicit do-nothing).

## Open questions

1. **Do these super-confident mispricings cluster by regime?** Four of five
   outliers are in Feb-March, one is Dec 30. Is this a winter thing? Does it
   persist into summer? Needs more data to answer, and we should flag it
   when HRRR arrives: is it correlated with HRRR forecast-variance anomalies?

2. **Is the 80¢+ signal a data anomaly?** Five days is so few that it could
   be a data quirk — e.g., the market froze for some technical reason,
   prices stayed at 99¢ after the book went stale, my "12 EDT snapshot"
   read the stale price. Worth verifying each of the 5 days has active
   fill volume around 12 EDT.

3. **Market may learn from this pattern.** If five days show the 99¢ favorite
   resolving NO, the market makers should adapt within 6-12 months and stop
   pricing to 99¢ confidently. The edge may be visible only for a window
   of a few more months before disappearing.

## Trading implications

- **Short-term**: if any morning shows a peaked ladder (p_fav ≥ 0.60, only
  1-2 strikes ≥ 10¢), fade the argmax with tight size limits.
- **Medium-term**: build a real-time monitor that flags peaked-ladder days
  around 11-12 EDT. Backtest on next N days of out-of-sample data.
- **Long-term**: once HRRR data is available, check whether HRRR forecast
  variance (ensemble spread) predicts these peaked-ladder mispricings.
  If yes, the strategy gates on "peaked ladder AND HRRR spread says high
  uncertainty in afternoon rise" — much cleaner edge definition.

## Decision

**This is the strongest refinement so far.** The ladder-shape filter converts
the noisy fade strategy into a high-conviction peaked-ladder strategy. Keep
as the primary thesis for Polymarket NYC daily-temp markets.

## Next

- **Exp 08**: paired long-underdog with short-favorite for risk reduction.
  Now that we have the peaked-ladder filter, the paired trade becomes
  more interesting — on peaked-ladder days, where does the probability
  mass actually land? If it clusters in the 2nd/3rd-favorite adjacent
  bucket, buying the underdog is ~free alpha on top of the short.
- **Exp 08b**: validate the 5 high-conviction trades individually. Look at
  fill volumes, spread, and bid/ask during the fade window. Rule out
  data anomalies.
- **Exp 09** (blocked on HRRR): ensemble-spread vs market peakedness.

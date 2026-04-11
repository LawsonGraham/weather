# Exp 04 — Fade the morning market favorite ⭐ (THE finding)

**Script**: `exp04_fade_morning_favorites.py`
**Date**: 2026-04-11
**Status**: strongest signal found so far. Needs out-of-sample and fee/slippage
validation before taking it to production.

## Premise

Exp02 baseline showed the `follow-favorite` strategy earns -0.63/$1 at 12 EDT (55 days, 18% hit rate). That's a massive negative — which means the *inverse* trade (fade the favorite, buy NO) should earn ~+0.67/$1 if the market's overround isn't eating it.

**Direct test**: at each morning snapshot, sell YES on the argmax range strike. Buy NO at `1 - p_fav`. Return per $1 invested = `(1 - y) / (1 - p_fav) - 1`.

## Headline table

| Snap    | n  | avg_fav_p | **miss_rate** | long_yes_avg | **fade_avg** | **fade_med** | fade_cum_pnl |
|---------|----|-----------|---------------|--------------|--------------|--------------|--------------|
| 10 EDT  | 55 | 0.38      | **73%**       | -0.38        | +6.96        | **+0.14**    | +383         |
| 11 EDT  | 55 | 0.39      | **76%**       | +5.55        | +20.4        | **+0.37**    | +1122        |
| 12 EDT  | 55 | 0.40      | **82%**       | -0.63        | +19.3        | **+0.47**    | +1060        |
| 13 EDT  | 55 | 0.42      | **80%**       | -0.61        | +12.4        | **+0.41**    | +683         |

**Read the median column, not the mean.** The median per-bet return for fading the 12 EDT favorite is **+0.47 per $1 invested** — more than 50% of individual fade trades are positive. This is not driven by penny-strike outliers (those would leave the median at 0 or negative while mean explodes). It's a structural mispricing.

## Why this is real (and why it's big)

At 12 EDT the argmax range strike trades at an average `p = 0.40`. The base rate for any single range strike (10-11 rungs per day) is ~9%. The "favorite" gets a ~2x boost over base rate via the market's current information. But its **actual win rate** is only 18% — meaning the market is over-confident by 2.2x on its own favorite.

A correctly-calibrated market would have `hit_rate = avg_fav_p = 0.40`. Instead we see 0.18. The mispricing sign is **consistently in the same direction** (always over-confident), not random noise.

The over-confidence pattern holds across filter thresholds:

| p_fav ≥ | n  | avg_fav_p | miss_rate | fade_avg | fade_cum |
|---------|----|-----------|-----------|----------|----------|
| 0.30    | 40 | 0.53      | 75%       | 26.5     | +1060    |
| 0.40    | 34 | 0.57      | 74%       | 31.1     | +1058    |
| 0.50    | 22 | 0.64      | 73%       | 47.9     | +1054    |
| 0.60    | 10 | 0.77      | **80%**   | 104.9    | +1049    |

Even at the 60¢+ "high-confidence favorite" tier, 8 out of 10 missed. Ten trades is tiny but the direction is clear.

## Broader variant: fade EVERY strike with p12 ≥ 0.30

| n  | avg_entry | miss_rate | fade_avg_ret | fade_cum |
|----|-----------|-----------|--------------|----------|
| 59 | 0.472     | 73%       | 17.97        | +1060    |

So the over-commit pathology is not just about *the single argmax* — any range strike priced above 30¢ at 12 EDT is also a sell. This rules out an explanation like "argmax has a tie-break artifact."

## What this probably is

Three candidate explanations:

1. **Market over-weights "intraday trend so far"**: mid-morning tmpf at 12 EDT is already decent (say 55°F on a clear day with GFS/HRRR projecting 65). Overnight traders price the 54-55 strike at 40% even though the true probability (from HRRR ensemble spread) might be 15%. The market anchors on "feels right for what's already here" and under-weights the spread in the afternoon rise. **This is our working hypothesis.**

2. **Pricing model latency**: market is pricing off stale data (e.g., yesterday's HRRR, not today's 06Z run). By 12 EDT the fresh HRRR would have resolved the bias, but the LP / maker bots haven't caught up.

3. **Overround / market structure**: maybe Polymarket has a structural ~10-15¢ overround on range strikes (sum of ladder > 1.05). Part of the fade edge gets eaten by the overround on exit. Needs the total-ladder check.

## Required validation before trading

- [ ] **Out-of-sample**: split by date, train on first 35 days, test on next 20, confirm the pattern survives.
- [ ] **Overround check**: compute `SUM(p)` across each day's ladder at each snap. If the ladder sums to 1.10-1.15 on average, half the fade edge is paid as maker spread.
- [ ] **Bid/ask modeling**: the `fade_avg` uses mid/last-trade price. Real fills would pay the ask on NO (=1 − bid on YES). Re-compute with `best_ask` for NO side (when present), haircut by 1-2¢.
- [ ] **Fee model**: Polymarket NegRisk charges on each trade. Build a minimal fee model and subtract per-trade.
- [ ] **Seasonality**: the current 55-day window is Jan-Apr 2026. Does the bias persist in summer when daytime high windows are narrower? Can't test until we have summer data.
- [ ] **Per-day Brier comparison**: confirm the fade strategy reliably beats "don't trade" on a per-day basis, not just on the aggregate.
- [ ] **Concentration risk**: 11 trades at p_fav ≥ 0.60 — is a clean subsample, or is it 3-4 outlier days driving the headline number?

## Why this is worth protecting

This is the first finding in the session that has a **positive median per-bet return**. Every earlier result showed mean-positive / median-zero "lottery" behavior. Median-positive means the strategy is directionally correct on a majority of individual bets, not reliant on outliers to pay the losers.

If the out-of-sample check holds and overround/fees are under ~8¢ per round trip, this is a **deployable strategy** for morning-of-day entry into NYC Polymarket daily-temperature markets. 55-day window gave +1060 units of cumulative return per $1 risked, which is enormous but dominated by the mean-vs-median gap.

**Do not over-claim the magnitude until validated.** But do claim the direction: morning Polymarket NYC daily-temp favorites are systematically over-confident.

## Next steps queued

- exp05: overround + bid/ask + fee modeling → realistic fade PnL
- exp06: out-of-sample split check
- exp07: per-day ladder shape over time (skewness, kurtosis, fit to Gaussian) — this feeds a richer version of the fade, e.g., "fade the bucket that's a 3σ outlier in its own ladder"

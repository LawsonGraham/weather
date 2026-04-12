# NBS (NBM) Forecast vs Market: The Definitive Test

**Date**: 2026-04-12
**Scope**: 262 resolved trading days, 11 US cities, 1 NBS forecast per day

## The question

Does the US government's best temperature forecast (NBM/NBS) outperform
Polymarket's crowd-sourced daily high prediction? If yes, a trading
strategy using NBS to pick buckets should beat one using market signals
alone.

## Result: NBS is 18% more accurate than the market

| city | n days | market MAE | NBS MAE | market bias | NBS bias | winner |
|---|---|---|---|---|---|---|
| Atlanta | 30 | 2.7 | **2.6** | +1.8 | +1.4 | NBS |
| **Austin** | 18 | 4.2 | **1.6** | +3.3 | +0.0 | **NBS by 2.6×** |
| **Chicago** | 30 | 6.4 | **4.4** | +5.9 | +3.6 | **NBS** |
| Dallas | 30 | 3.1 | **2.9** | +1.8 | +0.3 | NBS |
| **Denver** | 18 | 5.2 | **3.5** | +3.5 | +0.3 | **NBS** |
| **Houston** | 18 | 1.6 | **1.0** | +0.9 | -0.4 | **NBS** |
| LA | 18 | **2.1** | 2.8 | +0.6 | -0.9 | market |
| **Miami** | 30 | 1.7 | **1.4** | +1.4 | +0.4 | **NBS** |
| NYC | 23 | **3.6** | 4.2 | +1.7 | +1.1 | market |
| **SF** | 18 | 4.3 | **3.3** | +4.1 | +2.7 | **NBS** |
| Seattle | 29 | **2.5** | 2.7 | +2.1 | +2.1 | market |
| **ALL** | **262** | **3.4** | **2.8** | **+2.5** | **+1.1** | **NBS** |

**NBS wins 8 of 11 cities on MAE.** The 3 cities where the market is
more accurate (LA, NYC, Seattle) are the most-traded, most-efficient
markets. NBS dominates in less-traded cities (Austin 2.6×, Chicago,
Denver, Houston).

Per-day head-to-head: NBS closer to actual on **40%** of days, market
on **33%**, ties **27%**.

## Trading strategy test: buy the NBS-predicted bucket

For each resolved day, buy the bucket containing NBS's forecast max
temp at its 16 EDT market price. Compare to Strategy D V1 (buy fav+2).

| metric | NBS-bucket strategy | Strategy D V1 |
|---|---|---|
| Total PnL | **+$288.50** | **+$384.72** |
| Trades | 91 | 198 |
| **Per-trade avg** | **$3.17** | $1.94 |
| Hit rate (where traded) | varies, up to 64% | 31.8% |
| Without Miami | **-$4.24** (loses!) | +$192.51 |

**NBS is 63% better PER TRADE** ($3.17 vs $1.94) but trades less than
half as often (91 vs 198) because the NBS-predicted bucket doesn't
always exist in the market's ladder.

**Critical caveat**: NBS's positive total PnL is ENTIRELY dependent
on Miami (+$293 of the $289 total). Without Miami, NBS loses money.
Strategy D without Miami is still +$193 — much more robust.

## What drives the NBS edge

**Bias correction.** The market has a persistent +2.5°F warm bias
(underestimates the daily high). NBS has only +1.1°F bias. The 1.4°F
difference means NBS predicts a bucket ~1 position higher than the
market favorite, which turns out to be correct more often.

**NBS uncertainty signal (txn_spread_f).** Average spread: 1.6-2.5°F
by station. High-spread days = model is uncertain → market is more
likely to be wrong → bigger potential edge. Haven't formally tested
this as a filter yet.

## Why NBS doesn't translate directly to dominant trading P&L

1. **Bucket granularity mismatch**: NBS might predict 73°F, but the
   nearest bucket is 72-73°F. If the actual is 74°F, the 74-75°F
   bucket wins and NBS's "close" forecast still loses the trade.

2. **Low trade count**: the NBS-predicted bucket is sometimes outside
   the market's active ladder (no bucket at that temperature), so no
   trade is possible. 91 of a possible 262 days = 35% participation.

3. **Miami dominance**: one outlier-dependent city drives the total.
   Strategy D is more diversified across cities.

## Implications for strategy design

**NBS is a real signal but not a standalone strategy.** The optimal
approach is likely a COMBINATION:

1. **Use NBS to SELECT which bucket to buy** — on days where NBS
   disagrees with the market favorite by ≥2°F, buy the NBS bucket
   instead of fav+2
2. **Use Strategy D V1 as the fallback** — on days where NBS agrees
   with the market (or the NBS bucket doesn't exist), buy fav+2
3. **Use NBS spread (txn_spread_f) to SIZE** — bet bigger on
   high-uncertainty days where NBS and market disagree

This combined strategy should capture NBS's per-trade edge (+$3.17)
while maintaining Strategy D's participation rate (198 trades).

**Estimated combined PnL** (rough, not backtested): on the 91 days
where NBS fires, use NBS (+$289). On the other 107 Strategy D days,
use Strategy D ($385 - ~$96 overlap ≈ $289). Combined: ~$500-600
total on 198 trades.

**This needs proper backtesting with train/test splits before
trusting.** But the signal is real and measurable.

## Method notes

- NBS forecast: `txn_f` field from the latest morning run (runtime
  ≤ 14Z) for each day's daily-max ftime (00Z next day). One
  forecast per (station, day) after deduplication.
- Market favorite: highest-YES-priced bucket at ≤16 EDT from the
  hourly prices_history endpoint.
- Ground truth: IEM METAR daily max = `MAX(tmpf, max_temp_6hr_c ×
  9/5 + 32)` per local calendar day.
- Fee: 0.6% per entry (based on the `C × 0.05 × p × (1-p)` formula
  at typical entry prices).
- Bug fix: initial query had a 1-day offset in the NBS ftime→local_day
  mapping. Fixed by removing an erroneous `-1 day` offset. The
  corrected results are dramatically different from the buggy ones.

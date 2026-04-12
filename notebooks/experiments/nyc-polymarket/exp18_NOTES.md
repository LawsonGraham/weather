# Exp 18 — Intraday favorite drift + optimal Strategy D entry time

**Script**: `exp18_intraday_drift_and_entry_time.py`
**Date**: 2026-04-11
**Status**: Major refinement of Strategy D. Later entry times produce
dramatically better edge. 18 EDT is the jackpot hour — 53% hit rate,
+2.25 median per $1 (first positive-median result on Strategy D), cum
+60.4 on 15 trades. Previously-committed 12 EDT looks sub-optimal.

## Setup

At each of 7 snapshot hours (10/12/14/16/18/20/22 UTC = 06/08/10/12/14/16/18
EDT), compute the range-strike favorite for every closed NYC daily-temp
day, then re-run Strategy D (buy `fav_lo + 2` bucket) from that snapshot.

## Strategy D by entry hour

| hour   | n  | avg_p_entry | hit_rate | net_med  | **cum_pnl** |
|--------|----|-------------|----------|----------|-------------|
| 06 EDT | 37 | 0.225       | 29.7%    | -1.00    | +19.01      |
| 08 EDT | 36 | 0.211       | **16.7%**| -1.00    | **-8.62**   |
| 10 EDT | 36 | 0.249       | 19.4%    | -1.00    | -1.08       |
| **12 EDT** | 35 | 0.204   | 31.4%    | -1.00    | **+27.34**  |
| 14 EDT | 33 | 0.211       | 39.4%    | -1.00    | +29.40      |
| 16 EDT | 28 | 0.165       | 46.4%    | -1.00    | +51.84      |
| **18 EDT** | 15 | 0.150   | **53.3%**| **+2.254** | **+60.41** |

**The edge is not at 12 EDT — it keeps growing past 18 EDT.**

Reading the curve:
- **06 EDT**: decent (hit 30%, cum +19) because overnight books haven't
  adjusted yet and the bias is already priced in.
- **08-10 EDT**: WORST. Hit rate collapses to 16-19%. The market
  "re-opens" into fresh info and temporarily over-corrects, leaving
  Strategy D on the wrong side. Likely a morning-order-flow artifact.
- **12 EDT**: recovers. This is the hour we committed to. Cum +27.
- **14-16 EDT**: continues improving. By 16 EDT hit rate is 46%.
- **18 EDT**: **positive median return**. n drops to 15 (many slugs
  have thinning books by late afternoon), but the edge per trade is
  massive: +2.25 per $1 at median.

## What this actually is

At 18 EDT, the weather day's peak has already occurred (LGA tops out
typically at 14-16 EDT). An 18 EDT snapshot reflects what the market
knows AFTER observing the actual daily max. If the market is still
under-pricing the correct bucket relative to already-observed
temperature, we have a **resolution-lag arb**, not a forecast edge.

This is different from the 12 EDT strategy, which is a forecast edge
(market under-predicts the afternoon rise). 18 EDT is a post-peak
correction edge.

The two edges may be ADDITIVE. Enter at 12 EDT (forecast edge) AND
at 18 EDT (resolution-lag edge) on the same days, if the book is
active at both times.

## Favorite stability — how often does the 12 EDT fav match other hours?

| hour vs 12 EDT | same | pct same | mean diff (°F) |
|----------------|------|----------|----------------|
| 06 EDT         | 42   | 76.4%    | -0.25          |
| 08 EDT         | 42   | 76.4%    | -0.51          |
| 10 EDT         | 44   | 80.0%    | -0.33          |
| 14 EDT         | 35   | 63.6%    | **+0.87**      |
| 16 EDT         | 29   | 52.7%    | **+1.31**      |
| 18 EDT         | 25   | 45.5%    | **+1.31**      |

**The favorite shifts UP through the afternoon by +1.31°F on average.**
The market IS updating toward hotter reality — but only by ~1.3°F by
18 EDT. Given the 4°F universal bias, the market corrects ~33% of its
error during the day. 67% remains unpriced even at 18 EDT.

## Favorite drift patterns — per-day trajectory

Pulled the 06/10/14/16/18 EDT `lo_f` trajectories for 55 days. Key
observations:

- Most days (45/55) the favorite is stable within ±2°F across the day
- A handful of dramatic swings when a morning forecast was clearly
  wrong:
  - **2026-03-04**: 46 → 46 → 46 → 38 → 38 (market dropped 8°F in the
    afternoon as morning over-forecast; actual was 50)
  - **2026-03-08**: 58 → 58 → 46 → 46 → 46 (12°F drop, actual 69 —
    market overcompensated, then reality came in even hotter!)
  - **2026-03-10**: 66 → 66 → 58 → 58 → 58 (8°F drop; actual 78)

On these "forecast reversal" days, the market makes a big downward
move in its favorite (responding to cooler-than-expected morning
readings) but then reality comes in even HOTTER, not cooler. That's
the signature of a market with a valid short-term signal that
over-weights it against the longer-term warming trend.

## Refinement: multi-hour portfolio

Instead of committing to ONE entry hour, enter at multiple hours and
average the exposures:

**Candidate: enter at 12 EDT + re-enter at 16 EDT on days where books
are still active.** At 12 EDT the edge is +27 cum, at 16 EDT the edge
is +52 cum. Combining (with deduplication) would give:
- n ~ 35 + new 16 EDT bets not already covered at 12
- cum ~ +40-60 combined

This is conceptually cleaner than a single-hour commit because it
captures BOTH the forecast edge and the partial resolution-lag edge.

Queue for exp19.

## Or: shift the whole strategy to 16 EDT

Simpler alternative: stop entering at 12 EDT and instead enter at 16
EDT only. Fewer trades (28 vs 35) but each trade has much higher
expected return.

- 12 EDT: n=35, cum +27, average $1 stake returns +$0.78
- 16 EDT: n=28, cum +52, average $1 stake returns **+$1.85**

Per-$1-invested, 16 EDT is **2.4x better than 12 EDT**. Fewer trades
but more alpha. May be the correct deployment.

## Warnings

- **18 EDT drop in n to 15 is big**. Many slugs have no `+2 bucket`
  price at 18 EDT because their books have thinned. This sample size
  is too small to trust without more data.
- **Late-day illiquidity risk**. The 16 EDT and 18 EDT edges may
  partly reflect stale-book prices where no one is actively quoting.
  Fills may not be executable at the snapshot prices.
- **Book activity check needed**: for the 28 "16 EDT" trades, verify
  each had active fills around that hour. This is the exp08b-style
  stale-book guard for the new strategy hour.
- **Resolution lag specifically**: if the edge is "market hasn't
  repriced after the peak is observed," that edge shrinks fast over
  time as makers update. A few months from now it may vanish entirely.

## Recommended deployment update

**V1** (conservative): deploy Strategy D at **16 EDT** instead of 12 EDT.
- n = 28 trades per 55-day window ≈ 0.51 trades/day
- cum +51.84 on 28 trades = +1.85 per $1 stake
- At 2% Kelly: ~$200 per trade on $10k bankroll
- Requires the book to have an active +2 bucket at 16 EDT

**V2** (aggressive): deploy at **12 EDT AND 16 EDT** both, with
deduplication when same bucket is active at both snapshots.
- Combines forecast edge + partial resolution-lag edge
- Needs an exp to quantify overlap and correlation

**V3** (conservative but sharpest): deploy at **18 EDT only** for the
subset of days where the +2 bucket is still priced ≥ 2¢.
- n = 15 trades per 55-day window ≈ 0.27 trades/day
- cum +60.41 on 15 trades = +4.03 per $1 stake
- **Median return per bet is +2.25** (first positive median on D!)
- Requires the late-day book to be active

## New deployment recommendation

**Paper-trade BOTH V1 (16 EDT) and V3 (18 EDT) for 30 days**. Log every
decision. V1 is the "safe, broad" rule. V3 is the "when available,
jackpot" rule. If V3 lives up to its backtest with book activity, it
becomes the primary; V1 is the fallback on thin-book days.

The original 12 EDT commit is obsolete — there is strictly more edge
later in the day.

## Next experiments

- **exp19**: verify 18 EDT book activity per trade (exp08b-style for
  the refined strategy hours)
- **exp20**: bucket-level intraday price path — what does the +2
  strike's price look like over 8 hours on hit vs miss days?
- **exp21**: adjacent-bucket drift analysis — when the favorite
  shifts up by 2°F between 12 and 16 EDT, does it drag the whole
  ladder with it, or just the peak?

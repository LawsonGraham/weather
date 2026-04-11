# Exp A — Ladder sum != 1.0 (overround structure of NYC daily-temp markets)

**Script**: `expA_ladder_sum_arb.py`
**Date**: 2026-04-11
**Status**: Structural finding. The midpoint ladder sum is NOT 1.0 — there's
~2c average overround, but with huge day-to-day variance (april-12 avg 0.983,
april-13 avg 1.052). This changes how to interpret midpoint in backtests.

## Method

For every 1-min snapshot in `data/processed/polymarket_prices_history/min1/`,
dedup to one row per (slug, minute), group by market-date, sum `p_yes` across
all ~11 buckets of that day. A no-arbitrage ladder should sum to exactly 1.0.

## First-pass bug discovered (data hygiene lesson)

The raw `/prices-history` endpoint occasionally emits duplicate points at
the same second (observed ~1 dup per 1439-pt series). Without dedup, the
minute-bucket aggregation double-counts them and I was seeing ladder sums
of 2.1. Fix: `DISTINCT ON (slug, date_trunc('minute', timestamp))`.
**Document this for future 1-min work** — always dedup by (slug, minute)
before aggregating.

## Findings (complete 11-bucket snapshots only)

**Per-day mean ladder sum:**

| day          | n     | mean   | p50    | p95    |
|--------------|-------|--------|--------|--------|
| april-12     |  345  | 0.983  | 0.995  | 1.035  |
| april-13     |  340  | 1.052  | 1.051  | 1.124  |

**Distribution across all days:**

- 2482 snapshots (64%) with sum > 1.01 (OVER-priced) — avg 1.132
- 730 (19%) UNDER-priced — avg 0.939
- 446 (12%) flat in [0.99, 1.01]

**Distribution by deviation magnitude:**

| |dev|     | n   | avg_sum |
|-----------|-----|---------|
| < 0.5c    | 199 | 1.0006  |
| 0.5–1c    | 238 | 1.0007  |
| 1–2c      | 430 | 1.0054  |
| 2–5c      | 1159| 1.018   |
| 5–10c     | 985 | 1.044   |
| > 10c     | 866 | 1.281 (thin ladders, not real) |

## Interpretation

- **Midpoint ≠ fair value.** The midpoint ladder systematically over-sums
  because mid-of-spread aggregates toward the side with most resting liquidity,
  which is the YES side on hot buckets. The spread bias matters.
- **Day-to-day variation swamps day-of-day variation.** april-12 is under, april-13
  is over, and both are "same market". High-volatility-regime days (april-13
  forecast hot/wide uncertainty) have much higher overround than stable days
  (april-12). This is consistent with spread widening during uncertainty.
- **2–5c overround band is 30% of snapshots** — those are candidate "short
  the ladder" opportunities if you can get any buckets off at midpoint or
  better. But the 2% fee + slippage probably kills it on the spread.
- **Biggest under-sums (<0.85)** are almost all april-11 early-morning / evening
  snapshots where one bucket had a zero or stale price — **these are NOT arb**,
  they're incomplete-market artifacts.

## Consequences for Strategy D V1

The exp14 / Strategy D backtests use `p_at_16 * (1 + 0.02)` as entry cost (2%
fee). The ladder-overround distribution shows that:

- On days like april-13 (overround = 5c), the "true ask" is materially higher
  than midpoint — the 2% fee is probably an underestimate.
- On days like april-12 (underround = 2c), the 2% fee is an overestimate — if
  any real buys happened at a discount-to-midpoint, real PnL would beat the
  backtest.

**Action item**: when we have book data from the WS recorder, replace the flat
2% fee with a per-snapshot "real_ask_from_book" cost in the Strategy D backtest.

## Followups

- Is the overround correlated with the current favorite's probability? (low
  favorite prob → wider ladder → more overround?) → run when book data has
  a day or two of coverage.
- How much of the day-13 overround goes away when we use the top-of-book ask
  instead of midpoint? That's the real-execution question.

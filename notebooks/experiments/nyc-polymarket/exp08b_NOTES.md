# Exp 08b — Verify the 5 high-conviction peaked-ladder trades

**Script**: `exp08b_verify_peaked_trades.py`
**Date**: 2026-04-11
**Status**: **PASS — all 5 trades had active order books.** The peaked-ladder
finding from exp07 is not a stale-book data artifact.

## Test

For each of the 5 historical trades where the 12 EDT favorite was priced
≥ 85¢ and resolved NO, check the fill activity in the ±30-min window around
12 EDT:

- Fill count in [11:30, 12:30 EDT]
- Price range of fills in that window
- Total USD volume
- Distinct takers

If a trade was only a "99¢ frozen book with zero flow," I could not have
actually filled a fade at those prices and the backtest is an artifact.

## Results

| local_day  | strike    | p_fav_noted | window n_fills | price range  | window $    | takers |
|------------|-----------|-------------|----------------|--------------|-------------|--------|
| 2026-03-27 | 66-67°F   | 0.999       | 13             | 0.001–0.999  | $2,245      | 7      |
| **2026-03-12** | 56-57°F | 0.962     | **944**        | 0.003–0.998  | **$11,190** | **45** |
| 2026-03-05 | 44-45°F   | 0.900       | 92             | 0.02–0.979   | $1,717      | 28     |
| 2026-02-22 | 34-35°F   | 0.871       | 208            | 0.04–0.95    | $928        | 72     |
| 2025-12-30 | 32-33°F   | 0.850       | 12             | 0.15–0.85    | $112        | 4      |

**VERDICT: 5/5 active books.** Not a single stale-book artifact. The
smallest-volume case (2025-12-30, $112) still had 12 fills and 4 distinct
takers in a 60-minute window.

The 2026-03-12 case is especially striking: **944 fills in the window,
$11k of USD flow, 45 distinct takers**. Taking the YES side at 0.962
with real size was being done by real traders, hitting real asks. Someone
was paying 96¢ for a bucket that eventually got 0.

## What the fill stream looks like

2026-03-27 sample (one of many lines):

```
YES buy  0.9990   $1.00
NO  buy  0.0010   $0.00    (same trade, complementary token)
YES sell 0.9980   $15.03
YES buy  0.9990   $5.70
NO  buy  0.0010   $0.01
YES sell 0.9961   $81.68    ← larger YES sell
YES buy  0.9970   $4.99
YES buy  0.9960   $76.69    ← larger YES buy
```

Takers are actively crossing the book at 99¢+/-1c — this is not a frozen
order rusting at 0.99. Someone on the YES buy side is confident, someone on
the YES sell side is less confident. The spread is 1-2¢. Real liquidity.

## Implication

The exp07 finding — "fade peaked-ladder favorites" — is **not a data
artifact**. Real takers were pushing YES to 85-99¢ on these days, and the
day resolved NO on 5 of 5. The mispricing is real and the fills were real.

Still 5 trades is tiny, but at least it's 5 REAL trades. More data will
clarify whether this pattern persists or is a 2025-Q4 / 2026-Q1 regime
artifact.

## Decision

Keep the peaked-ladder fade thesis in active development. Size caution
applies (small N). But the "is this real at all?" gate is cleared.

## Follow-ups

- **Exp 10** (blocked on time): re-run the same peaked-filter on a longer
  history (if we can reingest the pre-2025-12-31 `Daily Temperature` tag-era
  markets from prediction-market-analysis Parquet). This would 2-3x the
  sample and probably tell us if the pattern is season-specific.
- **Exp 11**: simulate sizing the strategy with risk limits — e.g., Kelly
  sizing at 1/4 Kelly on each trade, with a max per-day exposure. Project
  PnL on the 5 historical trades to see what realistic capital deployment
  looks like.

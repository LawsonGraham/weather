# Exp 06 — Reconstruct actual bid/ask from fills

**Script**: `exp06_real_bid_ask.py`
**Date**: 2026-04-11
**Status**: **Flawed methodology, partial result.** Use aggregate spread number
with caution; per-day reconstruction broken. Needs exp06b to fix.

## Method

For the 12 EDT favorite on each day, pull YES-token fills in the ±5 min window
and try to back out a NO-side ask. On paper:

    NO_ask   = 1 - max(YES SELL price in window)   ← "smallest NO cost to buy"
    NO_bid   = 1 - min(YES BUY price in window)    ← "largest NO offer to sell"

## Bug in per-day reconstruction

Over a 10-min window, the price drifts. MAX of SELL fills is not "the bid at
12 EDT" — it's "the highest bid that was hit at any point in the window, even
after the 12 EDT mark when the price had moved up." Likewise for MIN of BUY
fills. The result is a systematically-NEGATIVE reconstructed spread
(`avg_full_spread = -0.068`) which is non-physical.

**Fix for exp06b**: for each side, take the *single last fill strictly before
12 EDT*. That gives a point estimate of bid/ask right at 12 EDT without
including future drift. Or use the `prices/**` parquet directly with a
snapshot at 12:00 for yes_price and derive bid ≈ yes_price, ask ≈ yes_price
+ measured_half_spread. Not done yet.

## Aggregate take-away that IS trustworthy

Across all 48 days × 55 favorites, total 2,157 fills in the windows:

| outcome | side | n   | avg_price |
|---------|------|-----|-----------|
| YES     | buy  | 906 | **0.670** |
| YES     | sell | 287 | **0.524** |
| NO      | buy  | 816 | 0.308     |
| NO      | sell | 148 | 0.520     |

**Implied aggregate spread**: YES ask ≈ 0.67, YES bid ≈ 0.52 → ≈ **15¢ full
spread** on average. This is a window average over many trades on different
days, so per-fill spreads at a given instant are tighter, but directionally
the 3¢ placeholder used in exp05 is **too optimistic**.

## Implication for exp05 numbers

If realistic half-spread is 5-7¢ not 3¢, check the exp05 sensitivity table:

    3¢ + 2%   →  net_med +0.634  (the reported exp05 headline)
    5¢ + 2%   →  net_med +0.581
    7¢ + 3%   →  net_med +0.517

The fade edge **still survives** at the more realistic cost — median stays
above +0.50 even at 7¢ spread + 3% fee. That's partly because fading the
argmax has a naturally cheaper entry cost (`1 - p_fav`) so absolute cost
haircuts map to smaller fractional returns.

## What this tells us about the market

Some observations that are independent of the bid/ask reconstruction:

1. **Volume skews heavily to YES-taker-buying (906 fills) and NO-taker-buying
(816 fills).** Makers are on both sides of every trade, but the taker flow is
balanced — the market is roughly as busy buying "temperature will land in
this bucket" as it is selling the same thesis. Not a one-sided market.

2. **YES BUY fills average higher than YES SELL fills** (0.67 vs 0.52). This
isn't a spread — it's aggregation. But it means across the 48 days, takers
buying YES were hitting higher prices on average than takers selling YES.
Consistent with the argmax fade finding: high-p YES buyers are getting filled
on markets that subsequently resolve NO.

3. **NO BUY fills average 0.31.** That's the "average NO cost" the backtest
should assume for a NO entry. Our 3¢ placeholder implied 1 - p_fav + 0.03 =
avg 0.63. The actual NO purchases are clustering much lower at 0.31 — but
that's NO purchases for *any* strike across the ladder, not the fade target.
Fading the argmax means NO-buying the highest-p range strike, which has a
mid cost of ~0.60. Consistent-ish.

## Decision

Don't trust the per-day spread reconstruction from this script. Believe the
aggregate direction: **actual spreads are wider than the 3¢ placeholder, but
not by so much that the fade edge dies**.

Fade strategy should be re-priced at **5¢ half-spread + 2% fee** as the
realistic cost floor, which gives net_med ≈ +0.58 per $1 (from the exp05
sensitivity table).

## Queued follow-up

- **Exp 06b**: take per-day bid/ask as last-fill-before-12-EDT on each side,
  not max/min over a window. Or use the `prices/**` yes_price as mid and
  apply a median spread from the fills (around the same time but measured
  adjacent-fill-to-fill).
- **Exp 06c**: check whether spread is narrower at peak volume hours (e.g.,
  14-16 EDT vs 09-12 EDT). If so, fade entry at morning may pay a wider
  spread than the backtest assumes.

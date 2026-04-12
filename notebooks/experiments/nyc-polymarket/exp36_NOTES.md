# Exp 36 — Targeted basket FAILS — single bucket wins

**Script**: `exp36_targeted_basket.py`
**Date**: 2026-04-11
**Status**: Clean negative result. Multi-bucket baskets dilute the
per-dollar edge. Strategy D's single-bucket design is structurally
better and should NOT be replaced with a basket.

## Setup

Exp35 found the eventual winning bucket has at most ~38¢ market price
even at 18 EDT after the peak. Hypothesis: a basket of 5 buckets near
the running max would catch the winner more often, with similar EV
but lower variance.

Tested 5 basket variants with anchor on tmpf at 14 EDT:
- A: lo in [tmpf+0, tmpf+5]
- B: lo in [tmpf+1, tmpf+5]
- C: lo in [tmpf+2, tmpf+6]
- D: lo in [tmpf+0, tmpf+8] (wide)
- E: lo in [tmpf+2, tmpf+4] (narrow)

## Results

| basket  | n  | legs | cost  | hits | cum_pnl |
|---------|----|------|-------|------|---------|
| A: +0,+5 | 25 | 3.0  | $0.87 | 0.84 | -$1.90  |
| B: +1,+5 | 13 | 3.0  | $0.83 | 0.85 | -$0.71  |
| **C: +2,+6** | 13 | 3.0  | $0.78 | 0.77 | **+$2.80**  |
| D: +0,+8 | 29 | 4.0  | $0.94 | 0.83 | -$4.26  |
| E: +2,+4 |  0 | —    | —     | —    | —       |

| **single Strategy D V1 @ 14 EDT** | **33** | — | **$0.21** | **0.39** | **+$47.77** |

The best basket variant (C) earns **+$2.80** vs single Strategy D's
**+$47.77**. Single bucket wins by ~17x.

## Why baskets fail — the math

**Single bucket** (Strategy D V1):
- Cost: $0.16 per share
- Hit rate: 31%
- Payoff if hit: $1 → return = 1/0.16 − 1 = **+5.25x**
- Per-bet EV: 0.31 × 5.25 = **+0.94 per $1**

**Basket** (3 strikes summing to $0.83):
- Cost: $0.83 per "stake unit"
- Hit rate: 84% (basket has higher chance of any strike winning)
- Payoff if hit: $1 → return = 1/0.83 − 1 = **+0.20x** (max)
- Per-bet EV: 0.84 × 0.20 = **+0.012 per $1**

**The basket dilutes the per-dollar edge from +94% to +1%.** Each
individual strike has the same under-pricing, but bundling them means
you pay for multiple strikes when only one wins. The aggregate
per-dollar return collapses to roughly random.

## Why exp35 didn't predict this

Exp35 said "the winner is priced at 38¢ at 18 EDT, you could buy at
38¢ and earn 1.6x". That's true IF you can identify the winner. The
basket strategy doesn't identify the winner — it just buys all
candidates. Buying all candidates means paying ~$0.80 for one $1
payoff, which is +25% IF one wins... and the hit rate isn't quite
high enough to make even that work after fees.

Strategy D's approach is different: it picks ONE specific candidate
that's structurally under-weighted (the +2 offset, where the upward
bias mean lives). The single-strike pick is what concentrates the
EV.

## Implication

**Do NOT switch to a basket.** Strategy D V1 at 14-16 EDT remains
the deployable strategy. The exp35 framing (the market spreads
probability across multiple candidates) is a description of the
market, not a recipe for bigger trades.

## What might work for higher expected return

1. **Concentrate on the BEST candidate, not all candidates.** Strategy
   D already does this with the +2 offset. If a higher-EV offset
   exists (e.g., offset = 1 + lambda × rise_needed for some lambda),
   that could improve.
2. **Per-day model** — use HRRR ensemble to price the actual
   probability of each strike, then buy whichever strike has the
   biggest model-price minus market-price gap. (Blocked on HRRR.)

## Decision

Drop the basket variant. Strategy D V1 stays the primary deployable.

## Queued

- Phase 2 (HRRR-based per-strike pricing) once HRRR backfill lands
  (~89% now, ~20 min remaining)

# Exp 23 + 25 — Corrections to earlier findings

**Scripts**: `exp23_fav_below_current.py`, `exp25_v5_at_late_hours.py`
**Date**: 2026-04-11
**Status**: Two corrections. (1) V5 does not stack with late entry hours —
V1 is better at 16/18 EDT. (2) The exp12 "fav below current" cluster was
5 of 6 argmax artifacts, not real mispricings.

## Exp 25 — V5 at 12/16/18 EDT

| entry | rule | n  | hit_rate | net_avg | net_med | cum    |
|-------|------|----|----------|---------|---------|--------|
| 12 EDT | V1 | 35 | 31%      | +1.55   | -1.0    | +54.16 |
| 12 EDT | V5 | 19 | **42%**  | +3.09   | -1.0    | +58.79 |
| 16 EDT | V1 | 28 | **46%**  | +3.36   | -1.0    | **+94.19** |
| 16 EDT | V5 | 15 | 47%      | +4.61   | -1.0    | +69.16 |
| 18 EDT | V1 | 15 | **53%**  | +7.73   | **+2.61** | **+115.9** |
| 18 EDT | V5 | 10 | 40%      | +6.00   | -1.0    | +59.98  |

**V5 hurts at 16/18 EDT.** The skip rules were designed for
morning-uncertainty days, but late-day entries have *different* edge
sources:

- **12 EDT edge**: forecast bias. The market is over-weighting overnight
  anchors and under-weighting afternoon rise. V5 (skip dry, skip
  high-forecast) correctly filters out cases where that bias is weak
  or wrong.

- **16 EDT edge**: mixture of forecast bias + observed-peak lag. By
  16 EDT, the actual 14-16 EDT peak is mostly in, and the market is
  slow to reprice. Skipping dry days THROWS AWAY trades that would
  have won from the observed-peak lag alone.

- **18 EDT edge**: almost entirely observed-peak lag. The peak has
  happened; V5 filters out valid resolution-lag arb cases.

**Rule**: V5 only applies at 12 EDT. V1 is the correct rule at 16/18 EDT.

## Updated deployable set

| version | entry | rule   | n  | hit_rate | cum_real | net_med | notes |
|---------|-------|--------|----|----------|----------|---------|-------|
| V1 @12  | 12 EDT | +2 fixed | 35 | 31%    | +54.16   | -1.0    | original |
| V5 @12  | 12 EDT | +2 skip  | 19 | **42%** | +58.79  | -1.0    | morning refinement |
| V1 @16  | 16 EDT | +2 fixed | 28 | **46%** | **+94.19** | -1.0 | strong |
| **V1 @18** | **18 EDT** | **+2 fixed** | **15** | **53%** | **+115.9** | **+2.61** | **BEST** |

**V1 at 18 EDT is the single best variant**: highest cum (+115.9),
highest hit rate (53%), and the only one with a POSITIVE MEDIAN per
bet (+$2.61). Small sample (n=15) but the cleanest signal.

## Exp 23 — Fav-below-current was mostly artifacts

Exp12 found 6 days where the 12 EDT favorite's lower bound was BELOW
the current tmpf. Mean gap +16°F, 0% hit rate. Suggested a separate
"extreme mispricing" edge. **Result**: 5 of 6 are data artifacts.

| day        | fav       | fav_p | tmpf_12 | actual | rise_needed |
|------------|-----------|-------|---------|--------|-------------|
| 2026-02-28 | 48-49°F   | 0.320 | 50      | 52     | -2          |
| 2026-03-10 | 58-59°F   | **0.002** | 71 | 78     | -13         |
| 2026-03-11 | 44-45°F   | **0.002** | 62 | 70     | -18         |
| 2026-03-17 | 32-33°F   | **0.001** | 38 | 54     | -6          |
| 2026-03-31 | 72-73°F   | **0.002** | 73 | 80     | -1          |
| 2026-04-04 | 50-51°F   | **0.002** | 65 | 68     | -15         |

**5 of 6 have fav_p ≤ 0.002**. These aren't real favorites — they're
the arbitrary winner of an `arg_max(strike, p12)` tiebreak on a ladder
where every bucket is priced near zero. Not a signal, a quirk.

On those days the real price distribution is probably wide (no strong
favorite) — the market is uncertain, and my argmax just picks whichever
strike happens to have `p = 0.002` vs `p = 0.0018`. The "fav_lo below
current" appearance is a coincidence of which near-zero strike got
picked.

**Only 2026-02-28 is a legitimate "fav below current" case** (fav_p
= 0.32, a real favorite). On that day the ladder has two peaks around
the current temp (44-45 at 6.7¢, 46-47 and 48-49 tied at 32¢), and
the actual day ended at 52°F — a real afternoon warm-up the market
missed. But that's ONE day, not 6. Not enough to build a strategy on.

**Verdict**: drop the "fav below current" hypothesis. There is no
separate extreme-mispricing edge. The exp12 mean-gap +16°F was a
5-artifact average.

## Implication — clean up the exp12 writeup

The exp12 NOTES should be updated to note:
- The 6 "fav below current" days had 5 argmax-on-near-zero artifacts
- Only 1 is a genuine case
- Drop the "big-edge play" framing for this segment

Not blocking — the exp12 universal-bias finding still stands. Just
the footnote about the 6 days needs a correction.

## Revised recommended deployable

**Primary: V1 at 18 EDT** (real-ask entry, fixed $100/bet or 2% Kelly)
- 15 trades over 55 days (0.27 trades/day)
- 53% hit rate
- Positive median PnL per bet (+$2.61)
- Cum +$115.9 on $100/bet capital (+773% ROC)
- 5.9% max drawdown at 2% Kelly

**Secondary: V1 at 16 EDT** (backup when 18 EDT book is thin)
- 28 trades, 46% hit rate
- Cum +$94.19 (+333% ROC)

**Tertiary: V5 at 12 EDT** (morning entry for days when you can't wait)
- 19 trades, 42% hit rate
- Cum +$58.79 (+155% ROC)

**Combined "layered" option** (do all three):
- Enter V5 at 12 EDT if skip rules permit
- Enter V1 at 16 EDT regardless (re-enter on the + 2 bucket even if V5 fired)
- Enter V1 at 18 EDT regardless (third entry)
- Caps notional per bet to avoid compounding
- Expected combined PnL: $58.79 + $94.19 + $115.9 = **$269 per $100 of stake**
  per trade-day, though substantial overlap between the hours means real
  PnL is lower.

## Caveats unchanged

- Small n at 18 EDT (15 trades)
- Regime decay risk
- Need real-time METAR for V5's skip rule
- Late-day books are thinner than 12 EDT but still active (exp19)

## Queued follow-ups

- Clean up exp12 NOTES with the artifact correction
- Build live runner for the layered V1@12+16+18 EDT approach
- HRRR comparison (still blocked, ~55% complete)

# Exp 41 — HRRR-driven strategy: NOT 2-3x Strategy D

**Script**: `exp41_hrrr_driven_strategy.py`
**Date**: 2026-04-11
**Status**: Important correction. HRRR is a better forecast than Polymarket
(exp40), but the Polymarket ladder structure prevents you from trading on
the biggest discrepancies. Phase 2 is at most ~+15% better than Strategy
D V1, not 2-3x as exp40 implied.

## Variants tested (16 EDT entry, real-ask cost)

| strategy            | n  | hit_rate | net_avg | cum_pnl |
|---------------------|----|----------|---------|---------|
| Strategy D V1 +2    | 28 | 46%      | +3.36   | **+94.19** |
| P2a HRRR exact      | 12 | 25%      | +1.91   | +22.88  |
| **P2b HRRR +1°F**   | 17 | 29%      | +6.38   | **+108.51** |
| P2c HRRR +2°F       |  8 | 13%      | -0.23   | -1.87   |
| P2d disagreement ≥4 |  3 | 33%      | +9.89   | +29.68  |

**Best HRRR variant (P2b +1°F shift) earns +108.51 vs Strategy D V1's +94.19.**
**A 15% improvement, not the 2-3x I projected.**

## The fundamental problem — ladder structure caps the accessible edge

Looking at the 26 days where HRRR and Polymarket favorite disagree by ≥4°F:

| day        | HRRR pred | Poly fav | actual | tradeable? |
|------------|-----------|----------|--------|-----------|
| 2026-03-08 | 65°F      | 46°F     | 69°F   | NO (no 65°F bucket) |
| 2026-03-10 | 76°F      | 58°F     | 78°F   | NO (no 76°F bucket) |
| 2026-03-11 | 72°F      | 44°F     | 70°F   | NO (no 72°F bucket) |
| 2026-03-26 | 75°F      | 54°F     | 74°F   | NO (no 75°F bucket) |
| 2026-03-31 | 82°F      | 56°F     | 80°F   | NO (no 82°F bucket) |
| 2026-04-04 | 73°F      | 50°F     | 68°F   | NO (no 73°F bucket) |

**Polymarket's ladder only spans ~10 strikes (~±5°F) around the current
favorite.** When HRRR disagrees by 8-26°F, HRRR's predicted bucket is
OUTSIDE the active ladder — you literally cannot buy a strike at that
temperature because no one has listed one.

Of the 26 disagreement days, **only 4 have an actual tradeable bucket**.
The other 22 are theoretical wins you cannot execute.

## What HRRR IS good for

The 4 tradeable disagreement days:

| day        | HRRR | fav | actual | result |
|------------|------|-----|--------|--------|
| 2026-02-28 | 51   | 42  | 52     | WIN (51-bucket hit) |
| 2026-03-09 | 71   | 60  | 71     | WIN (71-bucket hit) |
| 2026-03-19 | 44   | 40  | 44     | WIN (44-bucket hit) |
| 2026-04-08 | 50   | 46  | 48     | loss |

**3 of 4 wins = 75% hit rate** on a small disagreement filter where the
HRRR bucket IS in the ladder. The signal is real; the constraint is
which days have a tradeable HRRR bucket.

## Why P2c (+2°F shift) loses

When you push the offset further than +1°F above HRRR's prediction,
you're betting on temperatures HOTTER than HRRR's already-slightly-low
forecast. That's overreaching. P2c hit rate of 13% confirms — going
+2°F past HRRR is too far.

## Why P2b (+1°F shift) is the winner

HRRR has +1.27°F bias on average (exp40). Adding +1°F to HRRR's
prediction approximately corrects for that bias. The +1°F offset puts
your bucket at the unbiased forecast for the day's max.

## Corrected capacity estimate

I told the user yesterday that "Phase 2 is 2-3x Strategy D" and gave
capacity numbers based on that. **That was wrong.** Phase 2 is roughly
equal to Strategy D with a marginal +15% improvement from P2b.

Corrected capacity (per the user's question about market-making EV):

| tier             | bankroll | annual EV |
|------------------|----------|-----------|
| Hobby directional| $10k-50k | $5-25k    |
| Pro directional  | $200k-1M | $100k-1M  |
| Solo MM          | $500k-2M | $200k-700k|
| Pro MM           | $5M-20M  | $1.5-7M   |

**Total opportunity across all approaches and 28 cities: ~$5-15M/year**,
not $10-30M as I said yesterday. ~50% downward revision.

## The right way to use HRRR

NOT as a replacement for Strategy D. Use it as:

1. **Confirmation filter**: skip days where HRRR strongly disagrees with
   the Polymarket favorite in the WRONG direction (HRRR predicts much
   cooler than the favorite). On those days, the bias may not exist and
   Strategy D is buying into a real forecast.

2. **Bucket selection**: when the +2 bucket from Strategy D doesn't have
   a real ask price (the strike doesn't exist or has zero quoted size),
   fall back to the bucket containing HRRR's prediction.

3. **Sizing modulator**: on days where HRRR and the +2 offset agree
   (HRRR's prediction is within the +2 bucket), size up; on days where
   they disagree, size down.

This is the realistic Phase 2: a hybrid, not a replacement.

## Honest deployment recommendation

**Deploy Strategy D V1 at 16 EDT as primary.** Use HRRR as a
confirmation filter and bucket-selection backup. Don't expect HRRR to
be a magic 3x edge.

Paper-trade for 14 days. Validate live numbers match the backtest.
Then scale.

## What I got wrong in exp40

The exp40 HRRR-vs-METAR comparison showed HRRR was much more accurate
than Polymarket on AVERAGE and on the OUTLIER days. From that I
projected a 2-3x edge. I forgot that the OUTLIER days are unreachable
because the Polymarket ladder doesn't have strikes 15-25°F away from
the favorite. The signal exists but isn't monetizable.

Lesson: when projecting strategy EV, always check if the trade is
actually executable given the market microstructure. A model can be
right and untradeable at the same time.

## Queued

- exp42: integrate HRRR into live_now.py as a confirmation filter
  (not as a replacement strategy)
- exp43: investigate WHEN Polymarket adds buckets — is there a way
  to request a 76°F bucket be added on a hot day?

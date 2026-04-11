# Exp O — Someone IS running the ladder arb (evidence from trade flow)

**Date**: 2026-04-11
**Status**: **CORRECTS expN.** The 23:04 1-second blip was an outlier.
The 23:15:18-23:16:07 alert cluster shows sum_bid **persisted at 1.011
for 50+ consecutive seconds** AND we have direct evidence of another
trader executing multi-leg ladder sells during that window. The arb is
REAL, PERSISTENT, and ACTIVELY CAPTURED by at least one other party.

## 65-alert cascade on april-12 between 23:04 and 23:28 UTC

Alerts per minute:

| UTC minute | alerts |
|------------|--------|
| 23:04      | 1 (the isolated first blip) |
| 23:15      | **19** |
| 23:16      | 3  |
| 23:18      | 1  |
| 23:19      | 2  |
| 23:20      | 7  |
| 23:21      | **16** |
| 23:22      | **11** |
| 23:28      | 5  |

Median sum_bid across alerts = 1.011. Max = 1.012. Every single alert is
on april-12 (not april-11 which has consolidated, not april-13 which is
too far out).

## Second-by-second trace of the 23:15:18 window

```
23:15:14  sum_bid=1.001
23:15:15-17  0.991     ← brief dip below 1.0
23:15:18  sum_bid=1.011 ← ARB OPEN
23:15:19  1.011
23:15:20  1.011
...
23:16:07  1.011       ← 50 SECONDS CONTINUOUSLY at 1.011
23:16:09  1.001       ← drops to baseline
```

**50 consecutive seconds above 1.005.** That is an ETERNITY compared to
expN's "1-second blip" narrative. A taker with 1s latency has 50 tries to
execute the arb. This is catchable by any reasonable bot.

## Direct evidence of a taker running the ladder arb

During the 50-second window, the `last_trade_price` stream shows:

```
23:15:20.421  55f BUY  7.692306 @ 0.39     ← buying favorite
23:15:21.069  57f BUY  7.484847 @ 0.33     ← buying +1

23:15:21.089  59f SELL 7 @ 0.08    ← 10 simultaneous sells
23:15:21.089  65f SELL 7 @ 0.003
23:15:21.089  47-  SELL 7 @ 0.002
23:15:21.089  61f SELL 7 @ 0.015
23:15:21.089  66+  SELL 7 @ 0.005
23:15:21.089  51f SELL 7 @ 0.032
23:15:21.089  49f SELL 7 @ 0.006
23:15:21.089  63f SELL 7 @ 0.007
23:15:21.089  53f SELL 7 @ 0.171
23:15:21.089  55f SELL 7 @ 0.38
```

**10 SELL orders on 10 different buckets with identical size (7) all at
23:15:21.089 UTC within a 50-millisecond window.** This is a single
multi-leg order, submitted via an async batched API call, running the
ladder-bid arb exactly as we theorized.

Total receipts: 7 × (0.38 + 0.171 + 0.08 + 0.032 + 0.015 + 0.007 + 0.006
+ 0.005 + 0.003 + 0.002) = 7 × 0.701 = **$4.907**.

The missing bucket (57f @ 0.33) was NOT sold — possibly because it was
being bought (23:15:21.069) at the same moment, or the arb bot had
specific exclusion logic.

After the initial lift, 11 MORE buy orders hit every bucket at NO-side
prices (0.62-0.998 range, each 30 shares), at 23:15:28.076 - 23:15:29.907.
These look like **the arb bot COVERING their short positions with NO
tokens** — the inverse ladder construction. 11 NO-token buys at the NO
bids gives a hedged portfolio.

## The MM hypothesis is WRONG — or at least incomplete

In expN I claimed an active MM holds sum_bid at exactly 1.000 and corrects
deviations in <1s. That's not what's happening:

- **sum_bid baseline is actually ~1.001-1.011**, not 1.000
- **Arbs persist for tens of seconds**, not 1 second
- **The MM is not correcting aggressively** — in some windows it's
  absent altogether

What expN observed (the 23:04 1-second blip) was either a rare fast
correction or a coincidence of order timing. The 23:15 window shows the
real pattern.

## Revised capacity estimate

Between expJ's $75-150/day and expN's $5-15/day, the truth looks like:

- **Active-flow windows** (like the 23:15-23:28 burst): ~60-100 arbs/hour
  with 30-50s persistence
- **Quiet windows**: few or no arbs
- **Average rate over a full day**: ~20-40 arbs/hour during the 12-hour
  active window

At 7-30 shares per leg × $0.01-0.02 per cycle × 20-40 cycles/hour × 4-6
hours of active flow per day:

- **Per city: $5-30/day**
- **8 cities: $40-240/day**
- **The original expJ estimate was roughly right** for active-flow periods

The gotcha: **someone is already running this arb** (evidence above).
Competition is real. Our rate would be whatever's left after they take
their fill.

## What the "someone else" looks like

- Uses async batched order submission (10 sells in 50ms window)
- Sizes to 7 shares per leg (conservative, leaves depth for smaller
  players)
- Covers the short position with NO-side buys 7 seconds later
- Operates in 1-2 minute bursts, then quiet

Characteristics:
- Not a blisteringly fast HFT shop — 500ms-1s end-to-end latency is fine
- Probably an individual operator or small bot shop running a published
  arb strategy
- Not colocated with the Polymarket matcher (flow would be even faster)

## Implications for our build

1. **Taker model is viable** — 30-50s windows are plenty
2. **Competition exists** — we're not the only taker
3. **Size to whatever the other taker leaves on the table**: 7-share size
   suggests depth is 10-20 shares per top-of-bid typically. We can sit
   behind at 3-5 share size and still get fills
4. **Multi-leg batched API calls are a hard requirement** — can't run
   this with sequential order submission
5. **Passive MM model is still theoretically higher-capacity** but we
   can start with taker

## Open questions

- Is the trader buying 55f and 57f a separate directional trader, or
  part of the arb setup? Why BUY those two specifically before SELLING
  the other 10?
- What's the 11-leg NO-token buy at 23:15:28-29 doing? Is it a hedge,
  or is the arb bot running a paired inverse?
- How often does the arb window reopen after a correction? The 23:15-
  23:22 burst had multiple re-openings — is each re-opening another
  piece of flow arriving?

## Commit the correction

expN's "revised capacity 90% down" narrative should be marked as a
partial-data interpretation. This exp O data SHOWS the real rate
when flow is active. The truth is between expJ's and expN's estimates
but closer to expJ for active-flow periods.

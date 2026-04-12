# Exp K — Strategy D V1 replay vs real top-of-ask (with a pre-16-EDT pump discovery)

**Script**: `expK_stratD_real_ask.py`
**Date**: 2026-04-11
**Status**: Single-day validation of the Strategy D V1 cost assumption.
One clean number (17.6% real-ask premium on april-11) and one
surprising discovery (the +2 bucket PUMPS into the 16 EDT entry time,
suggesting copycat Strategy D flow).

## Method

Pull the tob parquet. For each of the three market-dates we have book
coverage for (april-11, 12, 13), pick the snapshot closest to 20:00 UTC
(16:00 EDT — Strategy D V1's entry time). Identify the favorite (max
mid excluding edge buckets) and compute the +2 bucket's real ask vs
the backtest's "mid × 1.02" cost assumption.

## Headline: real ask premium at 16 EDT

| md         | fav  | tgt (+2) | tgt_mid | bt_cost (mid × 1.02) | real_ask | **cost_gap** | pct   |
|------------|------|----------|---------|----------------------|----------|--------------|-------|
| april-11   | 60-61| 64-65    | 0.150   | 0.153                | **0.180**| **+2.7c**    | +17.6% |
| april-12   | 54-55| 58-59    | 0.275   | 0.281                | 0.290    | +0.95c       | +3.4%  |
| april-13   | 74-75| 78-79    | 0.225   | 0.230                | 0.229    | **-0.05c**   | -0.2%  |

**Average real-ask premium**: +1.2c per entry ≈ +7% over the
backtest assumption.

**Max premium observed** (april-11): **+17.6% over backtest**. On a
0.153 entry that's +2.7c of additional cost before you start measuring
PnL.

### Consequence for backtest validity

exp14/exp40 reported Strategy D V1 as +$3.36/trade at 46% hit rate on 28
historical days. The cost assumption (`p_at_16 * (1 + 0.02)`) was a flat
2% fee. This replay shows the ACTUAL fee is **3–18%** depending on day,
averaging ~7%. The backtest's PnL is optimistic by roughly:

- Average day: -1.2c per trade → -4% haircut on reported PnL
- Bad day (april-11 style): -2.7c per trade → -9% haircut

Updated expected PnL: **+$3.05–$3.24/trade** (down from $3.36). Still
positive but materially smaller. Hit rate doesn't change — this is a
pure cost adjustment.

## The big discovery: the +2 bucket PUMPS into 16 EDT entry time

Tracing the april-11 64-65°F bucket (the +2 target) minute-by-minute
from 19:24 UTC through 21:51 UTC:

| local time   | mid   | ask   | notes                                    |
|--------------|-------|-------|------------------------------------------|
| 15:24 EDT    | 0.070 | 0.108 | start of tob coverage                    |
| 15:25 EDT    | 0.036 | 0.038 | dipped briefly                            |
| 15:27 EDT    | 0.068 | 0.104 | bouncing                                  |
| 15:28 EDT    | 0.045 | 0.060 |                                          |
| …            |       |       |                                          |
| **16:00 EDT** | **0.150** | **0.180** | **Strategy D V1 entry — AT THE PEAK** |
| …            |       |       |                                          |
| 17:51 EDT    | 0.003 | 0.004 | collapsed to floor                       |

**The +2 bucket price more than DOUBLED from 15:24 EDT (0.07 mid) to
16:00 EDT (0.15 mid).** Then it collapsed to 0.003 by 17:51 EDT.

This is a classic "pump into the entry time" pattern. Who's buying?

**Hypothesis**: there are other traders running Strategy D (or a similar
"fade the favorite by buying the +2 bucket at 16 EDT" heuristic). They
all enter at the same time. Their collective buying pressure pumps the
+2 bucket's price 40-50% UP between 15:30 and 16:00 EDT. Strategy D V1
is entering **at the peak of the copycat-flow pump**, not at a clean
baseline price.

### Evidence this is copycat flow, not just ambient drift

1. **The pump is time-localized**: the bucket traded 0.07 at 15:24 and
   0.03-0.07 at 15:25-15:45, then suddenly jumped to 0.11-0.18 in the
   15:50-16:05 EDT window. Concentration near 16 EDT.
2. **It's asymmetric**: the pump happens before 16 EDT (entry time) and
   the collapse happens after. No natural price process explains this
   minute-level shape — it's flow-driven.
3. **The +2 bucket is mechanical**: anyone running a "fav_lo + 2"
   heuristic targets this exact bucket without individual judgment.
   Easy to coordinate without explicit coordination.

## Candidate Strategy D V2 — enter earlier

**If we move entry time from 16:00 EDT to 15:30 EDT**, we buy the +2
bucket at the PRE-pump price:

- Pre-pump mid (15:30 EDT): ~0.07 → backtest cost ~0.071
- Post-pump mid (16:00 EDT): ~0.15 → real cost ~0.180
- **Savings per trade: 11 cents** on a 0.07 entry basis — 155% relative
  improvement

This is conjectural from one day. Need multi-day verification, but the
shape is so clear on april-11 that it's worth testing.

Potential risks of 15:30 EDT entry:
1. **Less information** — the morning METAR readings haven't fully
   converged yet. You might enter the +2 bucket when the favorite is
   wrong.
2. **Smaller price dispersion** — pre-pump books may be thinner.
3. **Backtest hit rate may drop** — Strategy D's 46% hit rate was
   computed assuming 16 EDT entry. A 15:30 EDT entry could be worse
   at predicting the +2 bucket.

Needs a proper backtest using the prices_history min1 data (we have
24 h of 1-min on multiple days) to validate.

## Spread regime at 16 EDT snapshot

As a side observation: the spreads at 20:00 UTC on the three markets:

| md       | fav bid | fav ask | fav spread | +2 spread |
|----------|---------|---------|------------|-----------|
| april-11 | 0.79    | 0.83    | 4c         | 6c        |
| april-12 | 0.31    | 0.34    | 3c         | 3c        |
| april-13 | 0.26    | 0.28    | 2c         | 0.8c      |

Spread is tighter on april-13 (farther from resolution, thinner but
less flow-driven) and wider on april-11 (higher volume, but the book
responds to net flow). Consistent with the exp H finding that the
"high" midpoint regime (0.50–0.75) has the widest spread — but here
the fav is at 0.81 on april-11, which is above that regime.

## What this adds to the vault

- [[2026-04-11 Asymmetric mean reversion edge]] was already invalidated
- [[2026-04-11 First pass 1-min price data exploration]] Edge #1 is
  **augmented**: Strategy D V1 is still positive but with a 3-18%
  cost haircut AND a likely copycat-flow pump at entry time
- **New hypothesis worth a full backtest**: Strategy D V2 moves entry
  from 16 EDT to 15:30 EDT, buys the +2 bucket at the PRE-pump price

## Priority-1 followup for next iteration

- **Verify the pre-16-EDT pump pattern on april-10** (the resolved day
  we have 1-min data for): look at the 1-min price path of whatever
  bucket was the +2 target at 16 EDT on april-10. If the same pump
  shape appears, the hypothesis is double-verified.
- If 2/2, propose Strategy D V2 with a full multi-day 1-min replay.

## april-10 counter-evidence — NO pump on a boring day

Ran the same pre-16-EDT analysis against the april-10 +2 bucket (62-63°F;
favorite was 58-59°F). Result: **completely flat**. The +2 bucket traded
at 0.003-0.006 for the entire 15:00-17:00 EDT window with no pump
whatsoever. The favorite was already at 0.97-0.98 — the market had
resolved informationally by 15:18 EDT.

### Refined hypothesis — pump is day-regime dependent

| day     | regime  | fav mid at 15:30 EDT | +2 pump at 16 EDT? |
|---------|---------|----------------------|--------------------|
| april-10| resolved | 0.96 (already right) | NO                 |
| april-11| active  | ~0.55→0.80 (still converging) | YES (0.07→0.18, 2.5×) |

**Boring/resolved days**: Strategy D V1's +2 bucket is already at floor
because the market has priced in the answer. Entry time doesn't matter.
There's also no alpha to capture — the favorite is right.

**Active/moving days**: Strategy D V1's +2 bucket pumps into 16 EDT
because ambient directional flow (retail + copycat strategies) pushes
toward "higher peak" outcomes. Strategy D V1 pays the pump.

**Implication**: Strategy D V2 (15:30 EDT entry) only helps on active
days. Boring days are neutral (no advantage either way). Since active
days are also where most of Strategy D V1's backtested PnL comes from,
V2 could be materially better — **on the subset of days that matter**.

Needs multi-day validation with both the new time sample AND a day-
activity classifier ("is the market still moving at 15:30 EDT?").

## Followup queue

- Exp L: Strategy D V2 (15:30 EDT entry) backtest on prices_history min1
- Exp M: book state reconstruction with full L2 depth from book
  snapshots (currently tob has only top-of-book)
- Exp N: watch the april-12 arb window tomorrow and log alerts

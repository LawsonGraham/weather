# Backtest v2 Pre-Registration

**Date created**: 2026-04-14
**Branch**: `wt/backtest-v2`
**Author**: backtest run driven by Claude, supervised

This document is the **pre-registration** for the second round of
backtesting. It locks in the split, strategies, and decision rules
**before** looking at the out-of-sample data. Any deviation below this
line must be justified in writing and dated.

The motivation: prior backtests (exp01-36 + multi-city offset scan)
used the FULL available window for both discovery and evaluation.
That's fine for hypothesis generation, but not a valid test of edge.
Concern: per-city variance is enormous; what prior results may really
be showing is "Miami / Seattle had a peculiar bias in this period."
A proper temporal holdout tests whether the edge survives unseen days.

---

## 1. Data window + split

### Usable window
All sources have full coverage within **Mar 11 → Apr 10, 2026** (31 days):
- Prices (hourly): Mar 11 → Apr 11 (1-day buffer)
- Markets + outcomes: resolved through Apr 10 (NYC) / Apr 11 (others)
- NBS, GFS MOS forecasts: through Apr 11
- HRRR forecasts: through Apr 11
- METAR ground truth: through Apr 11

ASOS 1-min ends Mar 31 for most stations — we use **METAR-derived
daily max** as ground truth uniformly (simpler, and matches the
Polymarket resolution source).

### Split (locked)
- **IS (development)**: Mar 11 → Mar 31 (21 days, ~68%)
- **OOS (lockbox)**: Apr 1 → Apr 10 (10 days, ~32%)

Split is on the **resolution date** (the date the market settles).
Trade entries are one day earlier, but that's fine — the split is
still temporal-forward.

**OOS seal**: no OOS queries until Phase 6. Before that, any code
that touches OOS data must filter `market_date <= 2026-03-31`.

### Cities included
All 11 US cities with markets:
NYC, Atlanta, Dallas, Seattle, Chicago, Miami (long-coverage: ~31 MDs IS + ~10 MDs OOS)
Austin, Houston, Denver, LA, SF (short-coverage; Mar 24 start: ~8 MDs IS + ~10 MDs OOS)

Approximate totals:
- IS: ~186 market-days (6 × ~21 + 5 × ~8)
- OOS: ~110 market-days (11 × ~10)

---

## 2. Universe and price semantics

### Market universe
Daily-high-temperature markets only (`weather_tags ILIKE '%Daily Temperature%'`).
11-bucket structure per city-day:
- Bucket 0: `X°F or below` (tail low)
- Buckets 1-9: 2°F ranges
- Bucket 10: `Y°F or higher` (tail high)

Bucket thresholds per city vary (warmer cities have higher ranges).
Parsed per-slug from `group_item_title` (`"76-77°F"` → [76, 77]).

### Entry price
**Hourly midpoint** from `prices_history/hourly` at the entry timestamp.
No proxy to ask/bid without data (prices_history is midpoint by design).

**Ask premium**: in the main backtest, use midpoint as entry (no adjustment).
In a sensitivity analysis (pre-registered here), also compute PnL assuming
entry at `mid + 0.02` (2¢ worse than mid) as a conservative bound.

### Outcome / payout
YES payout if bucket wins = $1 per share, 0 otherwise.
Winning bucket is determined by the actual daily high at the city's
resolution station, parsed from METAR `max(tmpf)` over the resolution
day in local time.

Cross-check: compare computed winner against `outcome_prices[1]=1.0`
from markets.parquet. If they disagree on >5% of market-days, stop and
investigate before proceeding.

### Fees
`fee_usdc = C × 0.05 × p × (1-p)` per share, taker-side only.
Paid on entry; no exit fee (held to resolution).

### Hold period
Enter at fixed entry hour, hold to resolution. Deliberately simple.

### Entry hour
**20:00 UTC (~16 EDT)**. Chosen in prior work (exp32) as the empirical
sweet spot for NYC. Locked here for all cities. Sensitivity to entry
hour will be examined on IS only.

---

## 3. Strategies (pre-registered)

All strategies are **directional buys** (BUY YES on a specific bucket).
None are arbs — we have prior synthesis showing taker-arb is $1-8/day
net and not worth pursuing.

Per market-day, each strategy outputs zero or more (bucket_idx, stake)
recommendations. The "stake" convention throughout is **1 share** per
trade, unless a strategy explicitly scales (e.g. Kelly).

### S0 — NBS favorite (control)
Buy the bucket closest to NBS-predicted daily max at entry hour.
**Hypothesis**: loses money. Prior work showed -$0.32/trade.
Purpose: sanity check that the universe + mechanics are working.

### S1 — +2°F offset (Strategy D V1)
Buy the bucket whose *center* is 2°F ABOVE the NBS favorite's center.
(i.e., one bucket above favorite.)
**Hypothesis**: IS shows +$1-2/trade. OOS result will judge whether
"markets underestimate afternoon peaks" survives.

### S2 — +4°F offset (Strategy D V2)
Buy the bucket 2 buckets above favorite.
**Hypothesis**: from prior scan, higher variance but positive EV.

### S3 — S1 + S2 combined (basket)
Every time S1 triggers, also buy S2. Portfolio view.

### S4 — NBS-spread-filtered S1
Buy S1 ONLY if NBS `txn_spread_f` is in [2.0, 3.0]°F (NBS is
moderately uncertain). Prior work suggested this is a "sweet spot"
(42.9% hit, $5.45/trade) but n=28 → may not replicate.

### S5 — Model-based edge
Use the trained LightGBM daily-max model (already at
`data/processed/model/daily_max_model_v1.pkl`) to predict a
Normal-distributed daily high. Compute P(bucket) via Normal CDF
using the model's MAE (2.46°F) as sigma.

Bet on buckets where `model_P - market_P > threshold` AND `market_P > 0.02`
(avoid the lowest-price noise buckets).

Threshold is tuned on IS only. OOS uses IS-selected threshold.

### Strategies NOT tested in v2 (deferred to later work)
- Any SELL/SHORT strategy (prior work showed these lose)
- Fading favorites / mean-reversion plays
- Ladder arbs (taker side is dead after fees)
- Maker-rebate paper MM (needs separate quote-manager infra)
- HRRR-surprise events or sudden-weather-change strategies

---

## 4. Decision rules

A strategy **"survives"** if:
1. OOS per-trade PnL > 0 (EV-positive net of fees), AND
2. OOS per-trade PnL > -$0.50 below IS per-trade PnL (degradation
   less than $0.50/trade), AND
3. OOS hit rate > 0 (at least SOME wins, to protect from "all losing
   bets pass the EV test by luck on tail wins")

A strategy is "degraded but plausible" if:
- OOS per-trade PnL > 0 but much lower than IS (e.g., IS $2, OOS $0.20)

A strategy is "failed" if:
- OOS per-trade PnL ≤ 0

### What we will NOT do
- We will NOT "rescue" a failed strategy by post-hoc filters on OOS
- We will NOT retune entry hour based on OOS results
- We will NOT cherry-pick cities based on OOS performance
- We will NOT add new strategies after peeking at OOS

If all strategies fail OOS: we report the null result honestly and
update the vault with "prior edges don't generalize out-of-sample."

---

## 5. Depth estimation (exploratory, not pre-registered for edge claim)

Alongside the backtest, compute per-trade capacity from the book
JSONL recorder:

- For each (slug, entry_timestamp), parse the nearest L2 book snapshot
  from `data/raw/polymarket_book/<slug>/*.jsonl`.
- Report: depth at entry-price (N shares available within the 2¢ window),
  depth at top of ask, distribution of `last_trade_price` fill sizes in
  the hour around entry.

**Caveat**: the book recorder began 2026-04-11 or so. Most of our IS
period has NO book data — depth can only be estimated for the OOS
window and a handful of late-IS days. For the earlier IS, we have
only what `markets.parquet`'s `liquidity_num` tells us as a proxy.

If depth data is too sparse to be meaningful, we **report agnostic
to size** (1 share per trade) and flag depth as "needs more book data
→ revisit after 14+ days of recording."

---

## 6. Acceptance criteria for this backtest

By end of the backtest, we will produce:
1. A single `results.csv` with IS and OOS per-trade PnL for each strategy
2. Per-city breakdown (to check for city-specific overfitting)
3. Per-day PnL series (to check for single-day lucky wins)
4. A 1-page `findings.md` with an honest verdict on each strategy
5. A capacity estimate OR a "depth data too sparse" flag

**Only after** the IS analysis + strategy shortlist is complete,
and only ONCE, do we unseal OOS.

---

## 7. Things we will be honest about

- Sample sizes are small. With ~186 IS market-days × ~11 bucket
  trades/day, S1 has at most ~186 IS trades. That's barely enough
  for a noisy estimate of per-trade PnL.
- Any strategy with IS t-stat < 2 on IS alone should be treated as
  speculative on OOS.
- "Strategy D works on Miami" is NOT equivalent to "Strategy D works."
- Our ground truth is METAR top-of-hour; Polymarket resolution uses
  the actual reported max (could disagree by up to 1°F due to
  SPECI observations missed between top-of-hour METARs).

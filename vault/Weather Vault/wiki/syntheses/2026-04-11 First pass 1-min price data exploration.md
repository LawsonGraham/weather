---
tags: [synthesis, polymarket, 1-min-data, exploratory, market-microstructure]
date: 2026-04-11
related: "[[Polymarket prices_history endpoint]], [[Polymarket CLOB WebSocket]], [[Polymarket]]"
---

# First pass — 1-min Polymarket price-history exploration (2026-04-11)

Three exploratory experiments on the brand-new
`data/processed/polymarket_prices_history/min1/` dataset (54k rows over
42 open NYC daily-temp slugs, past 24 h). Purpose: build intuition about
how these markets actually behave at minute resolution, and surface any
naive edges or structural quirks.

## Headline findings

### 1. Midpoint ladder does NOT sum to 1.0 — and the deviation is day-dependent

For "Highest temperature in NYC on DATE" markets, by no-arbitrage the sum
of YES-midpoints across all ~11 mutually-exclusive bucket strikes should
be exactly 1.0 at every instant. **It's not.**

- **Mean ladder sum = 1.02** across all 1-min snapshots with a complete
  11-bucket ladder (n=3877)
- **64% of snapshots** have sum > 1.01 (over-rounded)
- **19% of snapshots** have sum < 0.99 (under-rounded)
- Day-by-day: **april-12 avg = 0.983** vs **april-13 avg = 1.052**. Same
  market type, different day, 7 cents of variation in the midpoint overround.

**Why**: midpoint is pulled toward the side with deeper resting liquidity.
On volatile / uncertain days the spread widens and the midpoint over-states
the fair YES price on top buckets. On quiet days the spread tightens and
the ladder sums near fair.

**Practical consequence**: Strategy D V1 and every backtest that uses
`p_at_16 * (1 + 0.02)` as entry cost is using a flat 2% fee that's right
on average but systematically wrong day-to-day. When we have book data
from the WS recorder, replace the flat fee with per-snapshot real asks.

### 2. Volatility concentrates in the "favorite neighborhood" — top 5 buckets carry 95% of the action

For each day, only the top ~5 buckets surrounding the current favorite see
meaningful 1-min price movement. Tail buckets sit at the tick floor (0.001)
and move <5 bp/min. The favorite itself and the ±2 neighbors carry:

- std 50–170 bp per 1-min step
- max single-step moves up to **17 cents**
- avg |Δp| of 10–20 cents/hour in the 3-bucket active window

The +2 bucket from favorite (Strategy D V1's target) sits right at the
edge of this active region — ~60 bp/min std, which is significant
but less than the favorite itself.

### 3. Day-before-resolution evening is the information-discovery peak

Hour-of-day |Δp| totals (april-11/12/13 combined):

| hr UTC (EDT) | avg bp/step |
|---|---|
| 21 (17 EDT) | **18.66** ← peak |
| 18 (14 EDT) | 15.60  |
| 22 (18 EDT) | 15.70 |
| 14 (10 EDT) | 15.13 ← morning HRRR run |
| 16 (12 EDT) | 14.56 |
| 01 (21 EDT overnight) | 5.66 ← trough |

**Peak trading activity is 17:00 EDT the day BEFORE resolution**, not
day-of. This is when traders digest the T-1 forecast cycle and reprice.
Secondary peaks at 10 EDT (morning HRRR update) and 14 EDT (midday HRRR
update).

This changes our mental model: for "tomorrow's temperature" markets, the
information-discovery phase is the PREVIOUS EVENING, not resolution day.
Strategy D V1 enters at 16 EDT on resolution day — but most of the price
signal has already been baked in the night before.

### 4. 1-min endpoint gives us the POST-information tail of resolved days

On april-10 (only resolved day with 1-min data), the winning bucket was
already at p_yes = 0.977 at our first 1-min datapoint (15:18 EDT on
april-10). The market had essentially figured out the answer before our
coverage window opened.

**Lesson**: Polymarket's `/prices-history?interval=1d&fidelity=1` endpoint
gives us the 24-hour *trailing* window. For a market resolving at 20:00 EDT,
that means we see from 20:00 EDT-1 to 20:00 EDT — which is ALMOST the full
info-discovery day but cuts off before the initial listing. For backtesting
the final 24 h of price action this is fine; for studying how the market
price evolves from listing-time to resolution we need the live WS recorder
to span the full market lifetime.

## Three candidate naive edges

1. **Thin-book mean-reversion scalping** on the active-region buckets.
   Observed 17-cent 1-min moves revert to the pre-move midpoint within
   5-10 minutes. Limit orders placed at post-move ± 1 cent should catch
   the revert. Requires book data to validate and sized positions to
   execute. **Priority 1** for next exploratory pass.

2. **Evening-before-resolution HRRR front-run**. If we can run HRRR on
   the 12 UTC cycle (for april-12's market: 2026-04-11 12 UTC), extract
   next-day forecast max, and place an order before the 17 EDT repricing
   peak, we can front-run the market's information digestion. This is
   essentially exp41 moved from day-of to evening-before. **Priority 2**.

3. **Short the ladder when overround > 5c**. Sell all 11 YES tokens for
   ~$1.05 and take ~$0.05 guaranteed minus fees. Requires real book asks
   ≤ midpoint + 1c. **Priority 3** — needs WS book depth to validate.

## Data-hygiene lesson (worth remembering)

The `/prices-history` endpoint occasionally emits duplicate (t, p) points
at the same second (~1 dup per 1439-point series). Always `DISTINCT ON
(slug, minute)` before aggregating across slugs or you'll double-count
and see ladder sums of 2.1 where the truth is 1.05. **Documented in
[[Polymarket prices_history endpoint]]** — or should be; add the dedup
note if missing.

## What's next

- Exp D: real-cost Strategy D replay using book data (when we have >1h of
  WS capture)
- Exp E: temperature→price lag study (METAR 2026-04-09..11 downloading)
- Exp F: favorite drift trajectory for april-11 AFTER tonight's resolution
  (full info-discovery day at minute resolution)
- Exp G: mean-reversion scalping backtest on the thin-book bucket moves
  from exp C

## Scripts

- `notebooks/experiments/nyc-polymarket/exploratory/expA_ladder_sum_arb.py`
- `notebooks/experiments/nyc-polymarket/exploratory/expB_winner_vs_losers.py`
- `notebooks/experiments/nyc-polymarket/exploratory/expC_volatility_regimes.py`

Each has a matching `*_NOTES.md` next to it with full experiment details.

## Related wiki pages

- [[Polymarket prices_history endpoint]] — source of all the 1-min data
- [[Polymarket CLOB WebSocket]] — live stream; will complement prices_history
  with depth and real asks, starting from 2026-04-11 19:24 UTC
- [[Polymarket weather market catalog]] — slug catalog

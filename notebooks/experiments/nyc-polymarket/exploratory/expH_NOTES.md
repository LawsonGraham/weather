# Exp H — Real-book replay of expG (sell-pop) + expA (ladder arb)

**Script**: `expH_real_book_replay.py`
**Date**: 2026-04-11
**Status**: TWO headline findings — one negative (kills exp G), one
potentially huge (confirms a real arb pattern, needs freshness check).

## Data

First run of the new `scripts/polymarket_book/transform.py` transformer
produced 979k top-of-book rows across ~65 minutes of live WS capture
(2026-04-11 19:24 UTC → 20:28 UTC). The transformer extracts `best_bid`
and `best_ask` from every `price_change` message and max/min from every
`book` snapshot. YES-token-only filter is applied via join against
`markets.parquet::yes_token_id`.

## Spread distribution by regime (YES token, active buckets)

| mid regime       | n      | mean  | p50   | p95   |
|------------------|--------|-------|-------|-------|
| floor (< 0.05)   | 310k   | 0.7c  | 0.6c  | 1.5c  |
| tail (0.05–0.25) | 97k    | 2.6c  | 2.0c  | 5.8c  |
| low  (0.25–0.50) | 58k    | 4.5c  | 3.0c  | 10c   |
| **high (0.50–0.75)** | 15k | **10.7c** | **9.0c** | 25c  |
| fav  (0.75–0.95) | 9k     | 3.0c  | 3.0c  | 5.0c  |

**The "high" regime (0.50–0.75 midpoint) has 9c MEDIAN spread.** This
is the active-favorite regime during the trading day. 9c spread means
if you buy the favorite at the ask and sell at the bid you lose 9c
immediately. Strategy D V1's implicit "2% fee" assumption is way too
optimistic for this regime — real round-trip cost is 10×.

The spread tightens dramatically once the favorite reaches 0.75+ (fav
regime, 3c spread) — that's post-resolution-lock when uncertainty is low.

## Finding 1 — Sell-pop mean-reversion edge is FAKE at the taker level

| metric              | value    |
|---------------------|----------|
| n pops (|Δmid| ≥ 3c in 60s) | 613 |
| avg pop size        | +5.9c    |
| avg mid reversion (t+60s) | +0.75c (weaker than expG's +1.9c at 10min) |
| **avg real taker PnL** | **-7.72c** |
| **taker win rate**  | **4.9%** |
| avg spread at moment-of-pop | 7.15c |

**The real taker PnL (sell at bid_now, buy back at ask_t+60) is -7.7c
per trade on average with a 4.9% win rate.** That's a complete reversal
of exp G's midpoint signal. What happened:

- Exp G measured "after the pop, does midpoint drift back?"
  — Yes, by ~1.9c at t+10 minutes.
- Exp H measures "after the pop, can a taker CAPTURE that reversion?"
  — No, because the spread at the pop moment is 7c; selling at
    `bid_now` gives you bid = mid - 3.5c, buying back at `ask_t+60`
    gives you ask = mid_t+60 + 3.5c. Even with the full 1.9c mid
    reversion, you lose 7c - 1.9c = -5.1c per round trip.

**The midpoint mean-reversion was 100% a spread-width artifact.** The
mid drifts because the book width fluctuates, not because the true
mark moves. Bid and ask move in lockstep when the book fills in.

**Exp G is invalidated for taker execution.** The sell-pop signal
cannot be captured as a taker. Would require market-making (posting
passive limits) to earn the spread back, which is a completely
different execution model and needs its own simulation.

## Finding 2 — Ladder BID sum exceeds 1.0 multiple times (POTENTIAL ARB)

Per-day ladder sums at full 11-bucket snapshots:

| day          | n_snap | avg_ask | min_ask | avg_bid | **max_bid** | avg_mid |
|--------------|--------|---------|---------|---------|-------------|---------|
| april-11     |   24   | 1.063   | 0.952   | 0.971   | **1.042**   | 1.017   |
| april-12     | 1566   | 1.039   | 0.657*  | 0.900   | 0.981       | 0.969   |
| april-13     |  532   | 1.049   | 0.816   | 0.868   | 0.976       | 0.958   |

(*april-12 min_ask = 0.657 happens with n_buckets = 10 not 11, so it's
a stale-quote / incomplete-snapshot artifact and NOT real arb.)

**The april-11 max_bid sum of 1.042 is on an 11-bucket complete ladder.**
If you can sell one YES token of each bucket at the posted bid prices,
your total receipt is $1.042 and exactly one bucket resolves to $1.00,
so your net profit is **+$0.042 risk-free per ladder** (less Polymarket
execution). Capital required: ~$11 (max loss = $1, but fills require
escrow on each of 11 shorts).

Specific timestamps:

| time (UTC)  | sum_bid | n_buckets |
|-------------|---------|-----------|
| 19:54:15    | 1.032   | 11        |
| **19:54:16** | **1.042** | 11      |
| 20:00:18    | 1.005   | 11        |
| 20:07:54    | 1.006   | 11        |
| 20:08:23    | 1.026   | 11        |
| 20:12:11    | 1.033   | 11        |
| 20:13:42    | 1.006   | 11        |
| 20:24:52    | 1.025   | 11        |

**Eight distinct seconds with sum_bid > 1.005 on a complete ladder in a
65-minute window.** If even half of these are real (not stale-quote
artifacts), there's a genuine free lunch here.

### BUT — freshness caveat

The sec_grid in expH_real_book_replay.py takes the LAST-KNOWN quote
per (slug, second). If one bucket's quote has stalled (no price_change
in the past 30s) its "last_known" bid will be an old number. The
cross-sectional sum could be adding a stale bid from 2 minutes ago to
fresh bids from right now, producing an apparent sum > 1.0 that didn't
ACTUALLY exist at any coherent point in time.

**We cannot confirm the arbitrage is real until we verify each flagged
snapshot has quotes across all 11 buckets that are all < T seconds old
at the snapshot time.** Priority 1 for exp I.

Also to verify:
- Is the sum_bid > 1.0 persistent for > 1 second, or is it a flash?
  A flash arb can't be executed in practice.
- Are there really ask-side liquidity on the other side of the bids?
  I.e. can you sell at the posted bid or is the bid a ghost quote
  with no counterparty?

## Finding 3 — Spread regime matters MORE than midpoint

The 10.7c spread on "high" regime (0.50–0.75 mid) is the biggest
execution-cost number in the whole session. Strategy D V1 was recommending
buys in exactly this regime (the +2 bucket of the favorite, which is
often a ~0.20–0.40 midpoint bucket, i.e. "low" regime with 4.5c spread).
That's better than the 10.7c high regime but still much worse than the 2%
flat-fee assumption (which is ~1c at mid=0.40).

**Consequence**: Strategy D V1's backtested +$3.36/trade will likely
degrade by 2-4c per trade when replayed against real spreads. If we
were paying 1c in the backtest and the real cost is 3-5c, our hit rate
has to be higher or our win size bigger to still be profitable. Needs
a full replay against tob to get the corrected number.

## Next iteration priorities

1. **Freshness-checked ladder arb verification** — re-run expH with
   a per-bucket "quote_age < 5s" filter; report how many of the 8 seconds
   remain as genuine > 1.0 sums
2. **Strategy D V1 replay with real book asks** — replace the 2% fee
   with the actual ask price at 16 EDT from tob, recompute exp14
3. **Book-state reconstructor** — full L2 depth evolution per bucket
   instead of just best bid/ask, to answer "how deep is the 0.77 bid
   on the favorite right now?" 
4. **Market-making simulation of the sell-pop** — if we posted passive
   limit orders at the pre-pop price, what fraction would have been
   filled during a pop, and what's the PnL? This is the only way sell-
   pop can become real.

## Summary

- **Exp G sell-pop edge killed** for taker execution. Midpoint mean-
  reversion was a spread-width illusion.
- **Ladder-bid-sum > 1.0 arb flagged** — 8 distinct snapshots, avg
  ~3c profit per ladder, but unverified for quote freshness.
- **Spread in the active-favorite regime is 9c median** — materially
  higher than the 2% fee assumption used in Strategy D backtests.

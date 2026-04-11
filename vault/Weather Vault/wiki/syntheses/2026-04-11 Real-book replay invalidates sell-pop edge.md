---
tags: [synthesis, polymarket, negative-result, 1-min-data, spread, exploratory]
date: 2026-04-11
related: "[[2026-04-11 Asymmetric mean reversion edge]], [[2026-04-11 First pass 1-min price data exploration]], [[Polymarket CLOB WebSocket]]"
---

# Real-book replay invalidates the sell-pop edge; ladder-bid arb candidate surfaces

**Status**: second-order correction on [[2026-04-11 Asymmetric mean reversion edge]] + new lead on [[2026-04-11 First pass 1-min price data exploration]]'s Edge #3.

This page supersedes the sell-pop section of the mean-reversion synthesis.
The midpoint-only edge claimed there does NOT survive real bid-ask
spreads. Experiment H replayed 613 real pops against the live WS book
data and produced a -7.72c average taker PnL with a 4.9% win rate. The
midpoint mean reversion was 100% a spread-width artifact — not a
tradable signal.

## The 4-hour arc of a wrong conclusion

1. **Exp A** (ladder midpoint sum) — noticed overround of 2–5c on midpoint ladder.
2. **Exp C** (per-minute volatility) — saw 17c 1-min moves on thin buckets.
3. **Exp G** (mean reversion at midpoint) — measured +1.9c avg reversion on
   3c+ pops, 65% hit rate. Looked like a clean signal.
4. **Exp H** (real-book replay) — ran the same pops against real bid/ask
   from the WS recorder's 65 minutes of captured data. **Real taker PnL
   is -7.7c per trade with 4.9% win rate.** The "reversion" is the
   mid moving because the spread filled in, not because the fair price
   changed.

**Lesson**: never trust a midpoint-space signal on prediction markets
until you've replayed it against real bid/ask. Midpoint mean-reversion
on thin books is almost always spread fill-in, not price discovery.

## The spread regime table (from exp H)

YES-token top-of-book over 65 min of recorded data, grouped by midpoint
regime:

| regime           | n      | mean spread | p50 spread | p95 spread |
|------------------|--------|-------------|------------|------------|
| floor (<0.05)    | 310k   | 0.7c        | 0.6c       | 1.5c       |
| tail (0.05–0.25) | 97k    | 2.6c        | 2.0c       | 5.8c       |
| low (0.25–0.50)  | 58k    | 4.5c        | 3.0c       | 10c        |
| **high (0.50–0.75)** | 15k | **10.7c**   | **9.0c**   | 25c        |
| fav (0.75–0.95)  | 9k     | 3.0c        | 3.0c       | 5.0c       |

**The active-favorite regime is the WORST for spread** — 9c median is
10× the 2% fee assumption Strategy D V1 has been using.

### Consequence for Strategy D V1

The +2 bucket Strategy D targets is typically in the "low" regime
(0.25–0.50 midpoint) which has a 3c median spread. That's better than
the high regime's 9c but still 2–3× the backtest assumption. Real PnL
on Strategy D is probably +$1.50–$2.00 per trade (down from the
backtested $3.36) once you replace the 2% fee with real asks.

**Queued**: full Strategy D replay against real tob (exp K).

## The potential real-arb: ladder BID sum > 1.0

While running exp H, the ladder-BID sum (sum of `best_bid` across all
11 buckets of a day's ladder) exceeded 1.0 **8 distinct times** during
65 minutes of recorded data:

| time UTC    | sum_bid | n_buckets |
|-------------|---------|-----------|
| 19:54:15    | 1.032   | 11        |
| **19:54:16** | **1.042** | 11      |
| 20:00:18    | 1.005   | 11        |
| 20:07:54    | 1.006   | 11        |
| 20:08:23    | 1.026   | 11        |
| 20:12:11    | 1.033   | 11        |
| 20:13:42    | 1.006   | 11        |
| 20:24:52    | 1.025   | 11        |

If these are real coherent cross-sectional snapshots, selling one YES
of each bucket into the bids yields $1.042 and exactly one bucket
resolves to $1 — net profit +4.2c per full ladder, risk-free (modulo
Polymarket's Polygon finality and off-chain matching).

**But**: exp H built the sec_grid with `DISTINCT ON (slug, second)
ORDER BY received_at DESC` — taking the most recent quote per bucket
within each second. If one bucket hasn't emitted a price_change in the
past 30 seconds, its "last-known" bid is stale, and the cross-sectional
sum could be adding a stale number from a minute ago to fresh numbers
from right now. **The arb candidate is NOT confirmed until we verify
every bucket had a quote update within some freshness window (e.g.
≤5 seconds) at the flagged timestamp.**

Priority 1 for exp I: freshness-filtered rerun.

If the freshness-filter halves the hit count to 4, we still have 4
real-arb opportunities in 65 minutes. Extrapolated to a full active
trading day (~10 h in the 19–05 UTC active window), that's ~40 arbs/day
at +3–4c each = ~$1.40/day per full ladder. Not huge in absolute PnL
but ROI per second of exposure is very high (a few seconds to execute
11 legs vs a few minutes to set up).

Also worth checking: does a persistent "maker" run out of capital on
these arbs? Each requires $1 per short × 11 buckets = $11 escrow.
Scaling to 100× ladders = $1100 capital. Manageable.

## Lessons locked into the vault

1. **Midpoint signals are suspect on prediction markets.** The book is
   thin, the spread is wide and dynamic, and midpoint mean-reversion
   almost always traces to spread width fluctuation. Always replay
   against real top-of-book.
2. **The high-confidence regime (0.50–0.75 midpoint) is the worst
   spread regime**, not the best. Intuition: when the market is unsure
   whether the favorite wins, makers widen out to protect against
   information-driven retail sellers.
3. **Strategy D V1's 2% fee assumption is optimistic** for the low-
   regime buckets it actually trades. Replay against real asks is
   required before live deployment.
4. **Real-arb candidates exist on the BID side** of the ladder, but
   need freshness-verification before being acted on. Cross-sectional
   sum-of-last-known-quote is an arb mirage.

## Related pages

- [[2026-04-11 Asymmetric mean reversion edge]] — the now-corrected
  midpoint finding; marked invalidated for taker execution
- [[2026-04-11 First pass 1-min price data exploration]] — Edge #3
  (short-the-ladder) now partially validated pending freshness check
- [[Polymarket CLOB WebSocket]] — data source for exp H's real-book
  replay; should add a "don't trust stale quotes in cross-sectional
  sums" note

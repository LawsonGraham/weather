---
tags: [synthesis, polymarket, edge, 1-min-data, mean-reversion, exploratory]
date: 2026-04-11
related: "[[Polymarket prices_history endpoint]], [[Polymarket CLOB WebSocket]], [[2026-04-11 First pass 1-min price data exploration]]"
---

# Asymmetric mean reversion in NYC daily-temp 1-min price data (2026-04-11)

**Edge candidate identified.** UP moves of ≥3 cents on active buckets
mean-revert 40% of their size in 10 minutes. DOWN moves do NOT revert —
they keep drifting. Naive "sell the 3c pop, cover at t+10" at midpoint
wins 65% of trades for +1.9 cents/trade (n=197). Independent of the
Strategy D favorite-drift edge.

## Headline

| trigger                        |   n | avg_pnl     | hit_rate |
|--------------------------------|-----|-------------|----------|
| **Sell 3c pop, cover t+10**    | 197 | **+1.90c**  | **64.8%** |
| Buy 3c dip, exit t+10          | 197 | -0.63c      | 50.0%   |

At larger trigger sizes (≥5c, n=35), the asymmetry is clearer: UP moves
revert 40% of their size, DOWN moves continue to drift down another 1.9c.

## Why asymmetric?

**Hypothesis — UP moves are retail FOMO, DOWN moves are informed.** When
price pops 3-5 cents on a thin book, a single buyer is walking the ladder.
Once they stop, the book fills back in at the prior level and midpoint
reverts. When price dips, a trader has new forecast info (HRRR update,
Google weather card, etc.) and is selling permanently — the new level
represents new information and stays.

Consistent with the exp C observation that ~95% of 1-min |Δp| concentrates
in the top 5 buckets around the favorite, where the book is thinnest and
retail overshooting is most visible.

## Interaction with [[2026-04-11 First pass 1-min price data exploration]]

- **Info-discovery peak is 21 UTC (17 EDT the day BEFORE resolution)** —
  this is likely when UP moves are most prevalent. Sell-pop should be
  most effective during these hours.
- **Volatility concentrates in top 5 buckets** — the edge doesn't exist
  on tail buckets (they sit at floor and rarely get pumped).
- **Ladder overround on active days is +5c** (april-13) — the market as
  a whole is over-priced; the mean-reversion may be part of how that
  overround creeps in (retail-driven pops push YES midpoints up; the
  market slowly reverts).

## Consequences for Strategy D

Strategy D V1 (favorite + 2 bucket, enter at 16 EDT) earns ~$3.36/trade
at 46% hit rate with a 2% fee (exp14/exp40). **Sell-pop at +1.9c/trade with
65% hit rate is in the same PnL ballpark — and it's an independent signal.**

**Throughput multiplier**: Strategy D fires once/day. Sell-pop fires 5-20
times/day PER ACTIVE BUCKET. The mean-rev edge could have 50-200x the
throughput. If both work and are uncorrelated, a combined portfolio has
materially better Sharpe than either alone.

## BUT — midpoint ≠ real execution

The +1.9c/trade is measured at MIDPOINT. Real execution requires:

1. The post-pop bid (where you'd actually sell) is within 1c of the
   post-pop midpoint. If the spread widens after a pop, you sell at a
   lower bid and lose half the edge immediately.
2. The cover-at-t+10 ask is within 1c of the t+10 midpoint. Otherwise
   you buy back at a worse price.

**The WS book recorder (running since 2026-04-11 19:24 UTC) will
eventually tell us the real bid/ask at these moments.** Once we have
4+ hours of recorded book depth overlapping with 3c+ pops, we can replay
the signal against real asks and compute post-spread PnL.

## Implementation path

Phase 1 (this week): validate the signal against real book data
- Wait for WS recorder to accumulate ≥8 h of coverage
- Transform book JSONL → parquet
- Replay all 3c+ UP moves from that window against real bid/ask
- Compute post-spread PnL
- If positive, proceed to Phase 2

Phase 2 (next week): paper-trade on live book
- Build a reactor that monitors the WS stream
- On observed 3c+ UP move, post a sell-limit at the current midpoint-1
- Cover at t+10 via market order or new sell-limit at the fresh midpoint
- Track fills, PnL, slippage in a logbook

Phase 3: live with small size
- Start at $100/trade
- Confirm live matches paper within 1 stdev
- Scale up over 30 trades

## Sample-size caveat

All 197 sell-pop observations come from a single day of 1-min data
(april-11 with rollback to april-10 evening and april-12/13 pre-close
activity). We need 3-5x more to trust the 65% hit rate.

Once april-12 has been through resolution (tomorrow evening), re-pull
the prices_history for that slug and re-run. Also re-run exp G weekly as
new data accumulates.

## Related

- [[2026-04-11 First pass 1-min price data exploration]] — parent synthesis
  that first observed the 17c 1-min moves on thin buckets
- [[Polymarket CLOB WebSocket]] — real-book-data source needed to validate
- [[Polymarket prices_history endpoint]] — source of the midpoint series
  used in the initial statistic

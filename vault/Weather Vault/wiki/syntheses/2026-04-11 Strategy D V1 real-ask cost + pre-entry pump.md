---
tags: [synthesis, polymarket, strategy-d, costing, flow-analysis]
date: 2026-04-11
related: "[[2026-04-11 Near-resolution ladder-bid arbitrage]], [[2026-04-11 Real-book replay invalidates sell-pop edge]], [[Polymarket CLOB WebSocket]]"
---

# Strategy D V1 real-ask cost + pre-16-EDT pump (2026-04-11)

## Two findings, one inspiring a Strategy D V2

### 1. Real-ask premium above backtest assumption

Strategy D V1 (exp14/exp40) assumes entry cost = `midpoint * 1.02`
(a 2% flat fee). Replayed the assumption for the 3 days we have live
tob book data on (april-11, 12, 13) at 16 EDT entry:

| day     | fav | +2 target | tgt mid | bt cost | **real ask** | premium   |
|---------|-----|-----------|---------|---------|--------------|-----------|
| april-11| 60-61 | 64-65  | 0.150   | 0.153   | **0.180**    | **+17.6%** |
| april-12| 54-55 | 58-59  | 0.275   | 0.281   | 0.290        | +3.4%      |
| april-13| 74-75 | 78-79  | 0.225   | 0.230   | 0.229        | -0.2%      |

Average premium: **+7%** above the backtest assumption. On a worst-day
(april-11), **+17.6%**. Strategy D V1's +$3.36/trade backtested PnL is
optimistic by ~4-9% after this correction — updated expected PnL is
$3.05-$3.24/trade. **Still positive, still material, but smaller.**

### 2. The +2 bucket PUMPS into 16 EDT entry on active days

Tracing april-11's +2 bucket (64-65°F YES) minute-by-minute through
the afternoon:

```
15:24 EDT → mid 0.070, ask 0.108    ← pre-pump
15:25 EDT → mid 0.036
15:27 EDT → mid 0.068
16:00 EDT → mid 0.150, ask 0.180    ← STRATEGY D V1 ENTRY, at the peak
…
17:51 EDT → mid 0.003, ask 0.004    ← complete collapse
```

**The +2 bucket more than doubled in the 36 minutes before Strategy
D V1's entry time, then collapsed to floor by the end of the trading
day.** This is the shape of a flow-driven pump: concentrated buying
pressure pre-16-EDT, followed by a collapse as the flow stops.

Hypotheses for the pump source (not distinguishable yet):

1. **Copycat Strategy D traders** — other systematic "buy fav+2 at 16
   EDT" strategies driving coincident flow.
2. **Retail directional bets on higher peaks** — people thinking "it's
   still warming up, the high might be 64°F", pumping the 64-65 bucket.
3. **Liquidity providers repositioning** — MMs pulling asks in
   anticipation of the 16 EDT event-risk.

Regardless of source, the empirical observation is clear: Strategy D V1
enters AT the top of a concentrated pre-entry price move.

### Counter-evidence from april-10 (boring day)

Ran the same path-trace for april-10's +2 bucket (62-63°F; favorite
was 58-59°F). **No pump.** The +2 bucket stayed at 0.003-0.006 the
entire 15:00-17:00 EDT window because the favorite was already at
0.97 — the market had resolved informationally by 15:18 EDT.

**The pump is DAY-REGIME dependent**:

| day regime | fav at 15:30 EDT | +2 pump visible? | comment |
|------------|------------------|------------------|---------|
| resolved / boring | ≥ 0.95 | NO  | market already right, no flow, no alpha |
| active / still converging | 0.4-0.85 | YES | flow piles into +2 bucket pre-16-EDT |

**The pump days are also the alpha days.** Strategy D V1 makes most
of its backtested PnL on the active/moving days (because those are
the days where the market is wrong about the favorite). On those same
days, the +2 bucket is being pumped by flow.

## Strategy D V2 hypothesis — move entry to 15:30 EDT

If the pump is predictable, the fix is to enter BEFORE it:

- **V1 entry**: 16:00 EDT, pay 0.150 mid / 0.180 ask on april-11
- **V2 entry**: 15:30 EDT, pay ~0.07 mid / ~0.08 ask on april-11 (pre-pump estimate)

**Savings estimate**: ~11 cents per active-day trade. On a 0.07 entry
basis that's a 155% relative improvement in cost. Even if the hit rate
drops from 46% to 40% due to less information at 15:30 EDT, the PnL
could still be significantly better.

**Risks of V2**:

1. **Less information**: morning METAR trajectory isn't fully baked at
   15:30 EDT. The +2 bucket might be wrong more often.
2. **Hit rate degradation**: the 46% backtested hit rate is 16-EDT-
   specific. A 15:30 entry needs its own backtest.
3. **Boring-day neutral**: V2 doesn't help on resolved days but also
   doesn't hurt. Portfolio-level, V2 ≥ V1 if the active-day PnL
   improvement outweighs any boring-day entry-time cost.

## Priority-1 followups

- **Multi-day validation**: run the minute-level +2 bucket path trace
  on all 571 historical days we have `/prices-history` for. Classify
  as "active" (favorite at 15:30 EDT was in 0.40-0.85 range) vs
  "boring" (≥ 0.95). Verify the pump pattern exists on active days.
- **Strategy D V2 backtest**: using prices_history min1 (the 42-slug
  × 24h 1-min data), run V2 at 15:30 EDT entry and report net PnL
  on active days vs Strategy D V1 at 16:00 EDT.
- **Identify WHO is pumping**: parse `last_trade_price` events from
  the live WS stream during the 15:30-16:00 EDT window on an active
  day. If there's a cluster of aggressive market orders on the +2
  bucket, that's the flow. Count them to estimate order size.

## What this changes in the vault

- **Strategy D V1 PnL is down-revised** by 4-9% due to the real-ask
  premium. Still positive, still material.
- **Strategy D V2 at 15:30 EDT is a new candidate** that could recover
  the lost 4-9% AND potentially add 5-10% more by avoiding the pump
  on active days.
- **New research question**: who's pumping the +2 bucket at 16 EDT,
  and can we detect their flow signature in real-time?

## Related pages

- [[2026-04-11 Near-resolution ladder-bid arbitrage]] — the other
  live-edge finding from today's session
- [[2026-04-11 Real-book replay invalidates sell-pop edge]] — parent
  synthesis that first showed spread regime matters > midpoint
- [[Polymarket CLOB WebSocket]] — data source for the real-ask replay

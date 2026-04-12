---
tags: [synthesis, polymarket, competitor, arb, execution]
date: 2026-04-11
related: "[[2026-04-11 Near-resolution ladder-bid arbitrage]], [[Polymarket CLOB WebSocket]]"
---

# Polymarket arb taker bot fingerprint (2026-04-11)

Clustering `last_trade_price` events from 4 hours of our WS capture
surfaces a clean competitor profile. **At least one disciplined
multi-leg ladder-arb bot is already running** on NYC daily-temp
markets. Characterizing them gives us our competitive options.

## The signature

From 10 multi-leg clusters observed:

- **Primary leg size**: 5.0 shares (7/10 SELL clusters)
- **Median legs per cluster**: 9.5 (range 8-21)
- **Execution speed**: 10-50 ms for most clusters
- **One burst at 0.4 ms** (colocation or batched-orders API)
- **SELL:BUY ratio**: 7:3
- **Rate**: ~2.5 clusters/hour during active periods
- **Inter-cluster gap**: median 8.5 min (flow-driven, not periodic)
- **Discipline**: SELL clusters only hit live bids, never dead buckets

## The SELL pattern = ladder-BID arb (our target strategy)

7 of 10 clusters are the classic ladder-bid execution:

```
Timeline of the 23:15:21.089 UTC cluster:
  +0.0ms  SELL 7 YES of 59forbelow @ 0.08
  +0.0ms  SELL 7 YES of 65f        @ 0.003
  +0.0ms  SELL 7 YES of 47forbelow @ 0.002
  +0.0ms  SELL 7 YES of 61f        @ 0.015
  +0.0ms  SELL 7 YES of 66forhigher@ 0.005
  +0.0ms  SELL 7 YES of 51f        @ 0.032
  +0.0ms  SELL 7 YES of 49f        @ 0.006
  +0.0ms  SELL 7 YES of 63f        @ 0.007
  +0.0ms  SELL 7 YES of 53f        @ 0.171
  +0.4ms  SELL 7 YES of 55f        @ 0.38
  total notional: $4.91
  total duration: 0.4 ms
```

This is a **single async batched submission**, all legs hit the
matcher essentially simultaneously. Polymarket likely has a batched-
orders endpoint we haven't explored yet.

## The BUY clusters = different strategy (likely different bot)

3 BUY clusters with irregular sizes (9.48, 9.02, 5.26) target
dead-tail buckets at tick-floor prices (0.01-0.02). Possibilities:

1. **Short-cover**: same bot closing earlier short positions cheaply
2. **Cheap-option accumulation**: separate strategy buying near-zero-
   probability tails as lottery tickets
3. **Fat-finger human trades**: irregular sizes suggest manual or
   semi-manual

Irregular sizing vs uniform-5 for the SELL side → **most likely two
different actors**.

## Competitive positioning

Given the fingerprint, our options:

### Option 1: head-to-head

Build an identical 5-share SELL ladder-arb bot. Compete for the same
bids. Catch ~30% of opportunities (the ones where we're faster or
where the competitor is absent). Estimated **$1.50-$9/day NYC**.

### Option 2: undercut on size

Use 3-share legs. Stay below the 5-share floor. When they deplete
the top-of-bid at their 5-share level, we sit on the residual. ~20%
of gross. **$1-$6/day NYC**.

### Option 3: piggyback

Observe when they fire (via `last_trade_price` event stream). Within
2 seconds of a 5-leg SELL burst, fire our own 3-share SELL at
whatever bids just refreshed. ~10% of gross, targeting the
20:12:14-style 21-leg re-open bursts where we can ride the tail.
**$0.50-$3/day NYC.**

### Option 4: target non-NYC cities (recommended)

Deploy the watchman + taker to Chicago, Miami, Philly, LA (4 cities).
Measure competition per city. Any city without a competitor captures
the full **$5-30/day per city** capacity from exp P.

If we're uncontested in 4 cities, total capacity: **$20-120/day** at
realistic catch rates.

**This is the best path.** NYC is the canonical / most-visible
Polymarket weather market; other cities probably haven't attracted
dedicated bots yet.

## Execution stack requirements

- **Batched async order submission** via httpx/aiohttp. Target 10-50
  ms from observe → all-legs-hit. Cannot match the 0.4 ms colo tier.
- **WS stream consumer** for per-bucket state. Already have this
  (the recorder + watchman).
- **Alert → execute pipeline** with <100 ms total latency.
- **Per-leg fill tracking** with partial-fill handling. If leg 7 of
  10 fails, need to assess remaining position risk.
- **Capital mgmt**: ~$50 per cycle at 5-share scale × 10 legs × up to
  20 concurrent markets = ~$10k max capital commitment, probably far
  less in practice.

## Open questions

1. **Is the taker bot a Polymarket-official market-making incentive
   participant?** Could be running under an exchange-sponsored spread
   program. If so, they may have better execution than us.

2. **Does Polymarket have a public batched-orders API?** The 0.4 ms
   burst suggests yes. Worth checking `py-clob-client` docs.

3. **Is the BUY bot the same entity or different?** Can we cross-
   reference Polygon wallet addresses from the fill events? (Polymarket
   fills are on-chain so addresses are public.)

4. **How does the bot find opportunities?** Same WS stream we use, or
   faster infrastructure? If same WS, we're latency-equal on
   observation and they just have faster submission.

## Followups for tomorrow's session

- **Deploy watchman to 4 non-NYC cities** (Chicago, Miami, Philly, LA).
  Run 24h. Count multi-leg clusters per city to measure competition.
- **Build a minimal batched-order-submit test** against Polymarket's
  CLOB using `py-clob-client`. Measure our actual end-to-end latency
  from alert → fill.
- **Port the exp R cluster detection logic into a live watchman add-on**
  that logs every multi-leg cluster in real time with a "competitor
  fired" alert.

## Related

- [[2026-04-11 Near-resolution ladder-bid arbitrage]] — parent edge
  synthesis; the competitive context makes its capacity estimates
  more realistic
- [[Polymarket CLOB WebSocket]] — the data source that surfaces both
  the arbs and the competitor fills

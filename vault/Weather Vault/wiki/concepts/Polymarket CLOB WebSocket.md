---
tags: [concept, data-source, polymarket, websocket]
date: 2026-04-11
related: "[[Polymarket]], [[Polymarket prices_history endpoint]]"
---

# Polymarket CLOB WebSocket

`wss://ws-subscriptions-clob.polymarket.com/ws/market`

The live **order-book + trade stream** for Polymarket's CLOB. This is the only way to get L2 book depth and real-time fills — `/prices-history` only returns midpoint. No historical playback: you must be connected to receive. If you miss it, it's gone.

## Subscription

```json
{"type": "MARKET", "assets_ids": ["<yes_token_id>", "<no_token_id>", ...]}
```

Both the YES and the NO token of a binary NegRisk market are separate `asset_id`s. Subscribe to both sides if you want the full picture — in practice only one side is actively quoted at a time (high-probability outcomes have thin offers, low-probability outcomes have thin bids), but the feeds are independent.

**There is no documented upper limit** on the number of `assets_ids` per subscription. We subscribe to all 42 open NYC daily-temp slugs × 2 tokens = 84 tokens in a single WS connection without issue.

## Message types

All messages have an `event_type` field (also echoed as `type`). Four kinds:

### 1. `book` — full L2 snapshot

```json
{
  "event_type": "book",
  "asset_id": "<token>",
  "market": "<condition_id_hex>",
  "timestamp": "<ms since epoch>",
  "hash": "<sha1-ish>",
  "tick_size": "0.01",
  "bids": [{"price": "0.405", "size": "1000"}, ...],
  "asks": [{"price": "0.415", "size": "500"}, ...]
}
```

Sent on initial subscription for every token. Also re-sent whenever the server thinks the client needs a resync (unclear trigger — observed every ~15 s for active tokens). Prices and sizes are **string-encoded floats** (not numbers), presumably to preserve tick precision.

### 2. `price_change` — incremental update

```json
{
  "event_type": "price_change",
  "market": "<condition_id>",
  "timestamp": "<ms>",
  "price_changes": [
    {
      "asset_id": "<token>",
      "price": "0.405",
      "size": "1000",
      "side": "BUY",
      "hash": "<sha1>",
      "best_bid": "0.405",
      "best_ask": "0.415"
    }
  ]
}
```

**Routing gotcha:** `asset_id` is **nested inside `price_changes[]`**, not at the top level. The top-level `market` is the condition_id, which maps to two tokens (YES + NO) — you cannot route by `market` alone. Our recorder extracts `price_changes[0].asset_id` when the top-level `asset_id` is missing.

A `price_change` replaces the resting order book at that price level. Size > 0 means there's `size` at this price; size = 0 means the level is empty. `side` is BUY (bid) or SELL (ask).

### 3. `last_trade_price` — trade just happened

```json
{
  "event_type": "last_trade_price",
  "asset_id": "<token>",
  "market": "<condition_id>",
  "price": "0.41",
  "side": "BUY",
  "size": "100",
  "fee_rate_bps": "0",
  "timestamp": "<ms>"
}
```

Polymarket's on-chain fee is currently zero (`fee_rate_bps: 0`). The 2 % fee in our backtests is a conservative bid/ask-spread approximation, not an explicit venue fee.

### 4. `tick_size_change` — minimum price increment changed

Rare. Observed zero times in smoke tests; documented for completeness.

## Observed traffic volume

NYC daily-temp subscription (42 open slugs × 2 tokens, 2026-04-11 afternoon):

- ~100 msgs/sec sustained during the 14–18 EDT pre-resolution hour
- ~60 % `price_change`, ~35 % `book` resyncs, ~5 % `last_trade_price`
- 30-second smoke test: 2047 messages, all 42 subscribed slugs populated with ≥1 record

Expected ~100k messages/hour during active NYC trading, tapering off overnight.

## Our implementation

`scripts/polymarket_book/download.py` is a single-connection recorder using the `websockets` library (no py-clob-client WS support exists — the library is REST-only). Design:

- Loads open NYC daily-temp slugs from `data/processed/polymarket_weather/markets.parquet`, orders by **proximity to today** so we hit the most actively traded near-resolution markets first
- Opens one WS connection, subscribes to all YES + NO tokens in one `MARKET` message
- Persists messages as JSONL partitioned by slug and hour: `data/raw/polymarket_book/<slug>/YYYY-MM-DD-HH.jsonl`
- Each persisted record is the raw upstream message plus a `_received_at` wall-clock timestamp (we trust our clock more than the server's for latency analysis)
- Reconnects with exponential backoff (1s → 60s, reset on successful connect)
- Runs as a `caffeinate -i` daemon so macOS power management doesn't suspend it

## Why JSONL not parquet at ingest

Stream-write append is trivial with JSONL. Parquet needs batching + schema; the 4-way union of message types would force a sparse schema. A future `transform.py` can convert to parquet once enough data accumulates and we know what columns we actually want.

## Known unknowns

- **No documented reconnect-resume**: we assume disconnects lose messages in the gap. The next `book` resync after reconnect should bring us back to a consistent state.
- **Server-side resync cadence** is undocumented. Observed ~every 15s per active token but we haven't characterized it.
- **Tick-size changes**: unobserved in smoke; unclear whether active NYC markets ever emit these.

## Related

- [[Polymarket]] — parent entity
- [[Polymarket prices_history endpoint]] — historical midpoint endpoint; complements the live WS book

# Architecture

The whole system is 4 small files. Read in this order, top to bottom:

```
src/
├── consensus_fade_plus1/
│   ├── discover.py    ← "what markets do we want to trade today?"
│   ├── strategy.py    ← "how do we actually buy them?"
│   ├── node.py        ← "wire discover + strategy into Nautilus"
│   └── cli.py         ← "what the operator runs"
│
└── lib/
    ├── weather/       ← forecast loaders used by discover.py
    └── watchers/      ← background pollers that keep data fresh
```

## Data flow (daily)

```
  ┌────────────────────────────────────────────────────────────────────┐
  │  Background daemon (cfp daemon — a separate process)                │
  │                                                                    │
  │  Every few minutes, watchers refresh:                              │
  │    - NBS forecasts (data/processed/iem_mos/NBS/)                   │
  │    - GFS MOS forecasts (data/processed/iem_mos/GFS/)               │
  │    - HRRR forecasts (data/processed/hrrr/)                         │
  │    - METAR observations (data/processed/iem_metar/)                │
  │    - Polymarket market catalog (data/processed/polymarket_weather/)│
  │    - Unified features parquet (data/processed/backtest_v3/)        │
  └────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │  cfp run (separate process — starts the Nautilus node)              │
  │                                                                    │
  │  1. discover.py queries features parquet + markets parquet:        │
  │     "which cities have consensus ≤ 3°F AND a +1 offset bucket      │
  │      that exists?" Returns list of (condition_id, no_token_id).    │
  │                                                                    │
  │  2. node.py builds a Nautilus TradingNode:                         │
  │     - PolymarketDataClient subscribes to those instruments' books  │
  │     - PolymarketExecClient handles order placement/cancel/fills    │
  │     - ConsensusFadeStrategy is wired in                            │
  │                                                                    │
  │  3. strategy.on_start() places ONE limit BUY per market at         │
  │     max_no_price (default 0.92) with qty=shares_per_market (110).  │
  │                                                                    │
  │  4. Polymarket's matching engine auto-fills our resting orders     │
  │     as new retail YES-bids appear in range. We pay the ASK price   │
  │     (always ≤ our limit), maker fee is zero.                       │
  │                                                                    │
  │  5. strategy.on_order_filled() logs each fill. Order keeps resting │
  │     until fully filled or we stop the node.                        │
  │                                                                    │
  │  6. Ctrl+C → strategy.on_stop() cancels all open orders.           │
  └────────────────────────────────────────────────────────────────────┘
```

## Why this design is simple

1. **Matching happens server-side.** Polymarket's CLOB matches asks against
   our resting bid. We don't need to watch the book or cancel/replace on
   every update. One order per market. Done.

2. **Nautilus handles the hard stuff.** L2 book subscriptions, reconnect
   logic, order state machine, fills via user-channel WSS — all in their
   adapter. We write ~50 lines of strategy code; Nautilus owns 80+ KB of
   plumbing.

3. **"Range buying" = one limit at our max price.** The user's ask was
   "fill anything in our buying range." A limit BUY at $0.92 literally does
   this: any ask that appears ≤ $0.92 matches us. No additional logic.

4. **Separation of concerns.** Data ingestion runs as a separate daemon
   process. Trading runs as its own process. They communicate via the
   filesystem (parquet files). Either can crash without affecting the other.

## File-by-file

### `discover.py` (~120 lines)

Input: a target date (defaults to today UTC).
Output: list of `TradeableMarket` — one per (city, +1 bucket) pair that
passes filters.

Filters: NBS + GFS present, consensus_spread ≤ 3°F, +1 bucket exists for
the city on that market_date.

### `strategy.py` (~100 lines)

Three methods: `on_start`, `on_stop`, `on_order_filled`. That's it.

On start: for each market, submit a limit BUY at `max_no_price` for
`shares_per_market` shares. Nautilus handles the rest.

On fill: log it. The remaining qty stays resting automatically.

On stop: cancel open orders.

### `node.py` (~80 lines)

Builds a `TradingNode` with Polymarket data + exec clients configured,
wires in `ConsensusFadeStrategy`, calls `node.run()` which blocks until
SIGINT/SIGTERM.

### `cli.py` (~90 lines)

Argparse subcommand dispatch. Each subcommand is ~5-10 lines:
- `setup`: wallet bootstrap
- `discover`: dry-run discovery
- `run`: start the trading node
- `daemon`: start the data watchers
- `watchers`: check watcher state

## Operational mental model

- **Every day, around the time you want to trade:**
  1. The data daemon (already running in the background) keeps feeds fresh.
  2. You run `cfp run`. It discovers markets, places orders, waits for fills.
  3. Leave it running. Each order sits on the book absorbing flow.
  4. Before markets resolve (~midnight UTC), Ctrl+C to cancel unfilled.

- **Scaling**: adjust `--shares-per-market` / `--max-no-price` as knobs.
  Fewer shares = less capital at risk. Lower max price = tighter edge
  requirement (fewer fills but higher per-fill edge).

- **Debugging**: logs go to stdout (Nautilus default). No hidden state —
  everything the strategy knows is visible in its constructor's dict.

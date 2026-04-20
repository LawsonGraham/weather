# Architecture

Five files, read top-to-bottom in this order:

```
src/
├── consensus_fade_plus1/
│   ├── discover.py      ← data function: "which markets pass consensus right now?"
│   ├── persistence.py   ← data sinks: LedgerWriter + BookSnapshotWriter
│   ├── strategy.py      ← the `while True` loop + IOC take logic
│   ├── node.py          ← wire everything into a Nautilus TradingNode
│   └── cli.py           ← operator entry points
│
└── lib/
    ├── weather/         ← forecast loaders used by discover.py
    └── watchers/        ← background pollers that keep data fresh
```

## Data flow

```
  ┌────────────────────────────────────────────────────────────────────┐
  │  cfp daemon — separate process                                      │
  │                                                                    │
  │  6 watchers probe upstream every 10s. On change, they fetch:       │
  │    NBS + GFS  → incremental append (~2s)                           │
  │    HRRR       → S3 HEAD probe, download new cycles                 │
  │    METAR      → current-month re-fetch                             │
  │    markets    → hourly Polymarket catalog refresh                  │
  │    features   → rebuilds backtest_v3/features.parquet              │
  └────────────────────────────────────────────────────────────────────┘
                                │
                                ▼  (parquet files on disk)
  ┌────────────────────────────────────────────────────────────────────┐
  │  cfp run — separate process, starts the Nautilus node               │
  │                                                                    │
  │  1. discover.py → initial list of tradeable markets                │
  │  2. node.py builds TradingNode with:                               │
  │       - PolymarketDataClient (L2 book deltas via WSS)              │
  │       - PolymarketExecClient (signs + submits orders)              │
  │       - ConsensusFadeStrategy wired in                             │
  │  3. Strategy runs its own asyncio task — a `while True` loop       │
  │     that ticks every 0.5s (see below)                              │
  │  4. Ledger + snapshots write JSONL under data/processed/cfp_*/     │
  │  5. Ctrl+C → on_stop() cancels pending, flushes writers            │
  └────────────────────────────────────────────────────────────────────┘
```

## The strategy's tick loop

Every 0.5s the strategy does this:

```
# (1) Refresh state — each function early-returns if nothing changed
state.active = refresh_active_markets(features.parquet)

# (2) For each currently-active market, consider taking liquidity
for iid in state.active:
    if iid in state.pending:             # an IOC is still resolving
        continue
    room = shares_per_market - state.positions[iid]
    if room < min_order_shares:          # we've hit the cap
        continue
    takeable = sum asks at ≤ max_no_price
    if takeable < min_order_shares:      # no in-range liquidity right now
        continue
    submit IOC BUY sized min(takeable, room) at max_no_price
```

### Why IOC (immediate-or-cancel), not resting limit

We want to **take** liquidity that's currently there, not **rest** on the book
advertising our interest. An IOC at price X:
- Crosses immediately against any existing ask at ≤ X
- Takes what it can (partial fills OK)
- Cancels anything unfilled
- Never sits visible on the book

This means:
- If nothing qualifying is on the book right now, the tick is a no-op —
  no capital sits in reserve against a resting order that may never fill.
- When retail places a new YES-bid that crosses, we see it on the next
  book delta and the next tick takes it.
- We never pay above our price ceiling.

### What triggers re-evaluation

- **New book state**: every book delta from Polymarket updates Nautilus's
  cache. The next tick reads the updated book.
- **New forecast data**: when a watcher rebuilds `features.parquet`, the
  mtime changes. The next `_refresh_active_markets()` re-runs discovery
  and the active set updates. If a market drops out of consensus, we stop
  acting on it; if it comes back, we resume.
- **Our own fills**: update `state.positions[iid]` locally on each fill
  so the per-market cap is enforced without waiting for Nautilus's
  portfolio reconciliation.

## Persistence

Two append-only JSONL files, rotated daily at UTC midnight:

- `data/processed/cfp_ledger/YYYY-MM-DD.jsonl` — every order event
  (submitted, accepted, filled, canceled, rejected) + active-set changes
  (active_added, active_removed) + session boundaries.
- `data/processed/cfp_book_snapshots/YYYY-MM-DD.jsonl` — top-10 bids +
  asks per subscribed instrument, every 10 minutes.

Both survive process crashes (flushed after every write). Rotation is
automatic — you can safely run the node across UTC midnight.

## File-by-file

### `discover.py` (~150 lines)

Input: target date (defaults to today UTC).
Output: `list[TradeableMarket]` — (city, +1 bucket, condition_id, no_token_id, …)

Filters: NBS + GFS present, consensus_spread ≤ 3°F, +1 bucket exists.
Called once at startup (for initial subscription list) and then from
the strategy's tick loop whenever features.parquet changes.

### `persistence.py` (~100 lines)

Two classes: `LedgerWriter` and `BookSnapshotWriter`. Both are thin wrappers
around a shared `_DailyJSONLWriter` that handles daily rotation + lazy
file opening. No external deps.

### `strategy.py` (~280 lines)

The continuous-polling strategy. Structure:

- `ConsensusFadeConfig` — frozen dataclass of knobs
- `StrategyState` — mutable dict of what we know (active markets, positions,
  pending orders, loop control)
- `ConsensusFadeStrategy` — Nautilus `Strategy` subclass:
    - `on_start` / `on_stop` — wallet up + down
    - `_main_loop` — the `while True` asyncio task
    - `_tick` — one iteration (refresh state, decide whether to act)
    - `_maybe_take` / `_submit_ioc_buy` — order submission
    - `on_order_*` event hooks — ledger writes + position updates
    - `_on_snapshot_timer` — periodic book snapshot to disk

Read top-to-bottom. The control flow is a single `while self.state.running:`
loop that calls `_tick()` every 0.5s.

### `node.py` (~130 lines)

Builds a Nautilus `TradingNode` with:
- Polymarket data + exec clients scoped to today's discovered instruments
- Our `ConsensusFadeStrategy` wired in
- Blocks on `node.run()` until SIGINT/SIGTERM

### `cli.py` (~200 lines)

Argparse dispatch. Subcommands:
- `setup` / `setup --check` — wallet bootstrap (one time)
- `daemon` — start all 6 watchers
- `watch <name>` — run ONE watcher live (for testing)
- `watchers` — show watcher state
- `discover` — dry-run discovery (no orders)
- `run` — start the trading node

## Operational mental model

- **Ingestion daemon runs continuously.** `cfp daemon` in a screen / tmux /
  systemd unit. Watchers probe every 10s, refetch only on change.
- **Trading node starts when you want to trade.** `cfp run` discovers
  markets, subscribes to books, starts ticking. Leave it running. It
  picks up new data automatically.
- **Every action is recorded.** If you want to know what happened, read
  `cfp_ledger/<today>.jsonl` or `cfp_book_snapshots/<today>.jsonl`.
- **Safe to kill at any time.** `on_stop` cancels in-flight orders
  (shouldn't be any since all orders are IOC, which terminate in milliseconds)
  and flushes writers.

## Scaling knobs

Config parameters (all in `ConsensusFadeConfig`, all overridable from CLI):

- `max_no_price`: edge threshold. Tighter = fewer fills, higher per-fill edge.
- `shares_per_market`: per-market position cap. Caps capital at risk.
- `min_order_shares`: minimum shares per IOC. Respects venue minimum.
- `tick_interval_seconds`: main loop cadence. 0.5s is the default.

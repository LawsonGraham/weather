# Architecture

Five files, read top-to-bottom in this order:

```
src/
├── consensus_fade_plus1/
│   ├── discover.py      ← data function: "which markets pass consensus right now?"
│   ├── persistence.py   ← data sinks: LedgerWriter + BookSnapshotWriter
│   ├── strategy.py      ← the `while True` loop + IOC take logic + daily rollover
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
  │  Runs continuously across UTC midnight. No daily restart.          │
  │                                                                    │
  │  1. node.py builds TradingNode with:                               │
  │       - PolymarketDataClient (L2 book deltas via WSS)              │
  │       - PolymarketExecClient (signs + submits orders)              │
  │       - ConsensusFadeStrategy wired in                             │
  │  2. Strategy starts a `while True` asyncio task that ticks every   │
  │     0.5s (see below)                                               │
  │  3. Nautilus's Polymarket adapter AUTO-LOADS any instrument the    │
  │     strategy subscribes to post-startup — fetches from Gamma,      │
  │     caches, opens the WSS sub. No manual pre-load required.        │
  │  4. Ledger + snapshots write JSONL under data/processed/cfp_*/     │
  │  5. Ctrl+C → on_stop() cancels pending, flushes writers            │
  └────────────────────────────────────────────────────────────────────┘
```

## The strategy's tick loop

Every 0.5s the strategy does this:

```
# (1) Sync subscriptions + active set with the current data picture.
#     Early-returns if nothing changed (mtime-gated).
_refresh_subscribed_and_active():
    for d_offset in [0 .. lookahead_days]:
        for m in discover_tradeable_markets(today + d_offset):
            if m.iid not in state.subscribed:
                subscribe_order_book_deltas(m.iid)   # Nautilus auto-loads
                state.subscribed.add(m.iid)
            if d_offset == 0:
                new_active[m.iid] = m     # today-only in the tradeable set
    state.active = new_active

# (2) Unsubscribe markets past end_date (throttled to every ~5 min).
_unsubscribe_expired():
    for iid in state.subscribed:
        if cache.instrument(iid).expiration_ns < now:
            unsubscribe_order_book_deltas(iid)
            state.subscribed.discard(iid)

# (3) For each active market, maybe take liquidity
for iid in state.active:
    if iid in state.pending:              # IOC still resolving
        continue
    room = shares_per_market - state.positions[iid]
    if room < min_order_shares:           # per-market cap hit
        continue
    takeable = sum asks at ≤ max_no_price
    if takeable < min_order_shares:       # no in-range liquidity right now
        continue
    submit IOC BUY sized min(takeable, room) at max_no_price
```

### Why IOC (immediate-or-cancel), not resting limit

We **take** liquidity that's currently on the book, we don't **rest** advertising.
An IOC at price X:
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

### Daily rollover across UTC midnight

This is the hard part we built for. Polymarket creates new daily-temperature
markets ~4 days before each resolution, and resolves them at `end_date` midnight
UTC. A bot that runs continuously needs to:

1. **Pick up new markets as they're created.** The daemon's `MarketsWatcher`
   refreshes `markets.parquet` hourly; the strategy tick re-discovers against
   this file every tick (mtime-gated).
2. **Subscribe to new markets' books.** The strategy calls
   `subscribe_order_book_deltas(new_iid)`. Nautilus's **auto-load** feature
   (PR f6f2a76, pinned in pyproject.toml) handles the cache-miss transparently:
   batched Gamma load → publish to cache → open WSS sub. To the strategy
   it's one synchronous call.
3. **Unsubscribe resolved markets.** Every ~5 min the tick scans the
   subscribed set and calls `unsubscribe_order_book_deltas` on anything
   whose `instrument.expiration_ns` has passed. This keeps WSS shards
   healthy and avoids logging spam on dead markets.
4. **Roll the "today" semantic.** Discovery uses `datetime.now(UTC).date()`
   as its default target date. When the clock crosses midnight UTC, the
   next tick sees a new `today`, re-runs discover, and today's new markets
   become the `active` set. The previous day's markets drop out of active
   (and will be unsubscribed by step 3).

The strategy also pre-subscribes to **today + `lookahead_days`**. With
`lookahead_days=1`, tomorrow's +1 bucket markets are already subscribed and
have warm book state by the time they roll into today. No book-warmup lag
at the moment of rollover.

## Persistence

Two append-only JSONL files, rotated daily at UTC midnight:

- `data/processed/cfp_ledger/YYYY-MM-DD.jsonl` — every order event
  (submitted, accepted, filled, canceled, rejected) + rollover events
  (subscribed, unsubscribed, active_added, active_removed) + session boundaries.
- `data/processed/cfp_book_snapshots/YYYY-MM-DD.jsonl` — top-10 bids +
  asks per subscribed instrument, every 10 minutes.

Both survive process crashes (flushed after every write). Rotation is
automatic — a running node writes into the correct file across UTC midnight.

## File-by-file

### `discover.py` (~150 lines)

Input: target date (defaults to today UTC).
Output: `list[TradeableMarket]` — (city, +1 bucket, condition_id, no_token_id, …)

Filters: NBS + GFS present, consensus_spread ≤ 3°F, +1 bucket exists,
`end_date == target_date`. Called every strategy tick (cheap — mtime-gated
in the strategy).

### `persistence.py` (~100 lines)

Two classes: `LedgerWriter` and `BookSnapshotWriter`. Both thin wrappers
around a shared `_DailyJSONLWriter` that handles daily rotation + lazy
file opening.

### `strategy.py` (~380 lines)

The continuous-polling strategy. Structure:

- `ConsensusFadeConfig` — frozen dataclass of knobs (including `lookahead_days`)
- `StrategyState` — mutable state (`subscribed`, `active`, `positions`, `pending`, loop control)
- `ConsensusFadeStrategy` — Nautilus `Strategy` subclass:
    - `on_start` / `on_stop` — wallet up + down
    - `_main_loop` — the `while True` asyncio task
    - `_tick` — one iteration: refresh state, unsubscribe expired, take
    - `_refresh_subscribed_and_active` — rollover subscribe + active-set compute
    - `_unsubscribe_expired` — rollover unsubscribe (throttled)
    - `_maybe_take` / `_submit_ioc_buy` — IOC order submission
    - `on_order_*` event hooks — ledger writes + position updates
    - `_on_snapshot_timer` — periodic book snapshot to disk

### `node.py` (~130 lines)

Builds a Nautilus `TradingNode` with:
- Polymarket data + exec clients scoped to today's discovered instruments
  (initial seed; the strategy expands via auto-load as needed)
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

- **Ingestion daemon runs forever.** `cfp daemon` in a screen / tmux /
  systemd unit. Watchers probe every 10s, refetch only on change.
- **Trading node runs forever.** `cfp run` starts the node and leaves it
  running. Handles market rollover automatically — no restarts needed.
- **Every action is recorded.** Order events + rollover events + session
  boundaries all land in `cfp_ledger/<today>.jsonl`. Book state is
  snapshotted to `cfp_book_snapshots/<today>.jsonl` every 10 min.
- **Safe to kill at any time.** `on_stop` cancels in-flight orders
  (shouldn't be any since all orders are IOC, which terminate in milliseconds)
  and flushes writers.

## Scaling knobs

Config parameters (all in `ConsensusFadeConfig`, all overridable from CLI):

- `max_no_price`: edge threshold. Tighter = fewer fills, higher per-fill edge.
- `shares_per_market`: per-market position cap. Caps capital at risk.
- `min_order_shares`: minimum shares per IOC. Respects venue minimum.
- `tick_interval_seconds`: main loop cadence. 0.5s is the default.
- `lookahead_days`: how many days ahead to keep subscribed. 1 = today+tomorrow.

## Why nautilus_trader is pinned to a git SHA

`pyproject.toml` pins `nautilus-trader` to commit `f6f2a76` on develop —
the auto-load commit from 2026-04-19, which didn't make it into the v1.225.0
release. Without auto-load, `subscribe_order_book_deltas(new_iid)` would
open a WSS sub but drop every message because the instrument wouldn't be in
the cache. Auto-load makes dynamic subscription just-work.

The pin includes the three bug fixes that landed on develop between v1.225.0
and f6f2a76 (commission formula, reconciliation commission, min_quantity).

Re-audit before bumping:
https://github.com/nautechsystems/nautilus_trader/compare/f6f2a76...develop

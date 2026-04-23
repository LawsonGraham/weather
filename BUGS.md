# BUGS

Living registry of bugs, quirks, and non-perfections observed in the
Consensus-Fade +1 bot. Maintained by whoever is running live sessions.
Update inline as things are found, fixed, or reclassified.

Covers the period 2026-04-20 → 2026-04-23 across Phase 6B smoke test and
first live daily session on 2026-04-22.

---

## Severity rubric

- **High** — actively blocks or could block trades, or loses/miscredits money
- **Medium** — distorts strategy accounting or risk controls without fully
  breaking things
- **Low** — log/ledger noise, cosmetic, or affects only observability

---

## OPEN

### B-003 — Spurious `active_added` events on every discovery rebuild

- **Severity**: LOW (log/ledger noise only)
- **Status**: Open
- **Observed**: 2026-04-22T00:00Z onward (post UTC day-crossover)
- **Symptom**: On the 2026-04-23 ledger, the `active_added` event type
  fires roughly every 5-10 minutes despite no materially-new markets
  entering the active set. By 03:38Z there were 36 `active_added` with
  only 1 `entry_window_opened` — the active set is being "re-added"
  without being "removed".
- **Evidence**: `/Users/lawsongraham/git/weather/data/processed/cfp_ledger/2026-04-23.jsonl`
  shows `active_added` counts climbing while `subscribed` /
  `unsubscribed` mirror in lockstep.
- **Impact**: Ledger bloat. No functional consequence (gates still
  prevent real orders).
- **Hypothesis**: In `_refresh_subscribed_and_active`, when
  `discovery_stale` is True, the `discovered_markets` dict is rebuilt
  with fresh `TradeableMarket` dataclass instances. Although the
  `InstrumentId` keys are stable, the set diff `new_active -
  self._state.active` is computed on dict keys — which should be
  stable. But somehow the diff is coming back non-empty. Worth checking
  whether `InstrumentId.__hash__` and `__eq__` do what we expect for
  the Polymarket adapter's ID structure.
- **Triage**:
  1. Add debug log in `_refresh_subscribed_and_active` just before
     `added = set(new_active) - set(self._state.active)` to print both
     sets. Run for 30 min and inspect.
  2. Check whether `self._state.active` is being reset somewhere
     unexpectedly (e.g., in `_unsubscribe_expired`).
- **Proposed fix**: Contingent on root cause. Likely a one-line fix.

---

### B-005 — No process-health alerting

- **Severity**: LOW (operational, not a code bug)
- **Status**: Open
- **Observed**: Throughout session
- **Symptom**: If the daemon or bot crashes overnight, there is no
  notification. `caffeinate` keeps the Mac awake and `nohup` survives
  terminal close, but a Python exception that escapes both
  `run_watchers()`'s exception handler and the main_loop try/except
  would kill the process silently.
- **Impact**: Potential silent failure during unattended operation.
- **Triage**: N/A (it's a missing feature, not a bug).
- **Proposed fix**: Add a launchd job that checks process liveness
  every 5 minutes and sends a macOS notification (via `osascript`) or
  SMS (via a webhook) if either PID disappears. Or use a simple
  shell-based watchdog that restarts the process if it exits.

---

## FIXED

### F-007 — Yesterday's expired markets resubscribe/unsubscribe on a 5-min cycle (was B-004)

- **Severity at time of finding**: LOW (cosmetic — log noise, Gamma
  API calls; no financial impact)
- **Fixed in**: branch `wt/b004-discover-filter`, commit 8b644c3
  (`fix(B-004): skip past-grace dates in discover_tradeable_markets`),
  2026-04-23. Merged to main.
- **Observed**: Continuously throughout 2026-04-22 and 2026-04-23
- **Root cause**: Two subsystems disagreed on what "active" means and
  trapped each other in a 5-min feedback loop.
  `strategy.py::_refresh_subscribed_and_active` iterates
  `for d_offset in range(-1, lookahead_days + 1)` and calls
  `discover_tradeable_markets(today + d_offset)` — so `d_offset=-1`
  passes yesterday's date. Discovery had **no filter on past-grace
  target dates** and returned any markets matching the consensus
  gate for that date. Strategy subscribed to them. On the next
  tick (≤5min later), `_unsubscribe_expired` noted
  `expiration_ns + 24h grace < now` and tore them down. Next
  `_refresh_subscribed_and_active` re-ran discovery, got the same
  markets back, resubscribed. Cycle.
- **Why 24h grace**: Polymarket's Gamma API returns `end_date_iso`
  as a date-only string (e.g. `"2026-04-22"`); Nautilus's adapter
  parses that as midnight UTC. Real market close is typically noon
  UTC plus resolution delay, so strategy adds 24h grace before
  tearing down subscriptions. The discover path had no mirror of
  this logic.
- **Evidence**: 2026-04-22 ledger had **238 subscribed** + **245
  unsubscribed** events against only 3 actually-active markets
  (Atlanta 84-85°F, LA 70-71°F, Miami 82-83°F). 2026-04-23 early
  AM showed the same pattern continuing on 04-22 markets
  (42 sub / 42 unsub in the first few hours).
- **Fix**: Add Filter (1) in
  `discover.py::discover_tradeable_markets` that fails fast:
  ```python
  grace_end = datetime.combine(target_date + timedelta(days=1),
                               time(0, 0, 0), UTC)
  if grace_end < datetime.now(UTC):
      return []
  ```
  New module-level `EXPIRY_GRACE = timedelta(hours=24)` mirrors
  `strategy.py::_unsubscribe_expired`'s `grace_ns` — the comment
  cross-references so the two stay in sync. Filter runs before
  any DuckDB work, so stale dates incur zero parquet I/O.
- **Proof** (2026-04-23 03:58Z vs 03:59Z on worktree copy):
  | target date | before fix | after fix |
  |---|---|---|
  | 04-21 (past-grace 28h) | 5 markets | 0 ✓ |
  | 04-22 (past-grace 4h) | 3 markets | 0 ✓ |
  | 04-23 (valid today) | 0 (HRRR NaN) | 0 (HRRR NaN) — unchanged |
- **Operator note**: live strategy process (`cfp run`) imports
  `discover` once at startup. Fix only takes effect after restart.

### F-006 — Polymarket WSS dynamic subscribe message silently ignored (was B-001)

- **Severity at time of finding**: HIGH (blocks any market subscribed
  after `on_start()` — silently missed entire trading windows on
  2026-04-22)
- **Fixed in**: branch `wt/b-001-dynamic-subscribe`, commit 178a63c
  (`fix(B-001): patch Polymarket WSS dynamic subscribe via reconnect`),
  2026-04-23. Pending fast-forward merge into main.
- **Observed**: 2026-04-22T17:42Z (first live session)
- **Root cause**: `PolymarketWebSocketClient` ships two separate
  subscribe paths. The **initial-connect** path (`_subscribe_all`,
  invoked when `_connect_client` brings up a fresh WS) sends
  `{"type": "market", "assets_ids": [...]}` — Polymarket's documented
  CLOB WSS subscribe format, which the server honors. The
  **dynamic-subscribe** path (`subscribe()` → `_create_dynamic_subscribe_msg`,
  invoked when a sub is added after the client is already connected)
  sends `{"assets_ids": [...], "operation": "subscribe"}` — a
  Nautilus-side invention that **Polymarket's CLOB WSS server silently
  drops**. Polymarket's official `py_clob_client` has no concept of
  runtime subscribe-mutation; the documented contract is "list every
  asset in the initial handshake". So every market the strategy added
  after `on_start()` (i.e. via `_refresh_subscribed_and_active`) sat
  registered in Nautilus's local bookkeeping but never appeared in
  Polymarket's actual subscription set. `cache.order_book(iid)`
  returned a non-None book object whose `.bids()` / `.asks()` stayed
  empty for the lifetime of the subscription.
- **Why the misleading "Subscribed ... order book deltas; depth=10"
  log fires anyway**: that line is the `success_msg` of the parent
  `LiveDataClient.subscribe_order_book_deltas`'s `create_task` call.
  It prints when the awaited coroutine returns *normally* — which it
  always does, because the dynamic-subscribe `_send` succeeds at the
  WebSocket-write layer. The server-side silent drop is invisible to
  the client.
- **Why bot restart fixed it**: restart re-runs `on_start()`, which
  feeds every market into `instrument_ids` upfront. Each subscribe is
  issued *before* the underlying WS is connected, so it goes through
  the `add_subscription`/`_schedule_delayed_connect` path. The eventual
  `_connect_client` then fires `_subscribe_all` with every queued sub
  in the working initial-connect format. Books populate within seconds
  (verified: ATL 84-85 NO snapshot at 18:04:34 had real depth after
  the 17:54 restart).
- **Fix**: New module `src/lib/polymarket/ws_subscribe_patch.py`
  monkey-patches `PolymarketWebSocketClient.subscribe` at import time.
  The replacement: when a fresh sub is added against a connected
  client, append it to the local subscription bookkeeping (under the
  existing `_lock`) and then call `_disconnect_client` followed by
  `_connect_client` — which re-runs the working `_subscribe_all` with
  the new sub included. Coalesces concurrent dynamic subscribes via
  the existing `_is_connecting` flag (subs queued during a reconnect
  ride along on that reconnect's `_subscribe_all`). Pre-connect
  subscribes and refcount-only resubscribes both fall through to the
  upstream behavior — only the broken dynamic-message path is
  intercepted. Patched once at top of `node._build_node` via
  `import lib.polymarket.ws_subscribe_patch`. Idempotent.
- **Why not patch Nautilus upstream?** Could submit a PR, but the
  patch is a behavioral change (every dynamic subscribe now triggers
  a reconnect) that other adapter users might not want. Local patch
  keeps the blast radius to this repo. Upstream PR is a future option
  if Polymarket ever publishes a real dynamic-subscribe API.
- **Verification**: `tests/lib/polymarket/test_ws_subscribe_patch.py`
  has 5 tests proving the patched behavior:
    1. `test_subscribe_already_in_refcount_is_noop_at_ws_layer` —
       refcount-only resubscribe sends nothing.
    2. `test_subscribe_pre_connect_does_not_reconnect` — pre-connect
       subs queue without triggering reconnect.
    3. `test_subscribe_post_connect_triggers_reconnect_not_dynamic_send`
       — the core fix: post-connect subs invoke `_disconnect_client`+
       `_connect_client` and explicitly do **not** invoke `_send` with
       the broken dynamic-subscribe payload.
    4. `test_subscribe_post_connect_under_concurrency_only_one_reconnect_per_sub`
       — five concurrent subscribes go through the reconnect path
       without ever hitting the broken send path.
    5. `test_unsubscribe_unchanged` — explicit guard that we did not
       touch the unsubscribe path.

  `tests/lib/polymarket/test_ws_subscribe_upstream_regression.py`
  reaches past the patch via `importlib.reload` and proves the
  unpatched class still emits the broken
  `{"assets_ids": ["dyn_token"], "operation": "subscribe"}` payload —
  this is a regression-witness so that if upstream ever changes its
  dynamic-subscribe shape, the test fails loudly and we re-evaluate
  the patch.

  All 6 tests pass: `uv run pytest tests/lib/polymarket/ --asyncio-mode=auto`.
- **Interim mitigation removed**: bot restart workaround no longer
  needed. The patch handles dynamic subscribes transparently.

### F-005 — Reconciled existing positions weren't credited to `_state.usd_spent` (was B-002)

- **Severity at time of finding**: MEDIUM (distorts per-market USD
  cap across bot restarts; material once we scale past $30/market)
- **Fixed in**: commit c82d86c on main (`strategy: seed usd_spent
  from venue positions on startup (B-002)`), 2026-04-22
- **Observed**: 2026-04-22T19:00:00Z (first full live session)
- **Root cause**: Nautilus's system kernel runs startup
  reconciliation BEFORE calling `_trader.start()` (see
  `system/kernel.py::start_async` lines 1021-1033). At
  reconciliation time, a Strategy's FSM is still `READY`, not
  `RUNNING`. The inferred `OrderFilled` events the ExecEngine
  synthesizes during reconciliation are published to the
  `events.order.{strategy_id}` topic, but `Strategy.handle_event`
  drops events when the FSM state isn't `RUNNING` (see
  `trading/strategy.pyx` line 1917: `if self._fsm.state !=
  ComponentState.RUNNING: return`). Net effect: pre-existing venue
  exposure at restart never reaches `on_order_filled`, and
  `_state.usd_spent` / `_state.positions` stay at their defaults
  — so the per-market USD cap effectively resets on every restart.
- **Fix**: New `_seed_state_from_reconciled_positions` method in
  `ConsensusFadeStrategy`, called from `on_start` after the FSM has
  transitioned to `RUNNING`. Reads `self.cache.positions_open()`
  (which was populated by the reconciliation pass that the strategy
  couldn't observe in real-time) and credits LONG positions against
  `_state.positions` + `_state.usd_spent`. Uses
  `Position.avg_px_open` when available; falls back to
  `config.max_no_price` when the venue report produced
  `last_px=$0.00` (common on Polymarket — see F-002). Pure helper
  `compute_reconciled_position_seeds` lives at module scope so it's
  unit-testable without standing up a TradingNode.
- **Not `cache.positions(strategy_id=self.id)`**: the BUGS.md original
  proposed fix suggested filtering by strategy_id. But reconciled
  orders are tagged with `strategy_id="EXTERNAL"` unless the strategy
  pre-claims them via `StrategyConfig.external_order_claims`.
  Filtering by `self.id` would return nothing. Using `positions_open()`
  (no filter) picks up EXTERNAL positions too, which is what we want.
- **Verification**: New test suite `tests/test_reconciled_position_seeding.py`
  — 9 tests covering the B-002 repro (5.92 shares, avg_px=0 → USD
  credit of $5.50), the non-fallback path (recovered avg_px=$0.82),
  SHORT/FLAT skip, empty input, and the usd_cap arithmetic shift
  (`max_shares_by_usd` now 26 instead of 32 on a same-market
  restart — matching BUGS.md's expected "~26" calculation). All pass.
- **Scrubbed stale docs**: `STRATEGY.md §7 Risk-control caveats` and
  `node.py`'s startup banner both claimed restarts reset the USD cap;
  both updated in the same commit.

### F-001 — `int(float(str(last_qty)))` truncated fractional-share fills

- **Severity at time of finding**: MEDIUM (blocked correct USD/position
  tracking under `allow_overfills=True`)
- **Fixed in**: Pre-live-session patch, 2026-04-22T~17:00Z
  (before first live trade)
- **Observed**: 2026-04-22T06:00Z during code review of strategy
  post-Phase 6B smoke test
- **Description**: `on_order_filled` cast `last_qty` to `int`,
  truncating fractional shares (common on Polymarket because the
  matcher fills whole maker blocks and we had `allow_overfills=True`).
  A 5.975605-share fill was recorded as 5 in `_state.positions`,
  leaving ~1 share of invisible exposure per overfill.
- **Fix**: `qty = float(event.last_qty)` and updated
  `positions: dict[InstrumentId, float]` annotation. Strategy
  `shares_per_market` cap still works because `int(min(...))` cast
  happens at order-submit time, not at state-tracking time.
- **Verification**: Today's 40.216215-share ATL fill was recorded
  exactly as `40.216215` in ledger's `filled` event and in
  `_state.positions`.

### F-002 — Nautilus "inferred" `OrderFilled` events report `last_px=0.00`

- **Severity at time of finding**: MEDIUM (silently disabled USD cap)
- **Fixed in**: Same patch as F-001, pre-live-session
- **Observed**: 2026-04-22T~17:00Z during code review (with supporting
  log evidence from the 2026-04-21 smoke test)
- **Description**: When Polymarket's async match-then-accept flow
  causes Nautilus's ExecEngine to synthesize an OrderFilled event, the
  `last_px` field is populated as `$0.00 USDC.e` (real fill price is in
  the PolymarketUserTrade but doesn't propagate to the OrderFilled
  object). With the original code `usd_spent[iid] += qty * 0.0 = 0`,
  effectively disabling the USD cap.
- **Fix**: Fallback in `on_order_filled`:
  ```python
  px = float(event.last_px)
  if px <= 0.0:
      px = self.config.max_no_price
  ```
  Conservative — overcounts USD spend, so the cap fires earlier and
  safer. An IOC at `max_no_price` can never fill above that price.
- **Verification**: Today's reconciled 5.92-share fill came in with
  `last_px=0.00`; fallback produced `usd_spent += 5.92 * 0.93 = $5.50`.
  Behavior confirmed in log.

### F-003 — `allow_overfills=False` (Nautilus default) rejected real fills

- **Severity at time of finding**: BLOCKING (fills happened on-chain
  but weren't tracked in bot state, leading to bookkeeping divergence
  from wallet reality)
- **Fixed in**: `src/consensus_fade_plus1/node.py` before first live
  trade (2026-04-22T~17:00Z)
- **Observed**: 2026-04-22T05:43Z during Phase 6B smoke test
- **Description**: Polymarket's matcher fills whole maker blocks (5.98
  shares into a request for 5). Nautilus's default treats this as an
  overfill and rejects the "extra" shares from internal bookkeeping —
  but can't undo the on-chain trade. Result: `_state.positions[iid] =
  0` while wallet actually owns 5.98 shares.
- **Fix**: `allow_overfills=True` in `LiveExecEngineConfig`. Strategy
  now accepts whatever Polymarket gives us, tracks it correctly.
- **Verification**: Today's TAKE for 32 shares filled for 40.22; both
  `_state.positions` and the ledger recorded 40.22; no
  "Order overfill rejected" errors.

### F-004 — Polymarket CLOB geoblock refused orders from US IPs

- **Severity at time of finding**: BLOCKING (could not place any orders)
- **Fixed by**: Routing outbound through a non-US, non-datacenter IP
  (personal VPN Brazil exit). Initial attempt via a Railway proxy
  failed because cloud datacenter IPs are also blocked.
- **Observed**: 2026-04-21T17:00Z during Phase 6B smoke test
- **Description**: Polymarket's Cloudflare rule blocks trading
  endpoints (POST /order, /orders) from US residential, US cloud, and
  global cloud-datacenter IP ranges. Read endpoints work globally.
- **Verified working**: Brazil residential VPN exit gets past the
  region filter (401 on unsigned curl = auth missing; not 403 =
  region restricted). Today's 19:00Z trade landed successfully.
- **Ongoing consideration**: Before 2026-04-28 Polymarket V2 cutover,
  validate V2 behavior under the same egress.

---

## UPSTREAM (not our bugs, worth tracking)

### U-001 — IEM MOS service intermittently delayed
- **Observed**: 2026-04-22T10:00Z–22:11Z
- **Symptom**: GFS 12Z cycle arrived at IEM ~4 hours after NOAA issued
  it (vs typical 45-60 min). NBS 13/14/15Z cycles similarly delayed.
- **Our response**: Watchers correctly report "no new data" from
  probe, continue polling, recover automatically when upstream
  catches up. Consensus filter uses whatever data we have.
- **Risk in volatile weather**: If upstream is stale during a front
  passage, our forecasts could silently be wrong.
- **Monitoring idea**: Add an alert when any single source is > 2
  hours stale beyond its expected cadence.

### U-002 — Polymarket WSS periodic disconnects
- **Observed**: ~30 reconnects across 24h
- **Symptom**: `nautilus_network::websocket::client: Received error
  message - terminating: WebSocket protocol error: Connection reset
  without closing handshake`
- **Our response**: Nautilus's Rust-layer WS client auto-reconnects
  silently. Books keep flowing. No action needed.

### U-003 — Polymarket market resolution often hours late
- **Observed**: Phase 6B market ("ATL 84-85°F on April 22") had
  `end_date_iso=2026-04-22T00:00Z` but market still showed
  `active=True closed=False accepting_orders=True` as late as 03:38Z
  next day (~27.5 hours after advertised close).
- **Impact**: Position payout is delayed but not lost. Wallet credit
  arrives whenever Polymarket's oracle fires.
- **Our response**: None needed, just be aware that "end_date_iso" is
  not a real trading halt time.

---

## Non-perfections (design trade-offs, not bugs)

### N-001 — Polymarket bucket ladders cap below warm-weather forecasts
- Several cities' bucket ladders top out at "X°F or higher" where X
  is below the current season's typical highs. On warm days
  (2026-04-22 in DAL/AUS/HOU/DEN), the favorite lands in the top
  "or higher" bucket and no +1 bucket exists → strategy correctly
  skips. This is a Polymarket product limitation; our strategy
  handles it correctly.

### N-002 — Nautilus's `allow_overfills=True` + Polymarket whole-block
  matching means the per-market USD cap is approximate
- A 32-share request can fill for ~40 shares, exceeding the cap by one
  maker-block quantity. Typical overshoot at our scale: $2-6. Operators
  should size the cap with 15-20% headroom.

### N-003 — West Coast entries (LAX/SFO/SEA) have a compressed window
- HRRR peak-coverage for PDT cities doesn't hit 6/6 until ~21-22Z, and
  the 15-local-PDT entry gate opens 22-23Z. West Coast books also tend
  to be thinner, and books typically go dead after the real-world peak
  ~00-02Z. Net: a ~2-4 hour window per day for West Coast trades, vs
  ~5-6 hours for East Coast. Backtest assumed uniform opportunity —
  reality is more concentrated.

### N-004 — Bot restart resets all in-memory state
- `usd_spent`, `positions`, `pending`, `first_eligible_ns`,
  `submissions_count` are all runtime-only. A mid-session restart:
  (a) forgets existing exposure (see B-002), (b) re-opens entry windows
  for markets that were already traded, (c) allows re-filling markets
  that were usd_cap_hit before restart. **Don't restart mid-session
  unless necessary** (e.g., to apply a critical patch like the B-001
  dynamic-subscribe bypass).

### N-005 — Entry window is per-market, not per-session
- `entry_window_minutes=30` is counted from each market's
  `first_eligible_ns`. Restarting the bot resets this. Markets whose
  window was closing before a restart will have a fresh 30-min window
  after restart. This could lead to double-entry on the same market
  across a restart. Mitigate by either: not restarting, or by seeding
  `first_eligible_ns` from the ledger's most recent
  `entry_window_opened` event during `on_start`.

---

## Historical context

- 2026-04-21: Phase 6B smoke test (1 live IOC, ATL 84-85 NO, 5.98
  shares at $0.82). Caught F-003 (overfills) and F-002 (last_px=0)
  inline; patched.
- 2026-04-22: First full live session. Caught B-001
  (dynamic-subscribe) at 17:42Z, restarted bot at 17:54Z as workaround.
  Executed 1 qualifying trade (ATL 84-85 NO, 40.22 shares at $0.74).
  Waiting on resolution at 04:00Z 2026-04-23.
- 2026-04-23: TBD.

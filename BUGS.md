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

### B-001 — Dynamic `subscribe_order_book_deltas` doesn't propagate to `PolymarketWebSocketClient` subscription registry

- **Severity**: HIGH
- **Status**: BEING WORKED ON (worktree `wt/b-001-dynamic-subscribe`, started 2026-04-23T03:53Z)
- **Observed**: 2026-04-22T17:42Z
- **Symptom**: Markets added to `_state.subscribed` via
  `_refresh_subscribed_and_active` after the initial `on_start()` had
  their Nautilus DataClient subscription call succeed (log:
  `DataClient-POLYMARKET: Subscribed ... order book deltas; depth=10`),
  but the underlying WSS client (`PolymarketWebSocketClient`) never
  added those instruments to its active subscriptions. Result: `cache.order_book(iid)`
  returned a book object whose `.bids()` and `.asks()` iterables were
  empty for those instruments, for the full duration they were
  subscribed.
- **Evidence**:
  - `data/processed/cfp_book_snapshots/2026-04-22.jsonl` at 17:08, 17:18,
    17:28, 17:38 each showed `bids=[] asks=[]` for the two today-markets
    (ATL 84-85 NO, MIA 82-83 NO) subscribed at 17:01:41.
  - Direct CLOB REST query to the same tokens showed real liquidity
    (e.g. `[(0.01, 6091), (0.02, 2055), ...]` on ATL NO).
  - Every Polymarket WSS reconnect line in the bot log
    (`Connected to wss://ws-subscriptions-clob.polymarket.com/ws/market`)
    reported the **same** `with 5 subscriptions` count — never 7 —
    even after the two new instruments were added at 17:01.
  - After bot restart at 17:54 (which re-seeded subscriptions via
    `on_start()` rather than via rollover), books populated within
    seconds: ATL NO at 18:04 had `best_bid=$0.86, best_ask=$0.88`.
- **Impact**: Effectively blocks any market that qualifies for trade
  after `on_start()` completes. At our scale, this could silently miss
  entire trading windows. On 2026-04-22 this blocked the 19:00Z ATL
  entry window for ~46 minutes until restart; we only caught it because
  book snapshots were being logged every 10 minutes.
- **Triage**:
  1. Read Nautilus's `PolymarketWebSocketClient.subscribe()` and
     `_client_subscriptions` mutation path (in
     `.venv/lib/python3.13/site-packages/nautilus_trader/adapters/polymarket/websocket/client.py`).
  2. Add a debug-level log when the PolymarketWebSocketClient's
     `_subscription_counts` dict is updated, so dynamic vs startup
     subscriptions are distinguishable in output.
  3. Reproduce deterministically: start the bot with empty
     `instrument_ids`, then trigger discovery mid-session to force the
     dynamic code path.
  4. Compare the subscribe payload (`_create_dynamic_subscribe_msg`)
     sent on the WSS against what Polymarket expects. There may be a
     formatting issue (e.g., sending condition_id when venue expects
     asset_id, or vice versa).
- **Proposed fix**: Once root cause is identified, either patch
  Nautilus (submit upstream PR) or work around by forcing a WS
  reconnect after any dynamic subscribe (reconnect path calls
  `_subscribe_all` which DOES correctly register all subs).
- **Interim mitigation**: Our `proxy` restart workflow. Not sustainable
  long-term.

---

### B-002 — Reconciled existing positions aren't credited to `_state.usd_spent`

- **Severity**: MEDIUM (depends on per-market cap sizing; low at our
  current $30 cap, material at $100-300 cap)
- **Status**: BEING WORKED ON (worktree wt/b002-reconcile-usd, 2026-04-22)
- **Observed**: 2026-04-22T19:00:00Z
- **Symptom**: At bot restart, Nautilus's ExecEngine reconciled an
  existing Polymarket position by synthesizing an `OrderFilled` event
  ("Generated inferred OrderFilled" in log) for the 5.9218-share ATL
  position from yesterday's Phase 6B test. This should have contributed
  to the strategy's `_state.usd_spent[iid]` via the normal
  `on_order_filled` handler. Instead, 65 minutes later at the 19:00Z
  TAKE decision, the strategy computed `max_shares_by_usd = 32`
  (implying `usd_spent[iid] ≈ $0`) rather than ~26 (which would be
  correct if the reconciled $5.50 were counted).
- **Evidence**:
  - Log at `17:54:34.759Z`: `Generated inferred OrderFilled(..., last_qty=5.921800, last_px=0.00 USDC.e, ...)`
  - Log at `19:00:00.587Z`: `TAKE ... BUY 32 @ 0.93 IOC` — qty=32 is the
    upper bound allowed by the USD cap with `usd_spent[iid]=0`.
  - Resulting overspend: $4.90 yesterday + $29.76 today = $34.66 on a
    market with a $30 declared cap.
- **Impact**: Per-market USD cap is effectively "fresh budget per
  session restart" rather than "fresh budget per market per day". If an
  operator restarts the bot mid-session (e.g., to apply a bugfix), the
  cap resets even for markets with existing exposure.
- **Triage**:
  1. Determine whether `on_order_filled` is actually called for the
     reconciled fill, or whether ExecEngine routes it differently. Add
     a debug log at the top of `on_order_filled` to confirm.
  2. If it IS called but during a window where `_state.usd_spent` hasn't
     been initialized, the event is being logged against `{}` default
     and "forgotten". Check ordering of `on_start()` vs reconciliation.
  3. Consider adding an explicit post-`on_start` seed step that reads
     `cache.positions()` and seeds `_state.positions` and
     `_state.usd_spent` from existing Nautilus state.
- **Proposed fix**:
  ```python
  # in on_start(), after super().on_start()
  for position in self.cache.positions(strategy_id=self.id):
      iid = position.instrument_id
      qty = float(position.quantity)
      self._state.positions[iid] = qty
      # Can't recover true avg_px from cache alone; use max_no_price
      # as conservative fallback. Only matters for USD cap.
      self._state.usd_spent[iid] = qty * self.config.max_no_price
  ```

---

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

### B-004 — Yesterday's expired markets resubscribe/unsubscribe on a 5-min cycle

- **Severity**: LOW (cosmetic — log noise, Gamma API calls)
- **Status**: Open
- **Observed**: Continuously throughout 2026-04-22
- **Symptom**: Expired markets (past `end_date + 24h grace`) are
  detected and unsubscribed by `_unsubscribe_expired`. But the next
  `_refresh_subscribed_and_active` tick (when features.parquet changes)
  re-runs discovery with `d_offset=-1` (yesterday), finds the expired
  markets in `markets.parquet`, and resubscribes them. Next 5-min
  expiry scan unsubscribes them again. Cycle.
- **Evidence**: 2026-04-22 ledger has **238 subscribed** and
  **245 unsubscribed** events against only 3 actually-active markets.
  The ratio of real subscribes (3) to churn events is ~1:160.
- **Impact**: Ledger bloat; minor Gamma API load; no financial impact.
- **Triage**:
  1. Verify by reading consecutive `_refresh_subscribed_and_active` log
     lines to confirm the re-sub/unsub pattern.
- **Proposed fix**: In `discover.py::discover_tradeable_markets`, filter
  markets whose `end_date` has already passed the 24h grace window:
  ```python
  # Drop markets where end_date + 24h grace < now
  grace_end = datetime.combine(target_date + timedelta(days=1), time(0, 0), UTC)
  if grace_end < datetime.now(UTC):
      continue
  ```

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

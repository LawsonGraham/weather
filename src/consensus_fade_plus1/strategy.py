"""Consensus-Fade +1 Offset — continuous polling strategy with live rollover.

Mental model: a `while True` loop that polls our data functions every
~0.5s. Each tick:

  1. Refresh state from data sources:
       - subscribed set: markets we're receiving book deltas for. Grows
         as discover finds new qualifying markets (auto-loaded + subscribed
         transparently via Nautilus). Shrinks when a market resolves.
       - active set: subset of subscribed that's tradeable RIGHT NOW
         (end_date == today AND consensus passes AND +1 bucket valid).
         This is the set the take logic fires on.
       - positions: per-market shares owned, updated locally on fills.
  2. For each active market: if room under cap AND asks in range, fire
     an IOC BUY sized to sweep them.
  3. Sleep tick_interval, repeat.

Continuous operation across UTC midnight:
  - The daemon refreshes markets.parquet every ~1h, picking up new days'
    markets ~4h after Polymarket lists them.
  - Every ~60s the tick re-runs discover for today AND the next
    `lookahead_days`. Any new instrument seen → subscribe_order_book_deltas
    (Nautilus's auto-load fetches the instrument from Gamma and wires up
    the WSS sub, with the instrument landing in the cache before any
    messages flow).
  - Every ~5min the tick checks `instrument.expiration_ns` on the
    subscribed set. Anything past expiry → unsubscribe.

No resting limit orders. Every buy is IOC — it crosses against existing
asks at <= max_no_price, takes what it can, cancels any unfilled
remainder. If nothing in-range is on the book right now, we wait for
it to show up and try again on the next tick.

Data flow:
  features.parquet + markets.parquet  -->  discover_tradeable_markets
                                              -->  subscribed / active
  Polymarket WSS                      -->  cache.order_book(iid)
                                              -->  takeable qty
  fills                               -->  self._state.positions
                                              -->  per-market cap
"""
from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
)
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from consensus_fade_plus1.persistence import BookSnapshotWriter, LedgerWriter

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_PATH = REPO_ROOT / "data" / "processed" / "backtest_v3" / "features.parquet"
MARKETS_PATH = REPO_ROOT / "data" / "processed" / "polymarket_weather" / "markets.parquet"

BOOK_SNAPSHOT_INTERVAL = timedelta(minutes=10)
BOOK_DEPTH = 10  # levels subscribed + persisted per side

# How often the tick runs the subscribe-sweep + expired-unsubscribe scans.
# Discovery is cheap (mtime-gated) so we can afford frequent checks, but we
# don't need sub-second cadence for something that changes daily.
EXPIRY_CHECK_INTERVAL_NS = 5 * 60 * 1_000_000_000  # 5 min

# Don't re-submit an IOC on the same instrument for this long after a reject.
# Prevents a tight TAKE→REJECT loop from spamming the exchange (common if
# the account is geoblocked, signature-invalid, or the venue is degraded).
REJECT_COOLDOWN_NS = 5 * 60 * 1_000_000_000  # 5 min

# If we accumulate this many consecutive rejects without any fill,
# flip the circuit breaker and stop submitting orders. Manual restart
# (cfp run again) resets it.
CIRCUIT_BREAKER_THRESHOLD = 20


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

class ConsensusFadeConfig(StrategyConfig, frozen=True):
    """Parameters for the continuous-polling strategy."""

    # Initial Nautilus InstrumentId strings to subscribe at on_start.
    # The tick loop handles rollover from here — new markets get subscribed
    # automatically as they appear; resolved markets get unsubscribed.
    instrument_ids: list[str]

    # Price ceiling for our IOC BUYs. Fair NO ~ 0.97 (backtest hit rate);
    # 0.92 implies ~5¢ of edge buffer.
    max_no_price: float = 0.92

    # Per-market position cap in shares. At 0.92 x 110 ≈ $100 risk per market.
    shares_per_market: int = 110

    # Minimum shares per IOC. Polymarket's per-market minimum is typically 5-15.
    min_order_shares: int = 5

    # Main loop tick cadence (seconds). Tighter = more reactive, also more
    # Python work. 0.5s is a comfortable balance.
    tick_interval_seconds: float = 0.5

    # How many days ahead to keep subscribed. 1 = today + tomorrow. Subscribing
    # ahead of time means when a market becomes today's tradeable, its book is
    # already warm. Nautilus auto-load handles the fetch transparently.
    lookahead_days: int = 1


# -----------------------------------------------------------------------------
# State — everything the strategy knows about the world
# -----------------------------------------------------------------------------

@dataclass
class StrategyState:
    """Mutable state refreshed by each tick."""

    # Instruments we're subscribed to book deltas for. Grows as new qualifying
    # markets appear; shrinks when a market resolves (expiration_ns passes).
    subscribed: set[InstrumentId] = field(default_factory=set)

    # Subset of `subscribed` currently tradeable (end_date == today AND
    # consensus passes). Take logic only fires on this set.
    active: dict[InstrumentId, object] = field(default_factory=dict)

    # Cached discovery inputs — skip re-discover when nothing changed.
    active_date: date | None = None
    active_features_mtime: float = 0.0
    active_markets_mtime: float = 0.0

    # Per-instrument position in shares (owned NO tokens). Updated on fills.
    positions: dict[InstrumentId, int] = field(default_factory=dict)

    # Per-instrument: client_order_id of an IOC currently in flight.
    # Prevents double-submission while one is still resolving.
    pending: dict[InstrumentId, ClientOrderId] = field(default_factory=dict)

    # Throttle control for the expired-unsubscribe scan.
    last_expiry_check_ns: int = 0

    # Per-instrument rejection cooldown. When an IOC on instrument X is
    # rejected (e.g., geoblock, signature reject, venue maintenance), we
    # skip X for REJECT_COOLDOWN_NS before trying again. Prevents a tight
    # rejection loop from hammering the exchange API.
    last_reject_ns: dict[InstrumentId, int] = field(default_factory=dict)

    # Global circuit breaker: if we've accumulated CIRCUIT_BREAKER_THRESHOLD
    # consecutive rejects (across all markets) without any fill, flip this
    # flag and refuse to submit more orders. Manual restart required to reset.
    total_rejects_streak: int = 0
    circuit_broken: bool = False

    # Loop control.
    running: bool = False
    tick_count: int = 0


# -----------------------------------------------------------------------------
# Strategy
# -----------------------------------------------------------------------------

class ConsensusFadeStrategy(Strategy):
    """Continuous polling loop with daily-market rollover + IOC takes."""

    def __init__(self, config: ConsensusFadeConfig) -> None:
        super().__init__(config)
        self._state = StrategyState()
        self._ledger: LedgerWriter | None = None
        self._snapshots: BookSnapshotWriter | None = None
        self._loop_task: asyncio.Task | None = None

    # --- Lifecycle --------------------------------------------------------

    def on_start(self) -> None:
        self._ledger = LedgerWriter()
        self._snapshots = BookSnapshotWriter()
        self._ledger.log(
            "session_start",
            instruments=list(self.config.instrument_ids),
            max_no_price=self.config.max_no_price,
            shares_per_market=self.config.shares_per_market,
            min_order_shares=self.config.min_order_shares,
            tick_interval_seconds=self.config.tick_interval_seconds,
            lookahead_days=self.config.lookahead_days,
        )
        self.log.info(
            f"ledger     → {self._ledger.dir_path}/YYYY-MM-DD.jsonl",
            color=LogColor.BLUE,
        )
        self.log.info(
            f"snapshots  → {self._snapshots.dir_path}/YYYY-MM-DD.jsonl",
            color=LogColor.BLUE,
        )

        # Seed subscriptions from the initial list. Nautilus auto-load covers
        # anything not yet in cache — any instrument fetched by
        # PolymarketInstrumentProviderConfig.load_ids is already cached; new
        # ones added later by the tick loop will be auto-loaded on subscribe.
        for iid_str in self.config.instrument_ids:
            iid = InstrumentId.from_str(iid_str)
            self.subscribe_order_book_deltas(
                iid, book_type=BookType.L2_MBP, depth=BOOK_DEPTH,
            )
            self._state.subscribed.add(iid)
        self.log.info(
            f"seeded {len(self._state.subscribed)} initial subscription(s); "
            f"rollover handles the rest",
            color=LogColor.BLUE,
        )

        # Kick off the continuous polling loop.
        self._state.running = True
        self._loop_task = asyncio.ensure_future(self._main_loop())

        # Schedule periodic book snapshots.
        self.clock.set_timer(
            name="book_snapshot",
            interval=BOOK_SNAPSHOT_INTERVAL,
            callback=self._on_snapshot_timer,
        )
        self.log.info(
            f"book-snapshot timer every {BOOK_SNAPSHOT_INTERVAL}",
            color=LogColor.BLUE,
        )

    def on_stop(self) -> None:
        self._state.running = False
        for order in self.cache.orders_open(strategy_id=self.id):
            self.cancel_order(order)
        if self._ledger is not None:
            self._ledger.log(
                "session_stop",
                tick_count=self._state.tick_count,
                subscribed_count=len(self._state.subscribed),
                positions={str(k): v for k, v in self._state.positions.items()},
            )
            self._ledger.close()
        if self._snapshots is not None:
            self._snapshots.close()

    # --- Main loop (literally `while True`) -------------------------------

    async def _main_loop(self) -> None:
        """Polling loop. Pulls state, decides whether to act, sleeps, repeats."""
        self.log.info(
            f"main loop started (tick every {self.config.tick_interval_seconds}s, "
            f"lookahead={self.config.lookahead_days} day(s))",
            color=LogColor.BLUE,
        )
        while self._state.running:
            self._state.tick_count += 1
            try:
                self._tick()
            except Exception as e:
                self.log.error(
                    f"tick #{self._state.tick_count} FAILED: {e!r}\n"
                    f"{traceback.format_exc()}",
                )
            await asyncio.sleep(self.config.tick_interval_seconds)
        self.log.info(
            f"main loop stopped after {self._state.tick_count} ticks",
            color=LogColor.BLUE,
        )

    def _tick(self) -> None:
        """One iteration of the loop: refresh state, act on each active market."""
        # (1) Sync the subscribed + active sets with the current data picture.
        #     Early-returns if nothing changed since last tick.
        self._refresh_subscribed_and_active()

        # (2) Unsubscribe resolved markets (throttled — runs every ~5min).
        self._unsubscribe_expired()

        # (3) For each currently-active market, consider taking liquidity.
        for iid in list(self._state.active.keys()):
            self._maybe_take(iid)

    # --- Data functions (cheap, called from _tick) ------------------------

    def _refresh_subscribed_and_active(self) -> None:
        """Re-run discover for today + lookahead days. Subscribe new instruments.
        Update the active set (today-only, consensus-passing).

        Early-returns unless one of these changed since the last refresh:
          - today's UTC date (handles the midnight rollover)
          - features.parquet mtime (new weather forecasts)
          - markets.parquet mtime (new markets listed)
        """
        today = datetime.now(UTC).date()
        features_mtime = (
            FEATURES_PATH.stat().st_mtime if FEATURES_PATH.exists() else 0.0
        )
        markets_mtime = (
            MARKETS_PATH.stat().st_mtime if MARKETS_PATH.exists() else 0.0
        )

        unchanged = (
            today == self._state.active_date
            and features_mtime <= self._state.active_features_mtime
            and markets_mtime <= self._state.active_markets_mtime
        )
        if unchanged:
            return

        # Lazy imports so this module is cheap to import from the CLI.
        from nautilus_trader.adapters.polymarket.common.symbol import (
            get_polymarket_instrument_id,
        )

        from consensus_fade_plus1.discover import discover_tradeable_markets

        # Discover markets for today + lookahead days. Today's qualifying
        # markets go into `active`; all qualifying markets become subscribed.
        new_active: dict[InstrumentId, object] = {}
        for d_offset in range(self.config.lookahead_days + 1):
            d = today + timedelta(days=d_offset)
            try:
                markets = discover_tradeable_markets(target_date=d, consensus_max=3.0)
            except FileNotFoundError:
                continue  # markets.parquet missing — daemon hasn't run yet
            for m in markets:
                iid = get_polymarket_instrument_id(m.condition_id, m.no_token_id)
                # Subscribe if new (Nautilus auto-load fetches + caches the
                # instrument from Gamma transparently before the WSS sub opens).
                if iid not in self._state.subscribed:
                    self.subscribe_order_book_deltas(
                        iid, book_type=BookType.L2_MBP, depth=BOOK_DEPTH,
                    )
                    self._state.subscribed.add(iid)
                    self._ledger.log(
                        "subscribed",
                        instrument_id=str(iid),
                        city=m.city,
                        market_date=str(m.market_date),
                        bucket=m.bucket_title,
                    )
                    self.log.info(
                        f"rollover+  subscribed {m.city} {m.market_date} "
                        f"({m.bucket_title})  [{iid}]",
                        color=LogColor.GREEN,
                    )
                # Mark today's qualifiers as active; lookahead days are just
                # subscribed (we warm the book but don't trade them yet).
                if d == today:
                    new_active[iid] = m

        # Log active-set diffs
        added = set(new_active) - set(self._state.active)
        removed = set(self._state.active) - set(new_active)
        for iid in added:
            m = new_active[iid]
            self.log.info(
                f"active+  {m.city} {m.market_date} ({m.bucket_title})",
                color=LogColor.GREEN,
            )
            self._ledger.log(
                "active_added",
                instrument_id=str(iid),
                city=m.city,
                market_date=str(m.market_date),
            )
        for iid in removed:
            self.log.info(f"active-  {iid}", color=LogColor.YELLOW)
            self._ledger.log("active_removed", instrument_id=str(iid))

        self._state.active = new_active
        self._state.active_date = today
        self._state.active_features_mtime = features_mtime
        self._state.active_markets_mtime = markets_mtime

    def _unsubscribe_expired(self) -> None:
        """Scan subscribed instruments; unsubscribe any past their expiration.

        Throttled to every EXPIRY_CHECK_INTERVAL_NS (5 min).

        Grace period: Polymarket's Gamma API returns `end_date_iso` as a
        date string ("2026-04-20") which Nautilus's adapter parses as
        midnight UTC. But the actual market end_date is typically noon UTC
        that day (or later), and resolution can happen hours after. To
        avoid prematurely unsubscribing from markets still in their final
        trading window, we apply a 24h grace before considering expired.
        """
        now_ns = self.clock.timestamp_ns()
        if now_ns - self._state.last_expiry_check_ns < EXPIRY_CHECK_INTERVAL_NS:
            return
        self._state.last_expiry_check_ns = now_ns

        # 24h grace — see docstring
        grace_ns = 24 * 60 * 60 * 1_000_000_000

        for iid in list(self._state.subscribed):
            inst = self.cache.instrument(iid)
            if inst is None:
                continue
            if inst.expiration_ns and inst.expiration_ns + grace_ns < now_ns:
                self.unsubscribe_order_book_deltas(iid)
                self._state.subscribed.discard(iid)
                self._state.active.pop(iid, None)
                self._ledger.log(
                    "unsubscribed",
                    instrument_id=str(iid),
                    reason="expired",
                    expiration_ns=int(inst.expiration_ns),
                )
                self.log.info(
                    f"rollover-  unsubscribed {iid} (expired)",
                    color=LogColor.YELLOW,
                )

    def _takeable_shares(self, iid: InstrumentId) -> float:
        """Sum of ask qty at prices ≤ max_no_price. 0 if book empty or no qualifying asks."""
        book = self.cache.order_book(iid)
        if book is None:
            return 0.0
        total = 0.0
        for lvl in book.asks():
            if float(lvl.price) > self.config.max_no_price:
                break  # asks sorted ascending — rest are too expensive
            total += float(lvl.size())
        return total

    # --- Action: submit an IOC when opportunity + room exist --------------

    def _maybe_take(self, iid: InstrumentId) -> None:
        """If there's takeable liquidity and room under the cap, submit IOC BUY."""
        if self._state.circuit_broken:
            return  # global circuit breaker tripped
        if iid in self._state.pending:
            return  # an IOC is still resolving on this market
        # Per-instrument cooldown after a rejection
        last_reject = self._state.last_reject_ns.get(iid, 0)
        if last_reject and self.clock.timestamp_ns() - last_reject < REJECT_COOLDOWN_NS:
            return
        pos = self._state.positions.get(iid, 0)
        room = self.config.shares_per_market - pos
        if room < self.config.min_order_shares:
            return  # per-market cap hit (or close to it)
        takeable = self._takeable_shares(iid)
        if takeable < self.config.min_order_shares:
            return  # nothing in-range on the book right now

        qty = int(min(takeable, room))
        self._submit_ioc_buy(iid, qty)

    def _submit_ioc_buy(self, iid: InstrumentId, qty: int) -> None:
        """Submit a limit BUY at max_no_price with IOC time-in-force.

        IOC = immediate-or-cancel: fills what it can right now at our price
        or better, cancels the rest. Never rests on the book.
        """
        instrument = self.cache.instrument(iid)
        if instrument is None:
            return
        price = self._snap_to_tick(self.config.max_no_price, instrument)
        order = self.order_factory.limit(
            instrument_id=iid,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(qty),
            price=price,
            time_in_force=TimeInForce.IOC,
        )
        # Track before submit so the next tick won't race another IOC on the
        # same instrument. Cleared when we see the order go terminal.
        self._state.pending[iid] = order.client_order_id
        self.submit_order(order)
        self.log.info(
            f"TAKE  {iid}  BUY {qty} @ {price} IOC",
            color=LogColor.CYAN,
        )

    # --- Event hooks → ledger + state updates -----------------------------

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        self._ledger.log(
            "submitted",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
        )

    def on_order_accepted(self, event: OrderAccepted) -> None:
        self._ledger.log(
            "accepted",
            client_order_id=str(event.client_order_id),
            venue_order_id=str(event.venue_order_id),
            instrument_id=str(event.instrument_id),
        )

    def on_order_filled(self, event: OrderFilled) -> None:
        # Any fill breaks the reject streak (exchange is clearly accepting orders).
        self._state.total_rejects_streak = 0
        qty = int(float(str(event.last_qty)))
        iid = event.instrument_id
        delta = qty if event.order_side == OrderSide.BUY else -qty
        self._state.positions[iid] = self._state.positions.get(iid, 0) + delta
        self._ledger.log(
            "filled",
            client_order_id=str(event.client_order_id),
            instrument_id=str(iid),
            side=str(event.order_side),
            last_qty=str(event.last_qty),
            last_px=str(event.last_px),
            commission=str(event.commission),
            position_after=self._state.positions[iid],
        )
        self.log.info(
            f"FILL  {iid}  {event.last_qty} @ {event.last_px}  "
            f"fee={event.commission}  pos={self._state.positions[iid]}",
            color=LogColor.GREEN,
        )
        # IOC terminal state: either fully filled (FILLED) or partial-fill+cancel.
        # Clear pending when the order goes closed.
        order = self.cache.order(event.client_order_id)
        if order is not None and order.is_closed:
            self._state.pending.pop(iid, None)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._state.pending.pop(event.instrument_id, None)
        self._ledger.log(
            "canceled",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._state.pending.pop(event.instrument_id, None)
        # Cooldown on this instrument + global circuit breaker
        self._state.last_reject_ns[event.instrument_id] = self.clock.timestamp_ns()
        self._state.total_rejects_streak += 1
        tripped = (
            self._state.total_rejects_streak >= CIRCUIT_BREAKER_THRESHOLD
            and not self._state.circuit_broken
        )
        if tripped:
            self._state.circuit_broken = True
            self._ledger.log(
                "circuit_broken",
                consecutive_rejects=self._state.total_rejects_streak,
                threshold=CIRCUIT_BREAKER_THRESHOLD,
            )
            self.log.error(
                f"CIRCUIT BREAKER TRIPPED after "
                f"{self._state.total_rejects_streak} consecutive rejects — "
                f"no more orders will be submitted this session. Restart to reset.",
            )
        self._ledger.log(
            "rejected",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
            reason=str(event.reason),
        )
        self.log.error(f"REJECTED  {event.instrument_id}  reason={event.reason}")

    # --- Book snapshot timer (unchanged) ----------------------------------

    def _on_snapshot_timer(self, event: TimeEvent) -> None:
        """Every BOOK_SNAPSHOT_INTERVAL, write top-N L2 snapshot of each subscribed book."""
        written = 0
        for iid in self._state.subscribed:
            book = self.cache.order_book(iid)
            if book is None:
                continue
            bids = [(float(lvl.price), float(lvl.size()))
                    for lvl in book.bids()[:BOOK_DEPTH]]
            asks = [(float(lvl.price), float(lvl.size()))
                    for lvl in book.asks()[:BOOK_DEPTH]]
            self._snapshots.snapshot(str(iid), bids, asks)
            written += 1
        self.log.info(
            f"book snapshot: wrote {written} instrument(s) to {self._snapshots.path}",
            color=LogColor.BLUE,
        )

    # --- Helpers ----------------------------------------------------------

    def _snap_to_tick(self, price_float: float, instrument) -> Price:
        """Round a float price down onto the market's tick grid."""
        tick = instrument.price_increment
        ticks = int(price_float / float(tick))
        return Price(ticks * float(tick), precision=tick.precision)

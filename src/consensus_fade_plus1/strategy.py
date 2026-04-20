"""Consensus-Fade +1 Offset — continuous polling strategy.

Mental model: a `while True` loop that polls our data functions every
~0.5s. Each tick:

  1. Refresh state from data sources:
       - active_markets: re-read features.parquet (if changed), keep
         only instruments where the 3 forecasts align on a +1 bucket.
       - book state: Nautilus's cache maintains live L2 books via WSS.
       - positions: maintained locally from OrderFilled events.
  2. For each active market:
       - Skip if an IOC is already in flight
       - Skip if we've hit the per-market cap
       - Read the book: sum ask qty at prices <= max_no_price
       - If anything takeable: submit an IOC BUY sized to sweep it
  3. Sleep tick_interval, repeat.

No resting limit orders. Every buy is IOC — it crosses against existing
asks at <= max_no_price, takes what it can, cancels any unfilled
remainder. If nothing in-range is on the book right now, we wait for
it to show up and try again on the next tick.

The strategy picks up new forecast data automatically: when a watcher
rebuilds features.parquet, the mtime changes and the next tick re-runs
discovery. If consensus widens on a market, it drops out of active and
we stop acting on it. If it tightens back, it returns.

Data flow:
  features.parquet -->  discover_tradeable_markets()  -->  active set
  Polymarket WSS   -->  Nautilus cache.order_book()   -->  takeable qty
  fills            -->  self._state.positions          -->  per-market cap
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
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

BOOK_SNAPSHOT_INTERVAL = timedelta(minutes=10)
BOOK_DEPTH = 10  # levels subscribed + persisted per side


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

class ConsensusFadeConfig(StrategyConfig, frozen=True):
    """Parameters for the continuous-polling strategy."""

    # Nautilus InstrumentId strings we subscribe to at startup.
    # The live set of ACTIVE markets (those currently passing consensus) is
    # recomputed on every tick as features.parquet changes.
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


# -----------------------------------------------------------------------------
# State — everything the strategy knows about the world
# -----------------------------------------------------------------------------

@dataclass
class StrategyState:
    """Mutable state refreshed by each tick."""

    # Currently tradeable markets (forecasts align + valid +1 bucket exists).
    # Keyed by InstrumentId; value is the TradeableMarket snapshot.
    active: dict[InstrumentId, object] = field(default_factory=dict)

    # mtime of features.parquet used when we last computed `active`. Used
    # to early-return refresh work when features haven't changed.
    active_mtime: float = 0.0

    # Per-instrument position in shares (owned NO tokens). Updated on fills.
    positions: dict[InstrumentId, int] = field(default_factory=dict)

    # Per-instrument: client_order_id of an IOC currently in flight.
    # Prevents double-submission while one is still resolving.
    pending: dict[InstrumentId, ClientOrderId] = field(default_factory=dict)

    # Instruments we've subscribed to book deltas for (from config.instrument_ids).
    # We never unsubscribe within a session.
    subscribed: set[InstrumentId] = field(default_factory=set)

    # Loop control.
    running: bool = False
    tick_count: int = 0


# -----------------------------------------------------------------------------
# Strategy
# -----------------------------------------------------------------------------

class ConsensusFadeStrategy(Strategy):
    """Continuous polling loop that takes asks ≤ max_no_price via IOC."""

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
        )
        self.log.info(
            f"ledger     → {self._ledger.dir_path}/YYYY-MM-DD.jsonl",
            color=LogColor.BLUE,
        )
        self.log.info(
            f"snapshots  → {self._snapshots.dir_path}/YYYY-MM-DD.jsonl",
            color=LogColor.BLUE,
        )

        # Subscribe to book deltas for every instrument in config.instrument_ids.
        # These are the only markets we'll ever consider this session.
        for iid_str in self.config.instrument_ids:
            iid = InstrumentId.from_str(iid_str)
            if self.cache.instrument(iid) is None:
                self.log.error(f"instrument not in cache: {iid}")
                continue
            self.subscribe_order_book_deltas(
                iid, book_type=BookType.L2_MBP, depth=BOOK_DEPTH,
            )
            self._state.subscribed.add(iid)
        self.log.info(
            f"subscribed to {len(self._state.subscribed)} instrument(s)",
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
        # Cancel any in-flight orders (IOCs should already be terminal, but
        # this is idempotent).
        for order in self.cache.orders_open(strategy_id=self.id):
            self.cancel_order(order)
        if self._ledger is not None:
            self._ledger.log("session_stop", tick_count=self._state.tick_count,
                             positions={str(k): v for k, v in self._state.positions.items()})
            self._ledger.close()
        if self._snapshots is not None:
            self._snapshots.close()

    # --- Main loop (literally `while True`) -------------------------------

    async def _main_loop(self) -> None:
        """Polling loop. Pulls state, decides whether to act, sleeps, repeats."""
        self.log.info(
            f"main loop started (tick every {self.config.tick_interval_seconds}s)",
            color=LogColor.BLUE,
        )
        while self._state.running:
            self._state.tick_count += 1
            try:
                self._tick()
            except Exception as e:
                import traceback
                self.log.error(
                    f"tick #{self._state.tick_count} FAILED: {e!r}\n"
                    f"{traceback.format_exc()}"
                )
            await asyncio.sleep(self.config.tick_interval_seconds)
        self.log.info(
            f"main loop stopped after {self._state.tick_count} ticks",
            color=LogColor.BLUE,
        )

    def _tick(self) -> None:
        """One iteration of the loop: refresh state, act on each active market."""
        # (1) Pull fresh state from our data functions. Each early-returns
        #     if nothing's changed since last tick.
        self._refresh_active_markets()

        # (2) For each active market, consider taking liquidity.
        for iid in list(self._state.active.keys()):
            self._maybe_take(iid)

    # --- Data functions (cheap, called from _tick) ------------------------

    def _refresh_active_markets(self) -> None:
        """Re-read features.parquet if its mtime changed; update active set.

        Filters currently-qualifying markets (from discover) down to those
        we already subscribed to at startup. Markets that dropped out of
        consensus leave the active set; markets that re-qualified return.
        """
        if not FEATURES_PATH.exists():
            return
        mtime = FEATURES_PATH.stat().st_mtime
        if mtime <= self._state.active_mtime:
            return  # unchanged since last check — skip the re-discovery work

        # Lazy imports so this module is cheap to import from the CLI.
        from nautilus_trader.adapters.polymarket.common.symbol import (
            get_polymarket_instrument_id,
        )

        from consensus_fade_plus1.discover import discover_tradeable_markets

        markets = discover_tradeable_markets(consensus_max=3.0)
        new_active: dict[InstrumentId, object] = {}
        for m in markets:
            iid = get_polymarket_instrument_id(m.condition_id, m.no_token_id)
            if iid in self._state.subscribed:
                new_active[iid] = m

        added = set(new_active) - set(self._state.active)
        removed = set(self._state.active) - set(new_active)
        for iid in added:
            self.log.info(f"active+  {iid}", color=LogColor.GREEN)
            self._ledger.log("active_added", instrument_id=str(iid))
        for iid in removed:
            self.log.info(f"active-  {iid}", color=LogColor.YELLOW)
            self._ledger.log("active_removed", instrument_id=str(iid))

        self._state.active = new_active
        self._state.active_mtime = mtime

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
        # Skip if an IOC for this market is still resolving.
        if iid in self._state.pending:
            return
        # Skip if we've already hit the per-market cap (or can't meet minimum).
        pos = self._state.positions.get(iid, 0)
        room = self.config.shares_per_market - pos
        if room < self.config.min_order_shares:
            return
        # Skip if no qualifying asks are currently on the book.
        takeable = self._takeable_shares(iid)
        if takeable < self.config.min_order_shares:
            return

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
        # IOC terminal state might be either fully-filled or partial-fill+cancel.
        # Check if the order is now closed and clear pending if so.
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

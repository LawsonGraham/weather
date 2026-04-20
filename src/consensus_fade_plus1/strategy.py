"""Consensus-Fade +1 Offset — Nautilus Strategy.

Read top to bottom. Four responsibilities:

  1. on_start:
       - open the ledger + snapshot writers
       - for each tradeable market:
           * subscribe to L2 book deltas (so Nautilus's cache maintains
             the live book, which we snapshot periodically)
           * place one resting NO-buy limit at max_no_price for the full
             per-market budget
       - schedule a 10-min timer that writes book snapshots to disk

  2. on_order_* events:
       - every submitted / accepted / filled / canceled / rejected event
         is appended as one JSONL line to the ledger

  3. on book-snapshot timer fire:
       - read each subscribed instrument's order book from the cache,
         write top-N bids + asks to disk

  4. on_stop:
       - cancel any unfilled orders
       - close the writers

Why this works as a "range order":
  A limit BUY at $0.92 auto-fills against every ask ≤ $0.92 as retail
  arrives. Polymarket's matching engine handles it — we don't watch or
  reprice. The book subscription is for VISIBILITY (snapshots for
  post-hoc analysis), not for live matching logic.
"""
from __future__ import annotations

from datetime import timedelta

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
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from consensus_fade_plus1.persistence import BookSnapshotWriter, LedgerWriter

BOOK_SNAPSHOT_INTERVAL = timedelta(minutes=10)
BOOK_SNAPSHOT_DEPTH = 10          # top N levels per side we persist
BOOK_SUBSCRIPTION_DEPTH = 10      # depth requested from Polymarket WSS


class ConsensusFadeConfig(StrategyConfig, frozen=True):
    """Parameters for the strategy."""

    # Nautilus InstrumentId strings for the NO tokens we've discovered.
    # Populated by node.py from discover.discover_tradeable_markets().
    instrument_ids: list[str]

    # Max NO price we're willing to pay. Fair NO ≈ 0.97 (hit rate from
    # backtest); 0.92 implies ~5¢ of edge buffer. Above this we don't quote.
    max_no_price: float = 0.92

    # Total shares to buy per market (caps our per-market position).
    # At max_no_price=0.92 x 110 shares ≈ $100 per market.
    shares_per_market: int = 110


class ConsensusFadeStrategy(Strategy):
    """Places one resting NO-buy per tradeable market + records everything."""

    def __init__(self, config: ConsensusFadeConfig) -> None:
        super().__init__(config)
        self._ledger: LedgerWriter | None = None
        self._snapshots: BookSnapshotWriter | None = None

    # --- Lifecycle --------------------------------------------------------

    def on_start(self) -> None:
        self._ledger = LedgerWriter()
        self._snapshots = BookSnapshotWriter()
        self._ledger.log("session_start",
                         instruments=list(self.config.instrument_ids),
                         max_no_price=self.config.max_no_price,
                         shares_per_market=self.config.shares_per_market)
        self.log.info(
            f"ledger → {self._ledger.dir_path}/YYYY-MM-DD.jsonl  |  "
            f"book_snapshots → {self._snapshots.dir_path}/YYYY-MM-DD.jsonl",
            color=LogColor.BLUE,
        )

        for iid_str in self.config.instrument_ids:
            instrument_id = InstrumentId.from_str(iid_str)
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Instrument not in cache: {iid_str}")
                continue

            # Subscribe so Nautilus's cache maintains live L2 book state.
            self.subscribe_order_book_deltas(
                instrument_id,
                book_type=BookType.L2_MBP,
                depth=BOOK_SUBSCRIPTION_DEPTH,
            )

            # Place the single resting NO-buy limit.
            price = self._snap_to_tick(self.config.max_no_price, instrument)
            qty = Quantity.from_int(self.config.shares_per_market)
            order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.BUY,
                quantity=qty,
                price=price,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self.log.info(
                f"QUOTING  {instrument_id}  BUY {qty} @ {price}  "
                f"(fills against any ask ≤ {price})",
                color=LogColor.CYAN,
            )

        self.clock.set_timer(
            name="book_snapshot",
            interval=BOOK_SNAPSHOT_INTERVAL,
            callback=self._on_snapshot_timer,
        )
        self.log.info(
            f"book-snapshot timer scheduled every {BOOK_SNAPSHOT_INTERVAL}",
            color=LogColor.BLUE,
        )

    def on_stop(self) -> None:
        for order in self.cache.orders_open(strategy_id=self.id):
            self.cancel_order(order)
        if self._ledger is not None:
            self._ledger.log("session_stop")
            self._ledger.close()
        if self._snapshots is not None:
            self._snapshots.close()

    # --- Order event hooks → ledger ---------------------------------------

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
        self._ledger.log(
            "filled",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
            side=str(event.order_side),
            last_qty=str(event.last_qty),
            last_px=str(event.last_px),
            commission=str(event.commission),
        )
        self.log.info(
            f"FILL  {event.instrument_id}  {event.last_qty} @ {event.last_px}  "
            f"fee={event.commission}",
            color=LogColor.GREEN,
        )

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._ledger.log(
            "canceled",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._ledger.log(
            "rejected",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
            reason=str(event.reason),
        )
        self.log.error(f"REJECTED  {event.instrument_id}  reason={event.reason}")

    # --- Timer: periodic book snapshots -----------------------------------

    def _on_snapshot_timer(self, event: TimeEvent) -> None:
        """Called every BOOK_SNAPSHOT_INTERVAL. Snapshot each subscribed book."""
        written = 0
        for iid_str in self.config.instrument_ids:
            instrument_id = InstrumentId.from_str(iid_str)
            book = self.cache.order_book(instrument_id)
            if book is None:
                continue
            bids = [(float(lvl.price), float(lvl.size()))
                    for lvl in book.bids()[:BOOK_SNAPSHOT_DEPTH]]
            asks = [(float(lvl.price), float(lvl.size()))
                    for lvl in book.asks()[:BOOK_SNAPSHOT_DEPTH]]
            self._snapshots.snapshot(str(instrument_id), bids, asks)
            written += 1
        self.log.info(
            f"book snapshot: wrote {written} instrument(s) to "
            f"{self._snapshots.path}",
            color=LogColor.BLUE,
        )

    # --- Helpers ----------------------------------------------------------

    def _snap_to_tick(self, price_float: float, instrument) -> Price:
        """Round a float price down onto the market's tick grid."""
        tick = instrument.price_increment
        ticks = int(price_float / float(tick))
        return Price(ticks * float(tick), precision=tick.precision)

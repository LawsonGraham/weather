"""Consensus-Fade +1 Offset — Nautilus Strategy.

Read this file top to bottom. The logic is dead simple:

  1. On start: for each market where the 3 forecasts agree, place a SINGLE
     resting limit BUY at our max acceptable NO price, for our full
     per-market budget. Polymarket's matching engine takes care of the rest.

  2. As new retail YES-bids appear (equivalent to new NO asks ≤ our price),
     the CLOB automatically matches them against our resting order. We fill
     at their price (always ≤ our limit), they at ours. Polymarket maker
     fee = 0, so every fill is clean edge.

  3. On fill: log it. The resting order keeps going until its full quantity
     is consumed (= we hit our per-market position cap) or we stop the node.

  4. On stop: cancel whatever didn't fill.

Why this works:
  A limit BUY at $0.92 IS the "range order" the user asked for — it matches
  against every ask price in [0.01, 0.92] as new liquidity arrives. We never
  need to re-price, re-submit, or watch the book. Set it and forget it.

  The "streaming data" matters for DISCOVERING which markets qualify, not
  for matching orders. Discovery happens in discover.py; matching happens
  inside Polymarket.
"""
from __future__ import annotations

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy


class ConsensusFadeConfig(StrategyConfig, frozen=True):
    """Parameters for the strategy."""

    # Nautilus InstrumentId strings for the NO tokens we've discovered.
    # Populated by node.py from discover.discover_tradeable_markets().
    instrument_ids: list[str]

    # Max NO price we're willing to pay. Fair NO = 0.97 (hit rate from
    # backtest); 0.92 implies ~5¢ of edge buffer. Above this we don't quote.
    max_no_price: float = 0.92

    # Total shares to buy per market (caps our per-market position).
    # At max_no_price=0.92 × 110 shares ≈ $100 per market.
    shares_per_market: int = 110


class ConsensusFadeStrategy(Strategy):
    """Places one resting NO-buy per tradeable market. That's it."""

    def __init__(self, config: ConsensusFadeConfig) -> None:
        super().__init__(config)

    # --- Lifecycle --------------------------------------------------------

    def on_start(self) -> None:
        """Place one limit BUY per market at our max NO price."""
        for iid_str in self.config.instrument_ids:
            instrument_id = InstrumentId.from_str(iid_str)
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Instrument not in cache: {iid_str}")
                continue

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
                f"QUOTING  {instrument_id}  BUY {qty} @ {price} "
                f"(fills against any ask ≤ {price})",
                color=LogColor.CYAN,
            )

    def on_stop(self) -> None:
        """Cancel every open order this strategy owns."""
        for order in self.cache.orders_open(strategy_id=self.id):
            self.cancel_order(order)

    # --- Events -----------------------------------------------------------

    def on_order_filled(self, event: OrderFilled) -> None:
        """Got a fill. Just log it — the remainder stays resting automatically."""
        self.log.info(
            f"FILL  {event.instrument_id}  {event.last_qty} @ {event.last_px}  "
            f"fee={event.commission}",
            color=LogColor.GREEN,
        )

    # --- Helpers ----------------------------------------------------------

    def _snap_to_tick(self, price_float: float, instrument) -> Price:
        """Round a float price down onto the market's tick grid."""
        tick = instrument.price_increment
        ticks = int(price_float / float(tick))
        return Price(ticks * float(tick), precision=tick.precision)

"""Nautilus TradingNode builder — the live entry point.

Flow:
  1. Discover today's tradeable markets (consensus ≤ 3°F, +1 bucket exists)
  2. Build Nautilus config — data + exec clients configured for Polymarket,
     instrument provider constrained to just our markets
  3. Wire in our ConsensusFadeStrategy with those instruments
  4. Run until Ctrl+C, then cleanly dispose

Env vars required (populated by `cfp setup`):
  POLYMARKET_PK           Polygon EOA private key
  POLYMARKET_API_KEY      L2 API key
  POLYMARKET_API_SECRET   L2 API secret
  POLYMARKET_PASSPHRASE   L2 passphrase
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from consensus_fade_plus1.discover import (
    TradeableMarket,
    discover_tradeable_markets,
    print_discovery_summary,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_env() -> None:
    """Load .env and verify required vars are present."""
    load_dotenv(REPO_ROOT / ".env", override=True)
    required = [
        "POLYMARKET_PK",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_PASSPHRASE",
    ]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"Missing env vars: {', '.join(missing)}. "
            f"Run `uv run cfp setup` to populate .env."
        )


def _build_node(markets: list[TradeableMarket], *,
                max_no_price: float,
                shares_per_market: int):
    """Build a Nautilus TradingNode wired for today's markets.

    Lazy-imports heavy Nautilus symbols so module is cheap for CLI help.
    """
    from nautilus_trader.adapters.polymarket import (
        POLYMARKET,
        PolymarketDataClientConfig,
        PolymarketExecClientConfig,
        PolymarketLiveDataClientFactory,
        PolymarketLiveExecClientFactory,
    )
    from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_instrument_id
    from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProviderConfig
    from nautilus_trader.config import LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.identifiers import TraderId

    from consensus_fade_plus1.strategy import (
        ConsensusFadeConfig,
        ConsensusFadeStrategy,
    )

    # Nautilus instrument IDs for the NO tokens
    instrument_ids = [
        str(get_polymarket_instrument_id(m.condition_id, m.no_token_id))
        for m in markets
    ]
    instrument_provider_config = PolymarketInstrumentProviderConfig(
        load_ids=frozenset(instrument_ids),
    )

    node_config = TradingNodeConfig(
        trader_id=TraderId("CFP-001"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            open_check_interval_secs=10.0,
            open_check_open_only=True,
            graceful_shutdown_on_exception=True,
        ),
        data_clients={
            POLYMARKET: PolymarketDataClientConfig(
                instrument_config=instrument_provider_config,
            ),
        },
        exec_clients={
            POLYMARKET: PolymarketExecClientConfig(
                instrument_config=instrument_provider_config,
                generate_order_history_from_trades=False,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=15.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    strategy_config = ConsensusFadeConfig(
        instrument_ids=instrument_ids,
        max_no_price=max_no_price,
        shares_per_market=shares_per_market,
    )
    strategy = ConsensusFadeStrategy(config=strategy_config)

    node = TradingNode(config=node_config)
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(POLYMARKET, PolymarketLiveDataClientFactory)
    node.add_exec_client_factory(POLYMARKET, PolymarketLiveExecClientFactory)
    node.build()
    return node


def run(*, max_no_price: float = 0.92,
        shares_per_market: int = 110) -> int:
    """Discover markets, start the node, trade until Ctrl+C."""
    _ensure_env()

    markets = discover_tradeable_markets()
    print_discovery_summary(markets)
    if not markets:
        print("[node] no tradeable markets today — exiting.")
        return 0

    print(f"[node] starting TradingNode for {len(markets)} market(s); "
          f"max_no_price={max_no_price}, {shares_per_market} shares/market")

    node = _build_node(markets,
                       max_no_price=max_no_price,
                       shares_per_market=shares_per_market)
    try:
        node.run()
    finally:
        node.dispose()
    return 0

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
from datetime import UTC, datetime, timedelta
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
                shares_per_market: int,
                lookahead_days: int = 1,
                max_submissions: int | None = None,
                min_entry_hour_local: int = 16,
                max_yes_ask: float = 0.22,
                entry_window_minutes: int = 30,
                max_usd_per_market: float = 30.0,
                max_ask_walk: float = 0.04):
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
            # Polymarket's matcher fills whole maker blocks rather than
            # splitting them, so an IOC for qty=5 can match against a 5.9756
            # block and we end up with 0.9756 more shares than requested.
            # Without this flag Nautilus's ExecEngine rejects the excess in
            # internal bookkeeping — but it cannot reverse the on-chain
            # trade, so state.positions diverges from wallet reality. Accept
            # the overfill so the fill event propagates to the strategy.
            allow_overfills=True,
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
        lookahead_days=lookahead_days,
        max_submissions_this_session=max_submissions,
        min_entry_hour_local=min_entry_hour_local,
        max_yes_ask=max_yes_ask,
        entry_window_minutes=entry_window_minutes,
        max_usd_per_market=max_usd_per_market,
        max_ask_walk=max_ask_walk,
    )
    strategy = ConsensusFadeStrategy(config=strategy_config)

    node = TradingNode(config=node_config)
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(POLYMARKET, PolymarketLiveDataClientFactory)
    node.add_exec_client_factory(POLYMARKET, PolymarketLiveExecClientFactory)
    node.build()
    return node


def run(*, max_no_price: float = 0.99,
        shares_per_market: int = 110,
        lookahead_days: int = 1,
        max_submissions: int | None = None,
        min_entry_hour_local: int = 16,
        max_yes_ask: float = 0.22,
        entry_window_minutes: int = 30,
        max_usd_per_market: float = 30.0,
        max_ask_walk: float = 0.04) -> int:
    """Start the trading node. Runs continuously until Ctrl+C.

    Initial instrument set = today's qualifying markets (seed for the
    provider's upfront load). The strategy's rollover logic expands this
    automatically as new markets qualify, and unsubscribes resolved ones.
    Safe to start with zero initial markets — the strategy will subscribe
    dynamically as consensus tightens.
    """
    _ensure_env()

    # Seed subscriptions across [yesterday_utc, today_utc + lookahead]. The
    # yesterday_utc window covers Eastern markets whose market_date is "just
    # ended in UTC" but the airport's local day hasn't ended yet (daily
    # 20:00-23:59 EDT phenomenon). Dedup by condition_id so the same slug
    # isn't loaded twice.
    today_utc = datetime.now(UTC).date()
    seen_condition_ids: set[str] = set()
    markets: list[TradeableMarket] = []
    for d_offset in range(-1, lookahead_days + 1):
        try:
            daily = discover_tradeable_markets(target_date=today_utc + timedelta(days=d_offset))
        except FileNotFoundError:
            continue
        for m in daily:
            if m.condition_id in seen_condition_ids:
                continue
            seen_condition_ids.add(m.condition_id)
            markets.append(m)
    print_discovery_summary(markets)
    if not markets:
        print("[node] no markets tradeable right now — starting anyway. "
              "Strategy will subscribe dynamically as markets qualify.")
    else:
        print(f"[node] starting TradingNode with {len(markets)} initial market(s); "
              f"max_no_price={max_no_price}, {shares_per_market} shares/market. "
              f"Rollover handles new/resolved markets automatically.")

    if max_submissions is not None:
        print(f"[node] session-wide submission cap: {max_submissions}. "
              f"Strategy will stop after that many IOCs (any outcome).")
    if min_entry_hour_local > 0:
        print(f"[node] entry gate: per-city local clock ≥ "
              f"{min_entry_hour_local:02d}:00. Each airport's own tz.")
    if max_yes_ask < 1.0:
        print(f"[node] market-wisdom cap: yes_ask ≤ {max_yes_ask} "
              f"(no_bid ≥ {1.0 - max_yes_ask:.2f})")
    print(f"[node] entry window: {entry_window_minutes} minutes from first "
          f"all-gates-pass per market")
    print(f"[node] per-market USD cap: ${max_usd_per_market:.2f} "
          f"(resets per market, in-memory — restarts currently reset it; "
          f"see STRATEGY.md §7)")
    print(f"[node] slippage cap: max_ask_walk=${max_ask_walk:.3f} "
          f"above best in-range NO ask")

    node = _build_node(markets,
                       max_no_price=max_no_price,
                       shares_per_market=shares_per_market,
                       lookahead_days=lookahead_days,
                       max_submissions=max_submissions,
                       min_entry_hour_local=min_entry_hour_local,
                       max_yes_ask=max_yes_ask,
                       entry_window_minutes=entry_window_minutes,
                       max_usd_per_market=max_usd_per_market,
                       max_ask_walk=max_ask_walk)
    try:
        node.run()
    finally:
        node.dispose()
    return 0

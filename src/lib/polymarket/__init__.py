"""Polymarket CLOB execution library.

Thin wrappers around `py-clob-client` and `web3` for:
- One-time wallet setup (USDC + ConditionalTokens allowances on 3 contracts)
- Deterministic L2 API credential derivation
- Limit order placement / cancellation
- Market metadata lookup

Upstream reference: vault/Weather Vault/wiki/concepts/Polymarket CLOB execution.md
"""
from lib.polymarket.client import PolymarketClient, load_client_from_env
from lib.polymarket.markets import BucketMarket, get_daily_temp_markets
from lib.polymarket.orders import OrderResult, cancel_order, place_limit_buy
from lib.polymarket.setup import check_setup, run_setup

__all__ = [
    "BucketMarket",
    "OrderResult",
    "PolymarketClient",
    "cancel_order",
    "check_setup",
    "get_daily_temp_markets",
    "load_client_from_env",
    "place_limit_buy",
    "run_setup",
]

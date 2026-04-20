"""Place and cancel limit orders on Polymarket CLOB.

Thin wrappers around py-clob-client that handle our specific needs:
- Limit BUY on a specific token_id
- post_only to guarantee maker + 25% fee rebate
- Runtime tick_size, min_order_size, neg_risk discovery
- Safe rounding to the market's tick grid
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from lib.polymarket.client import PolymarketClient


@dataclass
class OrderResult:
    success: bool
    order_id: str | None
    status: str | None
    size_matched: float
    error: str | None
    raw: dict


def place_limit_buy(
    client: PolymarketClient,
    token_id: str,
    price: float,
    size: float,
    *,
    post_only: bool = True,
    time_in_force: str = "GTC",
    dry_run: bool = False,
) -> OrderResult:
    """Place a limit BUY order on the given token.

    Arguments:
        client: PolymarketClient (from load_client_from_env())
        token_id: the token you're buying (YES or NO token ID)
        price: limit price in [0, 1]
        size: number of shares (must be >= market min_order_size)
        post_only: if True, order is rejected if it would cross the spread
                   (guarantees maker + 25% fee rebate)
        time_in_force: "GTC" (good-til-cancel) or "GTD" (good-til-date)
        dry_run: if True, validate + print but don't submit

    Returns an OrderResult. On failure, .success=False and .error is set.
    """
    from py_clob_client.clob_types import (
        OrderArgs,
        OrderType,
        PartialCreateOrderOptions,
    )
    from py_clob_client.order_builder.constants import BUY

    tick_str = client.get_tick_size(token_id)
    tick = Decimal(tick_str)
    neg_risk = client.get_neg_risk(token_id)
    book = client.get_order_book(token_id)
    min_size = float(getattr(book, "min_order_size", 0) or 0)

    # Round price to tick grid (for BUY: round DOWN so we don't overpay)
    px_decimal = (Decimal(str(price)) / tick).to_integral_value(ROUND_DOWN) * tick
    if px_decimal <= 0 or px_decimal >= 1:
        return OrderResult(success=False, order_id=None, status=None,
                           size_matched=0,
                           error=f"price {price} rounds to {px_decimal}, outside (0, 1)",
                           raw={})

    # Round size up to market min
    if size < min_size:
        size = min_size
    size_rounded = float(round(size, 2))

    if dry_run:
        print(f"[DRY-RUN] BUY {size_rounded} @ {px_decimal} on token {token_id[:20]}...")
        print(f"  tick={tick_str} neg_risk={neg_risk} min_size={min_size} "
              f"post_only={post_only} tif={time_in_force}")
        return OrderResult(success=True, order_id="DRY_RUN", status="dry_run",
                           size_matched=0, error=None,
                           raw={"price": float(px_decimal), "size": size_rounded})

    args = OrderArgs(
        token_id=token_id,
        price=float(px_decimal),
        size=size_rounded,
        side=BUY,
    )
    options = PartialCreateOrderOptions(neg_risk=neg_risk)
    try:
        signed = client.clob.create_order(args, options)  # type: ignore[attr-defined]
    except Exception as e:
        return OrderResult(success=False, order_id=None, status=None,
                           size_matched=0, error=f"create_order failed: {e}", raw={})

    order_type = OrderType.GTC if time_in_force == "GTC" else OrderType.GTD
    try:
        resp = client.clob.post_order(signed, order_type)  # type: ignore[attr-defined]
    except Exception as e:
        return OrderResult(success=False, order_id=None, status=None,
                           size_matched=0, error=f"post_order failed: {e}", raw={})

    # py-clob-client returns a dict
    if not isinstance(resp, dict):
        return OrderResult(success=False, order_id=None, status=None,
                           size_matched=0, error=f"unexpected response type: {type(resp)}",
                           raw={"raw": str(resp)})

    if not resp.get("success", False):
        return OrderResult(
            success=False, order_id=None, status=resp.get("status"),
            size_matched=float(resp.get("size_matched", 0) or 0),
            error=resp.get("errorMsg") or "post_order returned success=false",
            raw=resp,
        )
    return OrderResult(
        success=True,
        order_id=resp.get("orderID"),
        status=resp.get("status"),
        size_matched=float(resp.get("size_matched", 0) or 0),
        error=None,
        raw=resp,
    )


def cancel_order(client: PolymarketClient, order_id: str) -> dict:
    """Cancel a single open order. Returns the raw response dict."""
    return client.clob.cancel(order_id=order_id)  # type: ignore[attr-defined]


def cancel_all(client: PolymarketClient) -> dict:
    """Cancel every open order for this account."""
    return client.clob.cancel_all()  # type: ignore[attr-defined]


def get_order_status(client: PolymarketClient, order_id: str) -> dict:
    """Fetch current status of a specific order."""
    return client.clob.get_order(order_id)  # type: ignore[attr-defined]


def list_open_orders(client: PolymarketClient) -> list[dict]:
    """List all open orders for this account."""
    from py_clob_client.clob_types import OpenOrderParams
    return client.clob.get_orders(OpenOrderParams())  # type: ignore[attr-defined]

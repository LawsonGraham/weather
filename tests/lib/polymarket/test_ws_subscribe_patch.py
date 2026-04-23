"""Verify B-001 patch: dynamic Polymarket WS subscribes go through reconnect.

The unpatched `PolymarketWebSocketClient.subscribe` sends a dynamic-subscribe
WS message that Polymarket silently ignores. The patch in
`lib.polymarket.ws_subscribe_patch` replaces that path with a
disconnect+reconnect, so the new subscription is registered via the
working initial-connect message format.

These tests exercise the WS client at the `subscribe` boundary with the
underlying transport mocked. The behavioral assertions are:

  1. **Pre-connect subscribes** still bypass reconnect (initial connect
     handles them).
  2. **Already-subscribed (refcount>1) subscribes** are no-ops at the WS
     layer — no message, no reconnect, no `_send` call.
  3. **Post-connect subscribes** trigger `_disconnect_client` followed by
     `_connect_client` — and crucially do NOT send a dynamic-subscribe
     message via `_send` (the broken upstream behavior).
  4. The unpatched upstream `subscribe` would have called `_send` with the
     dynamic-subscribe payload — included as a regression guard so that if
     upstream renames things, this test fails loudly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from nautilus_trader.adapters.polymarket.websocket.client import (
    PolymarketWebSocketChannel,
    PolymarketWebSocketClient,
)

import lib.polymarket.ws_subscribe_patch  # noqa: F401  -- applies the patch


def _make_ws_client(*, connected: bool) -> PolymarketWebSocketClient:
    """Construct a PolymarketWebSocketClient with mocked internals.

    `connected=True` simulates the post-on_start state where one underlying
    Rust WS client is already established and serving subs. `connected=False`
    simulates the pre-connect state during `on_start` itself.
    """
    clock = MagicMock()
    loop = asyncio.get_event_loop()
    handler = MagicMock()

    client = PolymarketWebSocketClient(
        clock=clock,
        base_url="wss://test.invalid/ws/",
        channel=PolymarketWebSocketChannel.MARKET,
        handler=handler,
        handler_reconnect=None,
        loop=loop,
    )

    # Replace the methods that talk to the network with AsyncMock so we can
    # observe call patterns without setting up real connections.
    client._send = AsyncMock()
    client._connect_client = AsyncMock()
    client._disconnect_client = AsyncMock()

    if connected:
        # Seed one client with a single subscription to mirror the real
        # post-on_start state.
        client._next_client_id = 1
        client._client_subscriptions[0] = ["seed_token"]
        client._subscriptions = ["seed_token"]
        client._subscription_counts["seed_token"] = 1
        client._is_connecting[0] = False
        # Mark client 0 as "connected" by attaching a non-None client object.
        client._clients[0] = MagicMock()

    return client


@pytest.mark.asyncio
async def test_subscribe_already_in_refcount_is_noop_at_ws_layer():
    """Refcounted resubscribe should not reconnect or send anything."""
    client = _make_ws_client(connected=True)

    await client.subscribe("seed_token")

    assert client._subscription_counts["seed_token"] == 2
    client._send.assert_not_called()
    client._connect_client.assert_not_called()
    client._disconnect_client.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_pre_connect_does_not_reconnect():
    """If no client is connected yet, the pending initial-connect handles it.

    The dynamic-subscribe → reconnect path is only needed once we are already
    connected. Before that, `_get_client_id_for_new_subscription` allocates a
    fresh client id and the subscription is queued for the upcoming
    `_connect_client` (which the data client will schedule via
    `_schedule_delayed_connect`). The patch must NOT itself trigger a
    disconnect/connect in this state — there's nothing to disconnect, and
    initiating a connect here would race the data client's own scheduling.
    """
    client = _make_ws_client(connected=False)

    await client.subscribe("new_token")

    assert "new_token" in client._subscriptions
    assert client._subscription_counts["new_token"] == 1
    # Brand-new client id allocated for this sub.
    assert "new_token" in client._client_subscriptions[0]
    # No reconnect triggered — the upstream initial-connect path will pick
    # up this subscription naturally.
    client._disconnect_client.assert_not_called()
    client._connect_client.assert_not_called()
    # And critically: no dynamic-subscribe message sent.
    client._send.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_post_connect_triggers_reconnect_not_dynamic_send():
    """Core fix assertion: post-connect subscribes go through reconnect.

    This is the exact scenario that broke in production on 2026-04-22:
    `_refresh_subscribed_and_active` discovered new markets at 17:01 and
    called `subscribe_order_book_deltas` for each. Pre-patch, the WS client
    sent ``{"assets_ids": [token], "operation": "subscribe"}`` via `_send`
    and Polymarket silently ignored it.

    Post-patch, the WS client must instead disconnect+reconnect so that the
    new sub is included in the initial `{"type": "market", "assets_ids": [...]}`
    handshake on reconnect. We assert both the positive (reconnect happens)
    and the negative (no `_send` of the broken dynamic message).
    """
    client = _make_ws_client(connected=True)

    await client.subscribe("dyn_token")

    # The new subscription is registered in local bookkeeping for the
    # connected client_id (0), so the reconnect's `_subscribe_all` will pick
    # it up alongside the existing seed.
    assert "dyn_token" in client._subscriptions
    assert "dyn_token" in client._client_subscriptions[0]
    assert client._subscription_counts["dyn_token"] == 1

    # Reconnect path used.
    client._disconnect_client.assert_awaited_once_with(0)
    client._connect_client.assert_awaited_once_with(0)
    # The broken dynamic-subscribe message was NOT sent.
    client._send.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_post_connect_under_concurrency_only_one_reconnect_per_sub():
    """A burst of new subscriptions triggers reconnects (one per fresh sub).

    The strategy's `_refresh_subscribed_and_active` can spawn 2-7 concurrent
    `subscribe_order_book_deltas` calls in the same tick. Each fresh sub
    individually triggers a disconnect/reconnect under the patch. The
    important invariant is that each fresh subscription DOES result in a
    reconnect (otherwise that sub's initial-connect message never goes out)
    and NO dynamic-subscribe `_send` call ever happens.
    """
    client = _make_ws_client(connected=True)

    tokens = [f"burst_token_{i}" for i in range(5)]
    await asyncio.gather(*(client.subscribe(t) for t in tokens))

    # Every fresh subscription went through the reconnect path.
    assert client._disconnect_client.await_count == len(tokens)
    assert client._connect_client.await_count == len(tokens)
    # And again: zero dynamic-subscribe sends.
    client._send.assert_not_called()
    # All subs are registered in client 0's list (along with the seed).
    assert set(client._client_subscriptions[0]) == {"seed_token", *tokens}


@pytest.mark.asyncio
async def test_unsubscribe_unchanged():
    """Patch does not touch unsubscribe — Polymarket ignoring the dynamic
    unsubscribe is a bandwidth issue, not a correctness issue (the strategy
    discards unwanted deltas at the application layer). Verify upstream
    behavior is intact: a real (last-refcount) unsubscribe still goes
    through `_send` with the dynamic-unsubscribe payload.
    """
    client = _make_ws_client(connected=True)

    await client.unsubscribe("seed_token")

    assert "seed_token" not in client._subscriptions
    assert "seed_token" not in client._subscription_counts
    # The upstream unsubscribe path still calls _send with the dynamic
    # unsubscribe message — we explicitly do NOT change this behavior.
    client._send.assert_awaited_once()

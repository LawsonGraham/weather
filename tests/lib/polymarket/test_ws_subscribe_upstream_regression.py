"""Regression-witness for B-001 upstream behavior.

This test loads a *fresh, unpatched* copy of `PolymarketWebSocketClient`
(by reaching past the patch via `importlib`) and demonstrates that the
upstream `subscribe` path sends the broken dynamic-subscribe message
through `_send` instead of triggering a reconnect.

If this test ever flips (i.e., upstream changes its behavior), our patch
may no longer be needed — re-evaluate before bumping the pinned
nautilus-trader commit. Until then, this is the canonical proof of what
we are fixing.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


def _load_unpatched_ws_client_module():
    """Force a fresh import of the upstream module, bypassing our patch.

    The patch in `lib.polymarket.ws_subscribe_patch` mutates the class on
    import. To observe the original behavior we reload the module and grab
    a reference to the class before the patch can be re-applied.
    """
    # Drop the cached module so the next import re-runs class definitions.
    sys.modules.pop("nautilus_trader.adapters.polymarket.websocket.client", None)
    mod = importlib.import_module("nautilus_trader.adapters.polymarket.websocket.client")
    # Sanity: this fresh class must NOT carry our patch marker. If it does,
    # something cached the patched class globally and we cannot demonstrate
    # the upstream bug without process isolation.
    assert not getattr(mod.PolymarketWebSocketClient, "_b001_dynamic_subscribe_patched", False), (
        "Reloaded module still shows the patch marker — test cannot witness "
        "upstream behavior. Run this test in isolation."
    )
    return mod


def _make_ws_client_connected(mod) -> object:
    clock = MagicMock()
    loop = asyncio.get_event_loop()
    handler = MagicMock()
    client = mod.PolymarketWebSocketClient(
        clock=clock,
        base_url="wss://test.invalid/ws/",
        channel=mod.PolymarketWebSocketChannel.MARKET,
        handler=handler,
        handler_reconnect=None,
        loop=loop,
    )
    client._send = AsyncMock()
    client._connect_client = AsyncMock()
    client._disconnect_client = AsyncMock()
    client._next_client_id = 1
    client._client_subscriptions[0] = ["seed_token"]
    client._subscriptions = ["seed_token"]
    client._subscription_counts["seed_token"] = 1
    client._is_connecting[0] = False
    client._clients[0] = MagicMock()
    return client


@pytest.mark.asyncio
async def test_upstream_subscribe_sends_broken_dynamic_message():
    """Witness the bug: upstream sends a dynamic-subscribe message that
    Polymarket silently ignores, instead of reconnecting.
    """
    mod = _load_unpatched_ws_client_module()
    client = _make_ws_client_connected(mod)

    await client.subscribe("dyn_token")

    # Bookkeeping is done — but the wire message is the broken dynamic
    # subscribe, not a reconnect.
    client._disconnect_client.assert_not_called()
    client._connect_client.assert_not_called()
    client._send.assert_awaited_once()

    sent_args = client._send.call_args
    sent_msg = sent_args.args[1]
    # The exact payload Polymarket ignores. This is what we replace.
    assert sent_msg == {
        "assets_ids": ["dyn_token"],
        "operation": "subscribe",
    }, (
        "Upstream subscribe payload changed shape — re-validate B-001 patch "
        "and consider whether Polymarket now honors dynamic subscribes."
    )

    # Re-apply our patch for any tests that follow in this process.
    importlib.import_module("lib.polymarket.ws_subscribe_patch").apply_patch()

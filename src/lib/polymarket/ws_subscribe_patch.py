"""Runtime patch: route dynamic Polymarket WS subscribes through reconnect.

Background — Bug B-001
======================

`nautilus_trader.adapters.polymarket.websocket.client.PolymarketWebSocketClient`
implements two distinct subscribe paths:

  * **Initial-connect path** (`_subscribe_all`) sends ``{"type": "market",
    "assets_ids": [...]}`` as part of the WebSocket handshake. This is the
    documented Polymarket CLOB WSS subscribe format and is honored by the
    server: deltas start flowing for the listed assets within seconds.

  * **Dynamic-subscribe path** (`_create_dynamic_subscribe_msg`, used by
    `subscribe()` once the client is already connected) sends
    ``{"assets_ids": [...], "operation": "subscribe"}``. **Polymarket's CLOB
    WSS server silently ignores this message format.** No deltas ever arrive
    for assets added this way, even though the local subscription bookkeeping
    looks correct and `cache.order_book(iid)` returns a valid (but empty) book
    object.

Polymarket's official client (`py_clob_client`) has no concept of dynamic
subscribes — the documented contract is "list every asset in the initial
handshake". The Nautilus dynamic message is a pure adapter-side invention and
hits the void.

First observed during live trading on 2026-04-22: a market discovered and
subscribed at 17:01 stayed at empty book until the bot restarted at 17:54
(re-seeding via the initial-connect path). Symptoms documented in BUGS.md
B-001.

The fix
=======

We monkey-patch `PolymarketWebSocketClient.subscribe` so that whenever a new
subscription would otherwise be sent via the broken dynamic message, we
instead disconnect the affected client and immediately reconnect. The
reconnect uses `_subscribe_all` — the working initial-connect message format
— and includes the new subscription. Net effect: dynamic subscribes are
applied via a brief disconnect/reconnect round-trip rather than a no-op
message.

The patch:
  * Preserves the lock-based bookkeeping (refcount, `_subscriptions`,
    `_client_subscriptions`) exactly as upstream does it.
  * Coalesces concurrent dynamic subscribes via the existing `_is_connecting`
    flag — only one reconnect runs at a time, and any subs queued during the
    reconnect are picked up by the same `_subscribe_all` call.
  * No-ops the patch on already-subscribed (refcount > 0) and on subs added
    before the initial connect (those go through `_connect_client` naturally,
    same as upstream behavior).

Apply the patch by importing this module **once**, before any
`PolymarketDataClient` is instantiated. Idempotent — re-imports do nothing.
"""

from __future__ import annotations

import asyncio

from nautilus_trader.adapters.polymarket.websocket.client import PolymarketWebSocketClient

_PATCH_MARKER = "_b001_dynamic_subscribe_patched"


async def _patched_subscribe(self: PolymarketWebSocketClient, subscription: str) -> None:
    """Replacement for PolymarketWebSocketClient.subscribe.

    Polymarket WSS ignores dynamic subscribe messages, so we instead
    disconnect+reconnect the client to register the new subscription via the
    initial-connect message format. See module docstring for details.
    """
    target_client_id: int | None = None
    must_reconnect = False

    async with self._lock:
        count = self._subscription_counts.get(subscription, 0)
        self._subscription_counts[subscription] = count + 1

        if count > 0:
            self._log.debug(
                f"Already subscribed to {subscription} (count={count + 1})",
            )
            return

        self._subscriptions.append(subscription)
        target_client_id = self._get_client_id_for_new_subscription()
        self._client_subscriptions[target_client_id].append(subscription)

        # If a connected client exists for this subscription, we have to
        # reconnect — Polymarket ignores the dynamic-subscribe message that
        # upstream `subscribe` would otherwise send. If no client exists yet,
        # the pending `_connect_client` call (initial connect) will pick up
        # this subscription via `_subscribe_all` automatically.
        if self._clients.get(target_client_id) is not None:
            must_reconnect = True

    if not must_reconnect:
        return

    # Wait for any in-flight (re)connect to finish. If our subscription was
    # appended before that connect's `_subscribe_all` ran, we're already
    # registered with the server and don't need to reconnect ourselves.
    while self._is_connecting.get(target_client_id):
        await asyncio.sleep(0.05)

    # Disconnect+reconnect to apply the new subscription via the working
    # initial-connect message format. `_connect_client` itself sets
    # `_is_connecting[cid] = True` for its duration, so concurrent subscribes
    # spinning on the wait above will coalesce onto this single reconnect.
    self._log.debug(
        f"ws-client {target_client_id}: reconnecting to register dynamic "
        f"subscription {subscription} (Polymarket WSS ignores dynamic subscribes; "
        f"see B-001 patch)",
    )
    await self._disconnect_client(target_client_id)
    await self._connect_client(target_client_id)


def apply_patch() -> None:
    """Idempotently install the dynamic-subscribe reconnect patch."""
    if getattr(PolymarketWebSocketClient, _PATCH_MARKER, False):
        return
    PolymarketWebSocketClient.subscribe = _patched_subscribe  # type: ignore[method-assign]
    setattr(PolymarketWebSocketClient, _PATCH_MARKER, True)


# Apply on import so consumers only have to write `import lib.polymarket.ws_subscribe_patch`.
apply_patch()

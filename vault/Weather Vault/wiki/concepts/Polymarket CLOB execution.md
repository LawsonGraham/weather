---
tags: [concept, polymarket, execution, trading-stack]
date: 2026-04-16
related: "[[Polymarket]], [[Polymarket CLOB WebSocket]], [[2026-04-11 Polymarket fee structure + maker rebate pivot]]"
---

# Polymarket CLOB Execution

How to submit orders programmatically to the Polymarket international CLOB. Focused on the `py-clob-client` v1 Python library — what `strategies/consensus_fade_plus1/recommender.py` will graduate to once we're placing live orders.

## Stack at a glance

- **Chain**: Polygon mainnet (chain_id `137`). USDC is collateral, ERC-1155 conditional tokens are shares.
- **Host**: `https://clob.polymarket.com` (international). Polymarket US (`api.polymarket.us`, Ed25519 auth, KYC, invite-only as of April 2026) is separate and not covered here.
- **WebSocket**: `wss://ws-subscriptions-clob.polymarket.com/ws/{market,user}` for book updates / fill notifications.
- **Library**: `py-clob-client` v1 (mature; all official docs target it). `py-clob-client-v2` exists but examples still target v1.
- **No testnet in practice**. `Amoy` (chain_id 80002) is in the code but no liquid CLOB runs there. All smoke-testing is real-money Polygon mainnet.

## One-time wallet setup

1. Create / choose a Polygon private key. Fund with a few dollars of MATIC (gas for approvals) and some USDC (trading collateral).
2. **Set allowances on three exchange contracts** (EOA/MetaMask-style wallets only; Polymarket web / email wallets auto-set):
   - USDC `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
   - Conditional Tokens `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
   - Spender contracts to approve on both:
     - Main CTF Exchange `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`
     - NegRisk CTF Exchange `0xC5d563A36AE78145C45a50134d48A1215220f80a`
     - NegRisk Adapter `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`
   - Reference script: [`poly-rodr` gist](https://gist.github.com/poly-rodr/44313920481de58d5a3f6d1f8226bd5e).
3. **Derive L2 API creds** (one-time per wallet, deterministic thereafter):
   ```python
   bootstrap = ClobClient(HOST, key=PK, chain_id=POLYGON)
   creds = bootstrap.create_or_derive_api_creds()
   # Persist creds.api_key / api_secret / api_passphrase to .env
   ```
   Same nonce always returns the same creds — this is a derive, not a create.

## Auth layers

| Layer | Purpose | Use |
|---|---|---|
| **L1** — EIP-712 with private key | Order signing; derive L2 creds | Every order payload (signed locally by `client.create_order()`) |
| **L2** — 5 HMAC headers | All POST/DELETE API calls | `POLY_ADDRESS`, `POLY_API_KEY`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_PASSPHRASE` — handled by the client |

## Order types

| Type | Semantics |
|---|---|
| `GTC` | Good-til-cancelled — resting limit |
| `GTD` | Good-til-date — limit with expiration (60s min buffer) |
| `FOK` | Fill-or-kill — all-or-nothing market |
| `FAK` | Fill-and-kill — market, partial OK |

`post_only=True` allowed on GTC/GTD only; rejects orders that would cross the spread (guarantees maker fill + 25% fee rebate).

## YES vs NO

Not a side flag — **separate `token_id`** per outcome. Binary markets have two token IDs in `clobTokenIds`, aligned with the `outcomes` array:
- `outcomes == ["Yes", "No"]` → `clobTokenIds[0]` is YES, `clobTokenIds[1]` is NO
- Always verify the outcome-name alignment; don't assume.

"Buying NO at $0.85" = `side=BUY, price=0.85, token_id=<NO token ID>`.

## Size, tick, minimums

| Field | What | Check at runtime |
|---|---|---|
| Price | `0.00`–`1.00` float | Must align to market tick size |
| Tick | `0.1 / 0.01 / 0.001 / 0.0001` | `client.get_tick_size(token_id)` |
| Size units (limit) | Shares | — |
| Size units (market BUY) | USDC dollars | — |
| Min size | Per-market, shares | `client.get_order_book(token_id).min_order_size` |
| neg_risk flag | Whether market is NegRisk CTF | `client.get_neg_risk(token_id)` (weather markets are NegRisk) |

**Observed: recent markets have `min_order_size=15` shares.** The old "5 shares" figure is stale.

## Rate limits (Cloudflare-enforced; over-limit → HTTP 429)

| Endpoint | Burst (req/10s) | Sustained (req/10m) |
|---|---|---|
| `POST /order` | 3,500 (~350/s) | 36,000 |
| `POST /orders` (batch, 15 orders/req) | 1,000 | 15,000 |
| `DELETE /order` | 3,000 | 30,000 |
| `DELETE /orders` | 1,000 | 15,000 |
| `DELETE /cancel-all` | 250 | 6,000 |
| `/book`, `/price`, `/midpoint` | 1,500 /10s | — |
| `/trades`, `/orders`, `/order` (GET) | 900 /10s | — |
| General default | 9,000 /10s | — |

For a weather bot placing ~5 orders/day and polling book ~every few minutes, these limits are never binding.

## Fill monitoring

**Preferred**: user-channel WebSocket `wss://ws-subscriptions-clob.polymarket.com/ws/user` — auth'd subscribe with `{apiKey, secret, passphrase}` and `markets: [condition_id, ...]`. Receive `order` and `trade` events. PING every 10s or you're disconnected.

**Trade lifecycle**: `MATCHED → MINED → CONFIRMED` (terminal) or `RETRYING → FAILED` (terminal).

**REST fallback**: `client.get_order(order_id)` / `client.get_orders(OpenOrderParams(...))` / `client.get_trades(...)`.

## Cancellation

| Method | Use |
|---|---|
| `client.cancel(order_id)` | Single order |
| `client.cancel_orders([id, id, ...])` | Bulk |
| `client.cancel_all()` | Everything |
| `client.cancel_market_orders(market=..., asset_id=...)` | Scope to one market |

Returns `{"canceled": [...], "not_canceled": {id: reason}}`.

## Smoke-test recipe (no real capital at risk)

1. Set allowances. Verify via `client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))` → non-zero.
2. Pick a deep-liquidity market (an election or sports market with tight spread and multi-thousand-share depth).
3. Place `post_only=True` limit at a price well below best-bid (BUY) or above best-ask (SELL) — won't fill, won't cost taker fee.
4. Minimum feasible order: 15 shares × $0.01 = $0.15 notional. Real outlay after approvals is ~$0.50–$5 in Polygon gas depending on network state.
5. Verify `get_order` round-trips, user-channel WSS delivers the `order/placed` event, `cancel` successfully unwinds.
6. Then move to strategy orders.

## Concrete snippet — limit BUY of NO at $0.85

```python
# pip install py-clob-client python-dotenv
import os
import time
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, OrderType, OpenOrderParams,
)
from py_clob_client.constants import POLYGON
from py_clob_client.order_builder.constants import BUY

load_dotenv()
HOST, PK = "https://clob.polymarket.com", os.environ["PK"]

# First run only: derive creds once, persist, remove this block
# bootstrap = ClobClient(HOST, key=PK, chain_id=POLYGON)
# creds = bootstrap.create_or_derive_api_creds()
# write creds.api_key etc. to .env

client = ClobClient(
    HOST, key=PK, chain_id=POLYGON,
    creds=ApiCreds(
        api_key=os.environ["CLOB_API_KEY"],
        api_secret=os.environ["CLOB_SECRET"],
        api_passphrase=os.environ["CLOB_PASS_PHRASE"],
    ),
    signature_type=0,  # 0=EOA, 1=email wallet, 2=proxy wallet
)

NO_TOKEN_ID = "..."  # from clobTokenIds[1] per the outcomes array

tick = client.get_tick_size(NO_TOKEN_ID)
neg_risk = client.get_neg_risk(NO_TOKEN_ID)
book = client.get_order_book(NO_TOKEN_ID)
min_size = float(book.min_order_size)

order = OrderArgs(
    token_id=NO_TOKEN_ID,
    price=0.85,
    size=max(20.0, min_size),
    side=BUY,
)
signed = client.create_order(order)
resp = client.post_order(signed, OrderType.GTC)
order_id = resp["orderID"]

# Poll (prefer user-channel WSS in production)
for _ in range(30):
    state = client.get_order(order_id)
    if state.get("status") in ("MATCHED", "CONFIRMED"):
        break
    time.sleep(2)
else:
    client.cancel(order_id=order_id)
```

## Gotchas

- `Amoy` (chain_id 80002) appears in example code as a default. **Force `chain_id = POLYGON`** or you'll sign against the wrong domain and every submit will fail.
- `neg_risk` misflag → signs against wrong exchange → order accepted but won't fill. Weather markets are NegRisk (`True`).
- `min_order_size` is not stable across markets. Hardcoding 5 or 10 will intermittently fail with `INVALID_ORDER_MIN_SIZE`.
- Tick-size drift: most markets are `0.01`, but some are `0.001` or `0.0001`. Always query.
- US IP → geoblock. VPN is a policy violation (not an engineering one, but worth knowing).

## Open questions to resolve before live deployment

1. Do our weather markets return `min_order_size=15`? Query the orderbook of one `+1 offset` bucket and confirm.
2. Real fill experience vs quoted `best_yes_bid + 0.01` — does our post-only limit order actually sit at that level, or does it get replaced by a more aggressive MM quote?
3. Maker-rebate flow — we'd earn 25% of fee on each filled share. Need to account for this in realized PnL tracking.
4. Slug catalog lookup — `scripts/polymarket_weather/transform.py` already produces `yes_token_id` and `no_token_id` per slug. Plug directly into `token_id` field.

## References

- [py-clob-client v1 (canonical)](https://github.com/Polymarket/py-clob-client)
- [CLOB docs index](https://docs.polymarket.com/developers/CLOB/introduction)
- [Authentication](https://docs.polymarket.com/developers/CLOB/authentication)
- [Create Order](https://docs.polymarket.com/developers/CLOB/orders/create-order)
- [Cancel Orders](https://docs.polymarket.com/developers/CLOB/orders/cancel-orders)
- [L2 methods](https://docs.polymarket.com/developers/CLOB/clients/methods-l2)
- [WebSocket overview](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [Rate limits](https://docs.polymarket.com/quickstart/introduction/rate-limits)
- [Allowances reference gist](https://gist.github.com/poly-rodr/44313920481de58d5a3f6d1f8226bd5e)
- [AgentBets operational notes](https://agentbets.ai/guides/polymarket-api-guide/)

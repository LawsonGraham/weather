# Quickstart — Consensus-Fade +1 Offset

One-time setup, then daily operations. Full design in `STRATEGY.md`.

## First-time setup (5 minutes + ~$10 capital)

1. **Clone + install**
   ```sh
   uv sync
   ```

2. **Create a fresh wallet for trading**
   Generate a new Polygon private key. **Don't reuse a personal wallet.** Save the key somewhere safe — you'll need it exactly once.

3. **Fund the wallet (on Polygon mainnet)**
   - ~$5 MATIC for gas
   - USDC — as much as you plan to trade with (start with $100 to smoke-test)

4. **Populate `.env`**
   ```sh
   cp .env.example .env
   # Edit .env and fill in PK="0x<your_64_char_private_key>"
   ```

5. **Run setup (idempotent — safe to re-run)**
   ```sh
   uv run cfp setup
   ```
   This will:
   - Check + approve USDC spending on 3 exchange contracts
   - Check + approve Conditional Tokens on the same 3 contracts
   - Derive L2 API credentials and write them back into `.env`

   Cost: ~$0.50-2 in Polygon gas for the 6 approval txs. Only runs missing approvals on re-run.

6. **Verify**
   ```sh
   uv run cfp setup --check
   ```
   Should print `Status: READY`.

## Daily operations

### See today's recommendations (no orders placed)
```sh
uv run cfp recommend
uv run cfp recommend --date 2026-04-20
uv run cfp recommend --no-live    # skip the live CLOB price fetch
```

Output columns:
- `cs` — consensus spread (°F) between NBS/GFS/HRRR forecasts
- `NBS/GFS/HRRR` — the three forecasts
- `fav` — NBS favorite bucket
- `+1 bucket` — the bucket we'd BUY NO on
- `yes_mid`, `no_ask` — live CLOB prices
- `edge` — estimated edge in percentage points

### Dry-run submit (computes everything, doesn't place orders)
```sh
uv run cfp submit --dry-run --stake-usd 20
```

### Place real orders
```sh
uv run cfp submit --stake-usd 20
```
Defaults to today's date, consensus ≤ 3°F filter, post-only limit at NO ask.
Writes an append-only ledger to `data/processed/cfp_ledger.jsonl`.

### Check status
```sh
uv run cfp status            # open orders, address
```

### Emergency halt
```sh
uv run cfp cancel-all --yes  # cancel everything
```

## Useful command patterns

**Smoke test with minimum stake:**
```sh
uv run cfp submit --dry-run --stake-usd 15  # 15 shares × $0.90 = ~$13.50
uv run cfp submit --stake-usd 15            # real, minimal risk
```

**Tighter consensus filter (higher edge per trade, fewer trades):**
```sh
uv run cfp submit --consensus-max 2.0 --stake-usd 20
```

**Pre-resolution cleanup (run ~04:00 UTC before markets close):**
```sh
uv run cfp cancel-all --yes
```

## Required `.env` variables

| Variable | Required | Who sets it |
|---|---|---|
| `PK` | yes | you — once, after creating wallet |
| `CLOB_API_KEY` | yes | `cfp setup` derives and writes |
| `CLOB_SECRET` | yes | `cfp setup` derives and writes |
| `CLOB_PASS_PHRASE` | yes | `cfp setup` derives and writes |
| `CLOB_SIG_TYPE` | no (defaults to 0 = EOA) | you, only if using proxy wallet |
| `CLOB_FUNDER` | no (only for proxy wallets) | you |

## What can go wrong

- **`INVALID_ORDER_MIN_SIZE`** — stake too small. Min is ~15 shares per bucket. Raise `--stake-usd`.
- **`INVALID_TICK_SIZE`** — price rounding off by one tick. Usually auto-handled; if not, check `client.get_tick_size()` for that token.
- **US IP → geoblock on `clob.polymarket.com`**. Not an engineering issue.
- **Gas spike on setup** — Polygon fees spike occasionally. Wait and retry; gas is still cents.
- **Stale feature data** — if `cfp recommend` shows "no candidates" but you expect some, check whether `data/processed/backtest_v3/features.parquet` covers today. Regenerate via `notebooks/experiments/backtest-v3/build_features.py`.

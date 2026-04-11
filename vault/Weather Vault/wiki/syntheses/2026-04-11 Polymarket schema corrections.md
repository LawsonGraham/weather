---
tags: [synthesis, polymarket, schema, gotcha]
date: 2026-04-11
related: "[[Project Scope]], [[Execution Stack — Source Review]]"
---

# Polymarket — schema gotchas and corrections

Notes discovered while building the native `scripts/polymarket_weather/` downloader (Gamma API + Goldsky orderbook subgraph). Captures corrections to the upstream `jon-becker/prediction-market-analysis` `docs/SCHEMAS.md` that matter for our weather-markets work.

## Gamma API — `polymarket/markets` table

| Field | Upstream doc says | Actual schema | Impact |
|---|---|---|---|
| `clob_token_ids` | not mentioned | **string, JSON array of 2 token IDs** (YES + NO) | **Join key** for markets → trades. Without it, the join is impossible. |
| `market_maker_address` | not mentioned | **string** | Present on modern markets. |

## Goldsky subgraph — `polymarket/trades` table

| Field | Upstream doc says | Actual schema | Impact |
|---|---|---|---|
| `maker_asset_id` | `int` | **string** (256-bit ERC-1155 token hash) | Joins must use string equality. |
| `taker_asset_id` | `int` | **string** (256-bit ERC-1155 token hash) | Same as above. |
| `timestamp` | not mentioned | **exists but is always null** | Wall-clock time must be derived via `block_number` → `blocks` table. |
| `_contract` | not mentioned | **two values: `CTF Exchange` and `NegRisk CTF Exchange`** | Filter handles both. NegRisk is Polymarket's newer multi-outcome contract. |

## `polymarket/legacy_trades` (pre-CTF FPMM trades, ~2020–2022)

- `timestamp` column exists but is always null — same `block_number → blocks` join requirement as the main trades table.
- Uses `fpmm_address`, not `condition_id`. A separate `fpmm_collateral_lookup.json` (upstream) maps addresses to collateral tokens. **Not shipped** in the R2 archive snapshot we fetched; skip FPMM trades unless we need pre-2023 data.

## Contract versions

Weather markets on Polymarket are overwhelmingly **NegRisk CTF Exchange** (verified **574/574** for NYC = 100%). The Goldsky orderbook subgraph indexes both base CTF and NegRisk `OrderFilledEvent` in the same endpoint — verified via a live test query against a known NegRisk token.

## Gamma-side gotchas (beyond the SCHEMAS.md corrections)

- **`outcome_prices` is a JSON-encoded string**, not a float array. For resolved markets it's typically `["0","1"]` or `["1","0"]`. For pre-resolution snapshots it's like `["0.6543","0.3457"]`.
- **`outcome_prices` can contain absurd scientific-notation precision strings** like `"0.0000001761935909832323804205989381462587"` — parse carefully as floats.
- **`created_at` is null for ~40k markets (~10%)** — older markets where creation time wasn't captured.
- **`end_date` is null for ~1.5k markets.**
- **`clob_token_ids: "[]"` (empty JSON array)** on some very old markets with no CLOB listing. Filter these out before joining to trades.

## Tag 103040 (`Daily Temperature`) — coverage gap

- Created **2025-12-31** and is **not retroactive**. Markets resolved before that date are untagged and do not appear in the native Gamma fetch.
- If we later need pre-tag-era historical weather markets, reingest the `jon-becker` Parquet dataset once for template matching, then delete it again.

## Rate limits / pagination

- Goldsky subgraph paginates `OrderFilledEvents` at **1000 per page**. Walk forward with `skip=0, 1000, 2000, …`, stop when a page returns < 1000 rows.
- Space page requests by **200 ms** to be polite. Serialize across tokens; don't parallelize.

## Source

These corrections were discovered empirically via `pyarrow.parquet.ParquetFile` and live subgraph test queries on 2026-04-10 → 2026-04-11 during the switch from `jon-becker/prediction-market-analysis` as an indirect source to the native Gamma + subgraph pipeline in `scripts/polymarket_weather/`.

The downstream implications are handled in the active downloader. This page exists so the gotchas are preserved across future sessions without spelunking through the code.

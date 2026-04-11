# `prediction_market_analysis` — download source

Pulls the Kalshi + Polymarket Parquet dataset from
[`jon-becker/prediction-market-analysis`](https://github.com/jon-becker/prediction-market-analysis)
and lands it under `data/raw/prediction_market_analysis/`.

- **Upstream repo:** https://github.com/jon-becker/prediction-market-analysis
- **Archive URL:** https://s3.jbecker.dev/data.tar.zst
- **Archive size:** ~36 GiB compressed (~54 GiB extracted, per snapshot fetched 2026-04-10)
- **Format:** zstd-compressed tarball of Parquet files
- **Citation:** Becker, J. (2026). *The Microstructure of Wealth Transfer in Prediction Markets*.

## Run

```bash
python3 scripts/download/prediction_market_analysis/script.py           # idempotent
python3 scripts/download/prediction_market_analysis/script.py --force   # resume or retry
python3 scripts/download/prediction_market_analysis/script.py --fresh   # delete partial + retry
```

See `scripts/download/README.md` for the download contract (manifest schema,
idempotency, logging, `--force` vs `--fresh` semantics).

## What you get

```
data/raw/prediction_market_analysis/
├── MANIFEST.json
├── download.log
├── kalshi/
│   ├── markets/       # Parquet: Kalshi market snapshots
│   └── trades/        # Parquet: Kalshi trade ticks
└── polymarket/
    ├── markets/       # Parquet: Polymarket market snapshots  (~102 MB, 41 files, ~408k rows)
    ├── trades/        # Parquet: Polymarket CTF Exchange OrderFilled events  (~45 GB, 40k+ files)
    ├── blocks/        # Parquet: Polygon block → timestamp lookup  (~843 MB, 785 files)
    └── legacy_trades/ # Parquet: pre-CTF FPMM trades (~2020-2022)  (~211 MB)
```

## Known upstream schema doc errors

The upstream `docs/SCHEMAS.md` is incomplete or wrong in a few places that
matter for our analysis.  Our finding (verified via `pyarrow.parquet.ParquetFile`):

| Table | Upstream doc says | Actual schema | Impact |
| --- | --- | --- | --- |
| `polymarket/markets` | `outcomes`, `outcome_prices`, `volume`, `liquidity`, ... | **Also has `clob_token_ids` (string, JSON array of 2 token IDs) and `market_maker_address` (string)** | `clob_token_ids` is the **join key** to the trades table — without it, markets → trades is impossible |
| `polymarket/trades` | `maker_asset_id: int`, `taker_asset_id: int` | **Both are `string`** (they're 256-bit ERC-1155 token hashes, not ints) | Pandas/DuckDB treat them as strings; joins must use string equality |
| `polymarket/trades` | (not mentioned) | **`timestamp` column exists but is always null** | Wall-clock time must be joined via `block_number` → `blocks` table |
| `polymarket/trades` | (not mentioned) | **`_contract` column has two values: `CTF Exchange` and `NegRisk CTF Exchange`** | Must handle both when filtering; NegRisk is Polymarket's newer multi-outcome contract |
| `polymarket/legacy_trades` | (not mentioned) | **`timestamp` column exists but is always null** | Same as above; join via `block_number` |

When we build the next downloader for Polymarket data directly (bypassing this
dataset), these corrections should be baked into our own schema.

## Data shape — quick reference

| Table | Rows | Size | Partitioning |
| --- | --- | --- | --- |
| `polymarket/markets` | ~408,863 | 102 MB | 41 files named `markets_<N>_<N+10000>.parquet` |
| `polymarket/trades` | (not counted yet) | 45 GB | 40,454 files named `trades_<block>_<block+10000>.parquet` — by Polygon block number in 10k chunks |
| `polymarket/blocks` | ~78M (expected) | 843 MB | 785 files named `blocks_<block>_<block+100000>.parquet` — 100k block chunks |
| `polymarket/legacy_trades` | (not counted yet) | 211 MB | 221 files named `trades_<block>_<block+10000>.parquet` |
| `kalshi/markets` | (not counted yet) | part of 3.9 GB kalshi tree | — |
| `kalshi/trades` | (not counted yet) | part of 3.9 GB kalshi tree | — |

## Gotchas

- **`outcome_prices` is a JSON-encoded string**, not a float array.  For
  resolved markets it's typically `["0","1"]` or `["1","0"]`.  For
  pre-resolution snapshots it's like `["0.6543","0.3457"]`.
- **`created_at` is null for ~40k markets** (~10%) — older markets where
  creation time wasn't captured.  `end_date` is null for ~1.5k.
- **`outcome_prices` can contain absurd scientific-notation precision strings**
  like `"0.0000001761935909832323804205989381462587"` — be ready to parse them
  as floats with care.
- **Some markets have `clob_token_ids: "[]"` (empty JSON array)** — typically
  very old markets with no CLOB listing.  Filter these out before joining to
  trades.
- **The FPMM `legacy_trades` table uses `fpmm_address`, not `condition_id`.**
  A separate `fpmm_collateral_lookup.json` (upstream) maps addresses to
  collateral tokens.  Not shipped in the R2 archive snapshot we fetched;
  skip FPMM trades unless we need pre-2023 data.

## Validation history

| Date | Script version | Status | Notes |
| --- | --- | --- | --- |
| 2026-04-10 | v1 (bash) | failed | Initial run; curl exit 92 (HTTP/2 stream reset at 77% / 27.8 GB). Revealed a bug: `--force` was deleting the partial, defeating resume. |
| 2026-04-10 | v2 (bash) | complete | Fixed `--force`; used aria2c to resume from the 27.8 GB partial. ~100 MiB/s average. |
| 2026-04-11 | v3 (python) | — | Ported to Python + per-source folder layout. |

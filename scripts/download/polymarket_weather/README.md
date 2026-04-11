# `polymarket_weather` — raw Gamma + subgraph downloader

Reads slug catalog at `weather-market-slugs/polymarket.csv` and, for each
selected slug, pulls:

1. **Market metadata** from the Polymarket **Gamma API** (the full ~90-field
   JSON object, keyed by slug).
2. **Trade fills** from the **Goldsky orderbook subgraph**, paginated via
   GraphQL, for each of the market's two CLOB token IDs (YES + NO).

Output lands under `data/raw/polymarket_weather/`:

```
data/raw/polymarket_weather/
├── MANIFEST.json
├── download.log
├── gamma/
│   └── <slug>.json                # full Gamma market object
└── fills/
    └── <slug>.json                # {"<token_id>": [OrderFilledEvent, ...]}
```

## Run

```bash
# Smoke test — 5 markets from NYC
python3 scripts/download/polymarket_weather/script.py --city "New York City" --limit 5

# Full NYC run
python3 scripts/download/polymarket_weather/script.py --city "New York City"

# Everything
python3 scripts/download/polymarket_weather/script.py

# Specific slugs
python3 scripts/download/polymarket_weather/script.py --slugs slug1,slug2,slug3

# Force refresh (ignore per-slug cache)
python3 scripts/download/polymarket_weather/script.py --city "New York City" --force
```

## Flags

| flag | default | description |
| --- | --- | --- |
| `--slugs-file` | `weather-market-slugs/polymarket.csv` | CSV input with a `slug` column (and a `city` column if `--city` is used) |
| `--city` | — | Filter to one city (matched against the `city` column) |
| `--slugs` | — | Explicit comma-separated slug list (overrides `--slugs-file`) |
| `--limit` | — | Only process the first N matching slugs (useful for smoke tests) |
| `--force` | off | Re-download even if a per-slug cache file already exists |
| `--concurrency` | 1 | Kept at 1 for simplicity and API politeness; raise with care |

## Idempotency

Per-slug caching: if `data/raw/polymarket_weather/gamma/<slug>.json` **and**
`data/raw/polymarket_weather/fills/<slug>.json` both exist, the slug is
skipped unless `--force` is passed.  Partial slugs (gamma present but
fills missing) are re-tried.

The `MANIFEST.json` tracks overall status: `in_progress` while the run is
active, `complete` on success, `failed` on error (via a try/except guard).

## Subgraph pagination

The Goldsky orderbook subgraph paginates OrderFilledEvents at 1000 per
page.  For each token, we walk forward with `skip=0, 1000, 2000, ...` and
stop when a page returns fewer than 1000 rows.  A 200 ms delay between
pages keeps us polite.

## Contract versions

Weather markets on Polymarket are overwhelmingly **NegRisk CTF Exchange**
(verified 574/574 for NYC = 100%).  The Goldsky subgraph indexes both
base CTF and NegRisk events in the same endpoint — verified via a live
test query against a known NegRisk token.

## Self-contained script

Following `scripts/download/README.md`, this script is self-contained —
no shared `_common.py` import.  Helpers (logging, manifest lifecycle,
HTTP, subgraph pagination) are all inlined.

## What comes next

A transform script (`scripts/transform/polymarket_weather_parquet/`) will
read this raw JSON tree and produce Parquet files under
`data/processed/polymarket_weather_parquet/` with two tables: `markets`
and `fills`.  The Parquet tables are the backtest-ready surface; this raw
JSON tree is the immutable snapshot.

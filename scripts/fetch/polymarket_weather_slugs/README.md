# `polymarket_weather_slugs` — weather market slug fetcher

Discovers every Polymarket market carrying one of 6 weather-related tags
via the public Gamma API and writes a CSV catalog to
`weather-market-slugs/polymarket.csv` at the repo root.

## Run

```bash
python3 scripts/fetch/polymarket_weather_slugs/script.py
python3 scripts/fetch/polymarket_weather_slugs/script.py --refresh   # re-hit API, ignore cache
```

## Output

- `weather-market-slugs/polymarket.csv` — one row per unique market
- `data/interim/polymarket_weather_slugs/raw_gamma/tag_<id>.json` — cached raw API responses (gitignored)
- `data/interim/polymarket_weather_slugs/MANIFEST.json` — provenance

## Source: the 6 weather tag IDs

| ID | Label | Slug | Created | Notes |
| --- | --- | --- | --- | --- |
| 103040 | Daily Temperature | `temperature` | 2025-12-31 | The big one — city daily high-temp markets |
| 1474 | climate & weather | `climate-weather` | 2024-02-27 | Broader umbrella; older |
| 102186 | Hurricane Season | `hurricane-season` | 2025-05-29 | — |
| 85 | Hurricanes | `hurricanes` | 2023-11-02 | Real hurricanes, not NHL team |
| 102239 | Flood | `flood` | 2025-06-09 | Tag exists; frequently zero markets |
| 103235 | Snow Storm | `snow-storm` | 2026-01-22 | Inches-of-snow city markets |

**Excluded:** `Climate & Science` (103037) — too noisy.  Contains measles,
SpaceX IPO tickers, moon landings, earthquakes.

## Known limitation — historical coverage gap

The `Daily Temperature` tag was created 2025-12-31 and is **not retroactive**.
Markets that resolved before then are untagged and **will not appear in this
fetcher's output**.  We accept this loss for now — the fetcher is deliberately
Gamma-only, with no jon-becker template fallback.

If we later need pre-tag-era historical weather markets, options are:

1. Re-introduce jon-becker's parquet dataset for one-time template matching
2. Brute-force paginate `/markets?closed=true&order=createdAt&ascending=false`
   and template-match locally — but the ~250k offset cap only reaches back
   ~36 days of market creation, so this doesn't cover 2024 or earlier

## Schema — `polymarket.csv`

One row per unique market (deduped by `condition_id`).

| Column | Notes |
| --- | --- |
| `slug` | Market URL slug |
| `condition_id` | On-chain condition ID (0x-prefixed hex) |
| `question` | Market question text |
| `city` | Parsed from question text (`New York City`, `London`, etc.), blank for non-city markets |
| `weather_tags` | Comma-separated tag labels |
| `volume_gamma` | `volumeNum` from the API |
| `liquidity_gamma` | `liquidityNum` from the API |
| `best_bid`, `best_ask`, `spread` | Current top-of-book snapshot |
| `last_trade_price` | Most recent trade price |
| `order_price_min_tick_size` | Minimum price increment (execution config) |
| `order_min_size` | Minimum order size |
| `neg_risk` | NegRisk CTF Exchange flag |
| `active` | Currently tradeable |
| `closed` | Resolved / stopped trading |
| `created_at` | ISO 8601 |
| `end_date` | ISO 8601 |
| `resolution_source` | URL or identifier where outcome is sourced |
| `group_item_title` | Polymarket's own strike-ladder group label |
| `clob_token_ids` | JSON array of `[YES_token_id, NO_token_id]` |
| `outcomes` | JSON array of outcome names |

## The CSV is the source of truth

`weather-market-slugs/polymarket.csv` is **committed to git** and is the
permanent source of truth for what counts as a Polymarket weather market.
Downstream scripts (downloaders, transforms, backtests) should read this
file rather than re-running the fetcher.

Re-run the fetcher only when you want to refresh the list (new markets
created upstream, tags added, etc.).

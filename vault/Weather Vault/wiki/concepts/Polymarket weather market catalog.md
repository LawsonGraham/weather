---
tags: [concept, polymarket, catalog]
date: 2026-04-11
related: "[[Polymarket]], [[2026-04-11 Polymarket schema corrections]]"
---

# Polymarket weather market catalog

The authoritative list of weather-related markets on [[Polymarket]], stored as a committed CSV at `weather-market-slugs/polymarket.csv` at repo root. Every downstream script (downloaders, transforms, backtests, live traders) keys off this catalog. Refreshed rarely — once the fetcher has run, it's a stable input until Polymarket adds new tags or the user explicitly re-fetches.

## How it is produced

`scripts/polymarket_weather_slugs/download.py` queries the [[Polymarket]] Gamma API for **6 weather tag IDs**, pages through closed + open markets, dedupes by `condition_id`, extracts a normalized `city` field from the question text, and writes the result to `weather-market-slugs/polymarket.csv`.

The 6 tag IDs and labels:

| Tag ID | Label |
|---|---|
| `103040` | Daily Temperature |
| `1474` | climate & weather |
| `102186` | Hurricane Season |
| `85` | Hurricanes |
| `102239` | Flood |
| `103235` | Snow Storm |

## Catalog size

~15,381 unique markets across the 6 tags as of 2026-04-11. Size fluctuates as Polymarket adds new strike-ladder daily-temperature markets and as old hurricanes / snow storms resolve.

## Columns

| Column | Notes |
|---|---|
| `slug` | Market URL slug (unique key) |
| `condition_id` | On-chain condition ID (0x-prefixed hex) |
| `question` | Market question text |
| `city` | Parsed + normalized from question (`New York City`, `London`, ...); blank for non-city markets |
| `weather_tags` | Comma-separated tag labels |
| `volume_gamma` | `volumeNum` from Gamma (combined CLOB + AMM, USD notional) |
| `liquidity_gamma` | `liquidityNum` from Gamma |
| `best_bid`, `best_ask`, `spread`, `last_trade_price` | Top-of-book snapshot at fetch time |
| `order_price_min_tick_size` | Minimum price increment (needed for fill modeling) |
| `order_min_size` | Minimum order size (needed for position sizing) |
| `neg_risk` | NegRisk CTF Exchange flag (true for effectively all modern weather markets) |
| `active`, `closed` | Market status |
| `created_at`, `end_date` | ISO 8601 timestamps |
| `resolution_source` | URL or identifier where the outcome is determined (e.g. `wunderground.com/.../KNYC` or `KLGA`) |
| `group_item_title` | Polymarket's own strike-ladder group label (e.g. `60°F`, `80-81°F`) |
| `clob_token_ids` | JSON array `[YES_token_id, NO_token_id]` — **the join key to the fills table**; see [[2026-04-11 Polymarket schema corrections]] |
| `outcomes` | JSON array of outcome names |

## Important coverage gap

The `Daily Temperature` tag (`103040`) was created **2025-12-31** on the Polymarket side and is **not retroactive**. Markets that resolved before that date are untagged and do **not** appear in this catalog. If we later need pre-tag-era historical weather markets, reingest the `jon-becker/prediction-market-analysis` Parquet dataset once for template matching, then delete it again.

## Why the CSV is committed despite the "no data in git" rule

`weather-market-slugs/` is an explicit carveout from the data-conventions rule that gitignores `data/`. The catalog is small (~8 MB), semi-permanent, and serves as a source-of-truth identifier list that every downstream script depends on. Documented in the "Slug-catalog carveout" bullet of `CLAUDE.md` Data conventions.

## Consumers

- `scripts/polymarket_weather/download.py` — reads the catalog and pulls per-slug Gamma + fills
- `scripts/polymarket_weather/validate.py` — schema sanity checks against the catalog
- `scripts/polymarket_weather/transform.py` — raw JSON → Parquet, iterates the catalog
- Any future backtester or live trader

## When to re-run the fetcher

**Rarely.** Re-run only when:

- Polymarket adds new weather tags you want to include
- New markets have been created upstream that you want to backfill
- The classification logic in the fetcher changed (e.g. city extraction regex)

After re-running, `git diff weather-market-slugs/polymarket.csv` shows the delta; review before committing.

## Related

- [[Polymarket]] — the venue the catalog covers
- [[2026-04-11 Polymarket schema corrections]] — gotchas about the fields returned by the Gamma API

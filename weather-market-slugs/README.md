# `weather-market-slugs/` — committed source of truth

This directory holds the **authoritative slug catalog** of weather markets
on Polymarket (and, in the future, Kalshi).  The CSV files here are
**committed to git** and serve as the stable input for every downstream
script in the repo: downloaders, transforms, backtests, live traders.

> **Note on the "never commit CSV" rule in `CLAUDE.md`.**  The data
> conventions in `CLAUDE.md` say never commit CSV/Parquet/etc.  That rule
> applies to bulk data under `data/`.  The slug catalog is an explicit
> override because it is small (< 10 MB), semi-permanent, and serves as a
> source-of-truth identifier list that every downstream script depends on.

## Files

| File | Rows | Description |
| --- | --- | --- |
| `polymarket.csv` | ~15,381 | All markets carrying a Polymarket Gamma weather tag |

## How it is produced

`scripts/fetch/polymarket_weather_slugs/script.py` queries the Polymarket
Gamma API for 6 weather tag IDs, dedupes by `condition_id`, extracts a
normalized `city` field from the question text, and writes the result
here.  See
[`scripts/fetch/polymarket_weather_slugs/README.md`](../scripts/fetch/polymarket_weather_slugs/README.md)
for the full fetch logic and the 6 tag IDs.

## When to re-run the fetcher

**Rarely.**  This is a one-time artifact checked into git.  Re-run only
when you want to refresh the catalog:

- New markets have been created upstream that you want to backfill
- Polymarket added new weather tags you want to include
- The classification logic in the fetcher changed

After re-running, `git diff weather-market-slugs/polymarket.csv` shows the
delta and you can review before committing.

## Known coverage gap

The `Daily Temperature` tag (103040) was created **2025-12-31** and is not
retroactive.  Markets resolved before that date are untagged and **do not
appear here**.  If we later need pre-tag-era historical weather markets,
we would need to bring back jon-becker's parquet dataset for one-time
template matching, then delete it again.

## Schema — `polymarket.csv`

| Column | Notes |
| --- | --- |
| `slug` | Market URL slug (unique key) |
| `condition_id` | On-chain condition ID (0x-prefixed hex) |
| `question` | Market question text |
| `city` | Parsed + normalized from question (`New York City`, `London`, etc.); blank for non-city markets |
| `weather_tags` | Comma-separated tag labels |
| `volume_gamma` | `volumeNum` from Gamma (single combined CLOB + AMM volume, in USD notional) |
| `liquidity_gamma` | `liquidityNum` from Gamma |
| `best_bid`, `best_ask`, `spread`, `last_trade_price` | Top-of-book snapshot at fetch time |
| `order_price_min_tick_size` | Minimum price increment — needed for fill modeling |
| `order_min_size` | Minimum order size — needed for position sizing |
| `neg_risk` | NegRisk CTF Exchange flag (true for almost all modern weather markets) |
| `active`, `closed` | Market status |
| `created_at`, `end_date` | ISO 8601 timestamps |
| `resolution_source` | URL or identifier where the outcome is determined (e.g. `https://www.wunderground.com/history/daily/us/ny/new-york-city/KNYC`) |
| `group_item_title` | Polymarket's own strike-ladder group label (e.g. `60°F`, `80-81°F`) |
| `clob_token_ids` | JSON array `[YES_token_id, NO_token_id]` |
| `outcomes` | JSON array of outcome names |

## Consuming this file

```python
import pandas as pd
df = pd.read_csv("weather-market-slugs/polymarket.csv")

# All NYC markets
nyc = df[df["city"] == "New York City"]

# All city daily-temperature markets only (exclude hurricanes/floods/etc.)
daily = df[df["weather_tags"].str.contains("Daily Temperature", na=False)]

# Only actively-tradeable markets
live = df[(df["active"] == True) & (df["closed"] == False)]
```

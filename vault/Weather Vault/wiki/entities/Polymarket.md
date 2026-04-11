---
tags: [entity, market-venue, prediction-market]
date: 2026-04-11
related: "[[Kalshi]], [[KLGA]], [[Polymarket weather market catalog]], [[2026-04-11 Polymarket schema corrections]], [[Project Scope]]"
---

# Polymarket

On-chain binary prediction market on Polygon. Permissionless trading in principle (US access is restricted at the venue level). Binary outcome tokens via the Conditional Token Framework (CTF). One of two venues we target for daily weather markets — the other is [[Kalshi]].

## Contract variants

Polymarket has two CTF exchange contracts indexed in the same Goldsky subgraph endpoint:

- **`CTF Exchange`** — original single-outcome binary markets
- **`NegRisk CTF Exchange`** — newer multi-outcome-as-binaries format (strike ladders, etc.)

**Weather markets on Polymarket are overwhelmingly NegRisk** — verified 574/574 (100%) for NYC. Any modern daily-temperature or weather-event market is a NegRisk market. The base CTF Exchange is legacy territory.

## NYC weather market resolution

Polymarket resolves "highest temperature in NYC on DATE" contracts against **[[KLGA]]** (LaGuardia) via Weather Underground's LaGuardia Airport Station feed, rounded to whole degrees F, revisions after finalization ignored. This is **different** from [[Kalshi]], which resolves the same-sounding market against [[KNYC]] (Central Park).

**Always verify the resolution station per market.** The venue name alone doesn't tell you which physical weather station the contract resolves against.

## Data we pull

- **Market metadata** — Polymarket Gamma API at `https://gamma-api.polymarket.com/markets?...`, one JSON object per market, ~80-90 fields per response
- **Trade fills** — Goldsky orderbook subgraph `OrderFilledEvent` index, paginated GraphQL, per CLOB token ID, indexes both base CTF and NegRisk events in the same endpoint
- **Slug catalog** — `weather-market-slugs/polymarket.csv` at repo root, refreshed by `scripts/polymarket_weather_slugs/download.py`. See [[Polymarket weather market catalog]].

## Key schema gotchas

Full list in [[2026-04-11 Polymarket schema corrections]]. Highlights:

- `clob_token_ids` is a JSON-string field **not mentioned** in the upstream `jon-becker/prediction-market-analysis` `docs/SCHEMAS.md` — it is the **join key** from markets → trades and without it the join is impossible
- Trade `timestamp` column exists but is **always null** — wall-clock time must be derived via `block_number` → `blocks` table lookup
- Asset-ID fields are `string` (256-bit ERC-1155 hashes), not `int` as the upstream docs claim
- `outcome_prices` is a JSON-encoded string, not a float array; can contain absurd-precision values like `"0.0000001761935909832323804205989381462587"`
- ~10% of markets have null `created_at`; ~1.5k have null `end_date`
- Tag 103040 (`Daily Temperature`) was created **2025-12-31** and is **not retroactive** — pre-tag-era markets don't appear in the native Gamma fetch

## Used in this repo

- `scripts/polymarket_weather/download.py` — per-slug Gamma + Goldsky pulls into `data/raw/polymarket_weather/{gamma,fills}/<slug>.json`
- `scripts/polymarket_weather/validate.py` — schema sanity checks on the raw pulls
- `scripts/polymarket_weather/transform.py` — raw JSON → Parquet (markets, fills, prices) under `data/processed/polymarket_weather/`
- `scripts/polymarket_weather_slugs/download.py` — refreshes the slug catalog (see [[Polymarket weather market catalog]])

## Rate limits / pagination

- Goldsky subgraph paginates `OrderFilledEvents` at 1000 per page; walk forward with `skip=0, 1000, 2000, …`, stop when a page returns < 1000 rows
- Space page requests by **200 ms**; serialize across tokens, don't parallelize
- Gamma API has been stable at conservative request rates — no documented hard limit but be polite

## Related

- [[Kalshi]] — the other venue for NYC weather markets, different resolution station
- [[KLGA]] — LaGuardia, Polymarket's NYC resolution station
- [[Polymarket weather market catalog]] — the committed slug catalog that every Polymarket script keys off
- [[2026-04-11 Polymarket schema corrections]] — the full schema gotcha list with upstream-doc corrections
- [[Project Scope]] — primary venue in the trading target set

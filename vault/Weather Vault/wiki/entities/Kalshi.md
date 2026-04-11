---
tags: [entity, market-venue, prediction-market]
date: 2026-04-11
related: "[[Polymarket]], [[KNYC]], [[Project Scope]], [[Execution Stack — Source Review]]"
---

# Kalshi

CFTC-regulated US-based prediction market. Centralized limit-order-book (CLOB) venue — not on-chain. Binary event contracts trading at $0–$1 with whole-cent ticks. One of two venues we target for daily weather markets; the other is [[Polymarket]].

## NYC weather market resolution

Kalshi resolves "highest temperature in NYC on DATE" contracts against the **NWS Daily Climate Report** (product code `CLI`) for NYC, which reports the observation from [[KNYC]] (Central Park). Uses **Local Standard Time** day boundary — during Daylight Saving Time the "day" is 1:00 AM – 12:59 AM the next day.

This is **different** from [[Polymarket]], which resolves the same-sounding market against [[KLGA]] (LaGuardia). Always verify which station a given market resolves against.

Sources:
- Kalshi weather-market help page: `help.kalshi.com/markets/popular-markets/weather-markets`
- NWS CLI product for NYC: `forecast.weather.gov/product.php?site=NWS&issuedby=NYC&product=CLI`

## Liquidity observation

Per [[Project Scope]], Kalshi's NYC daily-high market had ~160k volume on a single day in early April 2026 — comparable to [[Polymarket]]'s ~111k for LA daily-high the same week. Both venues have real size on US city temperature contracts. Kalshi appears to be the deeper venue for US city weather.

## Tick and contract mechanics

- **Tick size:** $0.01 (one cent). The one-tick adverse-move fill model from `evan-kolberg/prediction-market-backtesting` uses this as the Kalshi default — see [[Execution Stack — Source Review]].
- **Fee model:** per-contract fee structure documented in the adapter referenced above. Not yet ported into this repo.

## Data we pull

**Not yet.** No Kalshi downloader in the repo as of 2026-04-11. Planned:

- `scripts/kalshi_weather/download.py` using Kalshi's REST API for live markets + trade history
- Fee model will be ported from `evan-kolberg/prediction-market-backtesting`'s `adapters/kalshi/fee_model.py`

Until this lands, [[Polymarket]] is the only venue with actual pulls in `data/raw/`.

## Related

- [[Polymarket]] — the other venue for NYC weather markets, uses a different resolution station
- [[KNYC]] — Central Park, Kalshi's NYC resolution station
- [[Project Scope]] — primary venue in the trading target set
- [[Execution Stack — Source Review]] — Kalshi fee-model + backtesting adapter reference

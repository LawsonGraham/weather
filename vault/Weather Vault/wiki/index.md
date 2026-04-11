# Wiki Index

> Catalog of every page in the wiki. Organized by type, with a one-line description each. Updated by `vault-capture` whenever new knowledge lands; read by `vault-seed` at session start.

## Entry points

- [[Project Scope]] — canonical scoping doc (top-level, not under `wiki/`)
- [[Execution Stack — Source Review]] — evaluation of three open-source prediction-market repos and the chosen execution-stack architecture (top-level, not under `wiki/`)

## Syntheses

_Cross-source analyses live in `wiki/syntheses/`. Written by `vault-capture` when a decision, gotcha, or lesson is worth preserving across sessions._

- [[2026-04-11 Near-resolution ladder-bid arbitrage]] — ✅ VERIFIED EDGE (capacity revised DOWN): the ladder-bid arb is real, but pre-resolution hours have an active MM correcting deviations in <1s. Real capacity is ~$5-15/day NYC at final-hour-only execution — expN first live alert caught a 1-second arb and discovered the active MM pattern.
- [[2026-04-11 Strategy D V1 real-ask cost + pre-entry pump]] — ⚠️ PARTIALLY RETRACTED by expL: the pump is on the FAVORITE (not +2), Strategy D V1 catches a local bottom on the +1 bucket, and V2 at 15:30 EDT is WORSE than V1. Real-ask premium (3-18% above backtest) still holds. NEW: the 18c favorite pump at 15:55 EDT looks HRRR-cycle-driven — potential edge.
- [[2026-04-11 Real-book replay invalidates sell-pop edge]] — ⚠️ corrects the mean-reversion synthesis below: real taker PnL -7.7c/trade; the midpoint "reversion" was a spread-width artifact. Also surfaced the ladder-bid-sum arb candidate (since verified — see above)
- [[2026-04-11 Asymmetric mean reversion edge]] — UP moves mean-revert 40% in 10 min, DOWN moves don't; sell-3c-pop at midpoint wins 65% / +1.9c (INVALIDATED for taker execution — see real-book replay synthesis)
- [[2026-04-11 First pass 1-min price data exploration]] — first exploratory pass on the brand-new 1-min Polymarket price data: ladder overround structure, volatility concentration, evening-before-resolution info peak, three candidate naive edges
- [[2026-04-11 Polymarket schema corrections]] — undocumented Gamma / Goldsky subgraph schema gotchas discovered while building the native Polymarket downloader

## Entities

_Named things that matter to the project: airports, markets, providers, competitors, models. Live in `wiki/entities/`._

- [[IEM]] — Iowa Environmental Mesonet; hosts the ASOS 1-minute archive used for Layer 1 ground truth
- [[Kalshi]] — CFTC-regulated US prediction market; NYC weather markets resolve against [[KNYC]]
- [[KLGA]] — LaGuardia Airport; NYC-area ASOS 1-minute site; [[Polymarket]] NYC resolution station
- [[KNYC]] — Central Park NWS first-order climate station; has 1-minute data; [[Kalshi]] NYC resolution station
- [[Polymarket]] — on-chain binary prediction market; NYC weather markets resolve against [[KLGA]]

## Concepts

_Ideas and methods: HRRR, MOS, TAF, calibration, Kelly sizing, ensemble spread, and so on. Live in `wiki/concepts/`._

- [[ASOS 1-minute]] — 1-minute-resolution surface weather observations; Layer 1 ground truth
- [[Data Validation]] — paranoid first-principles audit methodology for every data source; 6-level rigor ladder + historical bug record
- [[METAR]] — Layer 3 aviation routine + SPECI observations from IEM: schema, RMK-group decoding, market-relevance shortcuts
- [[Polymarket CLOB WebSocket]] — live L2 book + price_change + last_trade_price stream; the only way to get book depth and real-time fills
- [[Polymarket prices_history endpoint]] — CLOB `/prices-history` REST endpoint; hourly midpoint for all markets, 1-min for open markets (past 24 h)
- [[Polymarket weather market catalog]] — the committed slug catalog at `weather-market-slugs/polymarket.csv` that every Polymarket script keys off

## How this index works

- Every wiki page must be linked here. If a page isn't in the index, it's effectively orphaned.
- Each entry: `- [[path/to/page]] — one-line description`
- Keep entries alphabetical within each section.
- `vault-capture` maintains this file when new pages land.

# Wiki Index

> Catalog of every page in the wiki. Organized by type, with a one-line description each. Updated by `vault-capture` whenever new knowledge lands; read by `vault-seed` at session start.

## Entry points

- [[Project Scope]] — canonical scoping doc (top-level, not under `wiki/`)
- [[Execution Stack — Source Review]] — evaluation of three open-source prediction-market repos and the chosen execution-stack architecture (top-level, not under `wiki/`)

## Syntheses

_Cross-source analyses live in `wiki/syntheses/`. Written by `vault-capture` when a decision, gotcha, or lesson is worth preserving across sessions._

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
- [[Polymarket weather market catalog]] — the committed slug catalog at `weather-market-slugs/polymarket.csv` that every Polymarket script keys off

## How this index works

- Every wiki page must be linked here. If a page isn't in the index, it's effectively orphaned.
- Each entry: `- [[path/to/page]] — one-line description`
- Keep entries alphabetical within each section.
- `vault-capture` maintains this file when new pages land.

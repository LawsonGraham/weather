---
tags: [entity, station, nyc, resolution-source]
date: 2026-04-11
related: "[[IEM]], [[Kalshi]], [[ASOS 1-minute]], [[KLGA]]"
---

# KNYC — Central Park (New York, NY)

The official NWS first-order climate station for New York City, located in Central Park. **Not an airport** — it's a manual + automated station in the park, staffed by the NWS and reporting into the Daily Climate Report (CLI product). Station ID `NYC` in IEM's network (no `K` prefix — use bare `NYC`).

## Surprising finding: has 1-minute data

Contrary to the expectation that KNYC is "hourly-only" because it's a manual station, **it IS in the [[IEM]] ASOS 1-minute archive.** Site metadata reports `HAS1MIN=1` for `NYC` on the `NY_ASOS` network; a live request returned 1,440 rows for 2026-04-01 (i.e. one sample per minute for the full day). Use bare station code `NYC`.

Station metadata page: `mesonet.agron.iastate.edu/sites/site.php?station=NYC&network=NY_ASOS`

## Role in this project

- **[[Kalshi]] NYC weather market resolution source.** Kalshi resolves "highest temperature in NYC" contracts against the NWS Daily Climate Report (CLI) for NYC, sourced from this station. Local Standard Time day boundary.
- **1-minute ASOS data available** for the full Layer 1 training window, pulled via `scripts/iem_asos_1min/download.py --stations NYC ...`
- **NOT used by [[Polymarket]]** — Polymarket uses [[KLGA]] (LaGuardia). When trading the "same" NYC daily-high market on Polymarket vs Kalshi, you are trading against **different physical temperature sensors** miles apart with different microclimates. Always use the right station per venue.

## Urban microclimate note

Central Park is surrounded by dense urban terrain and tends to run warmer in summer than the surrounding airports — significant urban heat island effect. Per NOAA climate studies, Central Park can be 2–5°F warmer than the coastal NYC airports on summer afternoons. This matters for cross-venue arbitrage: the Polymarket (LaGuardia) and Kalshi (Central Park) contracts are NOT mechanically linked, and the station differential itself is a tradeable signal.

## Data pulled in this repo

`data/raw/iem_asos_1min/NYC/*.csv` — 11 monthly CSVs covering 2025-06 through 2026-04 (per the last download run). See `data/raw/iem_asos_1min/MANIFEST.json` for the canonical manifest.

## Related

- [[IEM]] — data provider
- [[Kalshi]] — resolution venue that uses this station
- [[KLGA]] — the sibling NYC-area station used by [[Polymarket]]
- [[ASOS 1-minute]] — the dataset type

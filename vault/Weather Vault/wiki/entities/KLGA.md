---
tags: [entity, airport, station, nyc, resolution-source]
date: 2026-04-11
related: "[[IEM]], [[Polymarket]], [[ASOS 1-minute]], [[KNYC]]"
---

# KLGA — LaGuardia Airport (Queens, NY)

New York City airport station, ICAO `KLGA`, bare code `LGA` in the [[IEM]] network. Standard automated ASOS site with continuous 1-minute observations. Located ~8 miles east of Central Park.

## Role in this project

- **[[Polymarket]] NYC weather market resolution source.** Polymarket resolves "highest temperature in NYC on DATE" contracts via Weather Underground's LaGuardia Airport Station feed, rounded to whole degrees F, with revisions after finalization ignored. Market rules source: the polymarket.com per-market rules text.
- **Best airport proxy for Central Park** among NYC-area stations. LaGuardia typically tracks [[KNYC]] within 1–2°F on most days — closer than KJFK (coastal sea-breeze cooling in summer) or KEWR (Newark urban/industrial microclimate differences).
- **NOT used by [[Kalshi]]** — Kalshi uses [[KNYC]] (Central Park). The two venues trade contracts with the same wording but different physical resolution sensors.

## Data pulled in this repo

`data/raw/iem_asos_1min/LGA/*.csv` — 11 monthly CSVs covering 2025-06 through 2026-04. Pulled via `scripts/iem_asos_1min/download.py --stations LGA ...` (bare code `LGA`, no `K` prefix).

## Cross-station delta vs [[KNYC]]

On typical days LaGuardia is within 1–2°F of Central Park. On summer afternoons with strong urban heat island effect, Central Park can run 2–5°F warmer than LaGuardia — meaningful for cross-venue arbitrage between [[Polymarket]] and [[Kalshi]] contracts with the same wording. The station differential itself may be a tradeable signal.

## Related

- [[IEM]] — data provider
- [[Polymarket]] — resolution venue that uses this station
- [[KNYC]] — the sibling NYC-area station used by [[Kalshi]]
- [[ASOS 1-minute]] — the dataset type

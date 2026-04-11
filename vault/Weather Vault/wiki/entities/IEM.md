---
tags: [entity, provider, weather-data]
date: 2026-04-11
related: "[[ASOS 1-minute]], [[KNYC]], [[KLGA]], [[Project Scope]]"
---

# IEM — Iowa Environmental Mesonet

Iowa State University mesonet service hosting authoritative US weather observation archives. Operated as a public good — free, no authentication, minimal rate limits. The primary source for historical 1-minute ASOS data and decoded METAR reports in this project.

## Endpoints used

- **ASOS 1-minute** — `https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py`
  - Station codes: **bare ICAO without the `K` prefix** (use `JFK`, `NYC`, `LGA` — not `KJFK`)
  - Dates: `sts=YYYY-MM-DDTHH:MMZ` and `ets=...` (ISO-8601 UTC)
  - Variables: `vars=tmpf&vars=dwpf&vars=sknt&...` (repeatable)
  - Output: `what=download&delim=comma`
  - Sample: `sample=1min`
  - Append `?help` to the endpoint URL for the full parameter reference
- **METAR archive** — `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py` (hourly + SPECI special reports)
- **Station metadata** — `https://mesonet.agron.iastate.edu/sites/site.php?station=<ID>&network=<NET>` (flags like `HAS1MIN=1` live here)

## Lag and availability

~24 hours behind real time for the ASOS endpoints (documented via `?help`). Archive coverage back to ~2000 for 1-minute data. Decades of decoded METAR for most stations.

## Surprising finding: KNYC has 1-minute data

Central Park ([[KNYC]]) is a manual NWS station, not an airport — but it IS in the IEM ASOS 1-minute network. Verified via `HAS1MIN=1` on the site metadata and a live request returning 1,440 rows for a single day. Use bare code `NYC`.

## Rate limits / politeness

No hard rate limit documented, but IEM throttles concurrent connections per IP. **Serialize stations; don't parallelize downloads.** Space requests naturally via the monthly-chunk pattern in the downloader.

## Used in this repo

- `scripts/iem_asos_1min/download.py` — monthly CSV pulls per station, writes `data/raw/iem_asos_1min/<STATION>/<YYYY-MM>.csv` + `MANIFEST.json`
- Currently pulling `NYC` (Central Park, for [[Kalshi]] resolution) and `LGA` (LaGuardia, for [[Polymarket]] resolution), 11 months each

## Related

- [[ASOS 1-minute]] — the dataset type we pull from IEM
- [[KNYC]] — Central Park, resolves [[Kalshi]] NYC markets
- [[KLGA]] — LaGuardia, resolves [[Polymarket]] NYC markets
- [[Project Scope]] — data-stack Layer 1 (ground truth)

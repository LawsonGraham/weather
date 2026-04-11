---
tags: [concept, weather-data, ground-truth]
date: 2026-04-11
related: "[[IEM]], [[KNYC]], [[KLGA]], [[Project Scope]]"
---

# ASOS 1-minute

**Automated Surface Observing System** 1-minute-resolution observation feed. The finest-temporal-resolution surface weather observation dataset available for US stations, sourced from the ~900 ASOS sites at major airports and select NWS stations. Updated every minute; lagged ~24 hours in the [[IEM]] archive.

## Role in this project

**Layer 1 of the 6-layer data stack per [[Project Scope]]** — the ground-truth `y` against which we train forecasts and resolve trades. Every point-forecast bias-correction model in this project is ultimately supervised by ASOS 1-minute observations at the target station.

## Variables available

| Variable | Meaning | Unit |
|---|---|---|
| `tmpf` | Air temperature | °F |
| `dwpf` | Dew point temperature | °F |
| `sknt` | Wind speed (2-minute average) | knots |
| `drct` | Wind direction | degrees |
| `gust_sknt` | 5-second peak wind gust | knots |
| `gust_drct` | Direction of peak gust | degrees |
| `pres1`, `pres2`, `pres3` | Station pressure (3 sensors) | inHg |
| `precip` | 1-minute precipitation accumulation | inches |
| `ptype` | Precipitation type | categorical (rain/snow/freezing) |
| `vis1_coeff`, `vis1_nd`, ..., `vis3_*` | Visibility coefficient + nominal distance (3 sensors) | various |

## Quirks and gotchas

- **Station codes are bare** — no `K` prefix. Use `JFK` not `KJFK`, `NYC` not `KNYC`, `LGA` not `KLGA`.
- Variables reported every 1 minute for most channels. Wind is a 2-minute average with the 5-second peak gust reported separately in `gust_sknt` / `gust_drct`.
- The [[IEM]] archive lags real time by **~24 hours**. For near-real-time trading, a different source path is needed (e.g. direct NOAA feeds or commercial aggregators).
- **Not every station in the IEM network has 1-minute data.** Sites that do are flagged `HAS1MIN=1` in the station metadata. Surprising finding: [[KNYC]] (Central Park) has 1-minute data despite being a manual NWS station.
- **No hard rate limit** documented by IEM, but they throttle concurrent connections per IP. Serialize stations — do not parallelize downloads.
- Monthly CSV files grow to ~5–10 MB per station per month depending on how many variables are requested.

## Request shape (via [[IEM]] CGI)

```
https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py
  ?station=NYC
  &sts=2025-06-01T00:00Z
  &ets=2025-07-01T00:00Z
  &vars=tmpf&vars=dwpf&vars=sknt&vars=drct&vars=gust_sknt
  &sample=1min
  &tz=UTC
  &what=download
  &delim=comma
```

Append `?help` (no other params) to the endpoint URL for the full parameter reference.

## Used in this repo

- `scripts/iem_asos_1min/download.py` — monthly CSV pulls per station. Writes `data/raw/iem_asos_1min/<STATION>/<YYYY-MM>.csv` + `MANIFEST.json`. Supports `--stations`, `--start`, `--end`, `--force`, `--fresh`, `--dry-run`. Uses the ICAO-to-bare-code alias map so `KNYC`/`KLGA` also work as input.
- Currently pulling `NYC` (Central Park, for [[Kalshi]] resolution) and `LGA` (LaGuardia, for [[Polymarket]] resolution), 11 months each.

## Related

- [[IEM]] — the provider / archive hosting the data
- [[KNYC]] — Central Park ASOS 1-minute site (manual NWS station, surprisingly in the 1-min network)
- [[KLGA]] — LaGuardia ASOS 1-minute site (standard automated airport)
- [[Project Scope]] — Layer 1 ground-truth source in the 6-layer data stack

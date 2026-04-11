---
tags: [scope, core]
---

# Project Scope

> Core scoping document for the weather forecasting project. Source: imported from Notion, 2026-04-10.

## 1. Who currently has the best forecasting

- Aviation
- Energy
- Govtech

## 2. Where is the data

### Tier 1

- [Tomorrow.io Weather API](https://www.tomorrow.io/weather-api/) — big hub, useful to scope which data seems valuable; tackle raw sources for historical data
- [NOAA NCEI Access Data Service](https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation)
- [IEM ASOS 1-minute](https://mesonet.agron.iastate.edu/request/asos/1min.phtml) — free historical, located at airports (which is where UMA resolves to)
- [Synoptic Data](https://synopticdata.com/)

### Tier 2

- Wunderground?
- [AccuWeather Developer](https://developer.accuweather.com/home)
- [XWeather](https://signup.xweather.com/)
- [weather-milliseconds (GitHub)](https://github.com/JulianNorton/weather-milliseconds)
- [PRISM (Oregon State)](https://prism.oregonstate.edu/)

### Simple Plan

1. **IEM ASOS** — tick-level temps historically
2. **HRRR NWP Model** (public good) — raw use or training data
    - `s3://noaa-hrrr-bdp-pds/`
    - https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/
3. **IEM METAR** — deeper context data
    - https://mesonet.agron.iastate.edu/request/download.phtml

---

## Claude-Suggested Plan

### Layer 1 — Ground Truth (Target Variable `y`)

**IEM ASOS 1-Minute**

- **What:** Physical sensor measurements at airport, 1-min resolution
- **Variables:** temp, dew point, wind speed/dir, precip accumulation, pressure, visibility
- **History:** Back to 2000, updated daily with ~24hr delay
- **Access:** https://mesonet.agron.iastate.edu/request/asos/1min.phtml
- **Scriptable:** https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py
- **Format:** CSV, free, no auth
- **Stations:** ICAO codes — `KSFO`, `KJFK`, `KORD`, `KDFW`, `KLAX`

### Layer 2 — NWP Model Output (Strongest Predictor Features `X`)

**NOAA HRRR — primary**

- **What:** Physics-based atmospheric simulation, 3km grid, CONUS
- **Variables:** temp, dew point, wind, precip rate, cloud cover, visibility, CAPE, simulated radar reflectivity, pressure
- **Update cycle:** Every 1 hour, 18hr horizon (48hr 4x/day)
- **Historical archive:** `s3://noaa-hrrr-bdp-pds/` — back to July 2014, free
- **Real-time:** https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/
- **Format:** GRIB2 — use **Herbie** (`pip install herbie-data`) to extract by lat/lon without downloading full files
- **Key insight:** Extract only the grid cell nearest each target airport — don't pull full domain

**NOAA GFS — secondary (longer horizon)**

- **What:** Global model, 13km, 16-day horizon
- **Use for:** Features beyond HRRR's 18hr window
- **Archive:** `s3://noaa-gfs-bdp-pds/`

**NOAA RAP — tertiary (rapid update, coarser)**

- **What:** 13km, hourly updates, 21hr horizon — HRRR's coarser sibling
- **Use for:** Cross-validation, gap filling
- **Archive:** `s3://noaa-rap-pds/`

### Layer 3 — METAR Observations (Rich Qualitative Features)

**IEM METAR Archive**

- **What:** Hourly + special (SPECI) aviation weather reports — decoded
- **Variables beyond ASOS:** sky condition layers + heights, ceiling, present weather type codes (RA/SN/FG/TS), pressure tendency, hourly precip total, thunderstorm begin/end times, peak wind
- **History:** Decades, global coverage
- **Access:** https://mesonet.agron.iastate.edu/request/download.phtml
- **Scriptable:** `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=KSFO&data=all&...`
- **Parser:** `pip install metar` — handles full string + remarks decoding
- **Key fields:** `SLP`, `TT` (precise temp), `PK WND`, `PRESRR/PRESFR`, `RAB/TSB`, `P` (precip amount)

### Layer 4 — Upstream Spatial Observations (Mesoscale Context)

**Synoptic Data API**

- **What:** 170k+ stations, 320 networks, aggregated + QC'd — road sensors, university nets, mesonet, RWIS
- **Why:** Weather at your airport in 3–6 hours is partly determined by what's happening 100–300 miles upwind *right now*. Single-station prediction misses this entirely.
- **Query pattern:** radius search around each airport, pull all reporting stations
- **Variables:** temp, wind, pressure, precip, humidity — 160 total
- **Resolution:** 5-min for most stations, some 1-min
- **History:** 1 year on base tier, more on commercial
- **Access:** https://api.synopticdata.com/v2/stations/timeseries
- **Pricing:** Free tier exists, paid for scale
- **Python:** `pip install SynopticPy`

### Layer 5 — Radar (Precipitation + Convection Nowcasting)

**NOAA NEXRAD Level 2**

- **What:** WSR-88D radar network, ~5 minute scan cycle, reflectivity + velocity
- **Why:** Best signal for precipitation onset, intensity, and storm approach — essential for 0–3hr convective nowcasting
- **Archive:** `s3://noaa-nexrad-level2/` — free, back to 1991
- **Real-time:** `s3://unidata-nexrad-level2-chunks/` — chunked live feed
- **Parser:** `pip install nexradaws arm-pyart` — PyART handles GRIB/radar formats
- **Key derived products:** composite reflectivity, storm motion vectors, echo tops

### Layer 6 — TAF — Aviation Forecast Baseline (Benchmark)

**Terminal Aerodrome Forecasts**

- **What:** Official NWS 24–30 hour aviation forecasts issued every 6 hours, specific to each airport
- **Why:** This is the **benchmark you need to beat**. TAFs are produced by trained NWS meteorologists using the same NWP models + local knowledge. If your model can't beat TAF skill, it's not useful.
- **Access:** https://aviationweather.gov/api/data/taf?ids=KSFO&format=json
- **Historical archive:** Iowa State IEM also archives TAFs
- **Use as:** Both a feature (what did NWS forecast?) and evaluation baseline

### How the Layers Relate

```
                    TRAINING                          INFERENCE

Layer 5  NEXRAD ────► radar features (0-3hr)
Layer 4  Synoptic ───► upwind obs features
Layer 3  METAR ─────► current conditions features
Layer 2  HRRR ─────► NWP forecast features
         ↓                                               ↓
Layer 1  ASOS 1-min ──► y (ground truth)        ► predicted y
                              ↓
Layer 6  TAF ───► benchmark to beat
```

### Suggested Build Order

```
Phase 1 — Baseline
  Pull ASOS 1-min + HRRR for 1 target airport, 1 year
  Train simple bias-correction model (XGBoost)
  Evaluate vs TAF → establish benchmark gap

Phase 2 — Enrich features
  Add METAR sky/ceiling/present weather
  Add pressure tendency signals
  Evaluate improvement

Phase 3 — Spatial context
  Add Synoptic upwind observations
  Evaluate improvement on convective events specifically

Phase 4 — Nowcast layer
  Add NEXRAD for 0-3hr precipitation prediction
  SPECI change-point signals

Phase 5 — Scale
  Expand to all target airports
  Per-airport models vs shared model decision
```

---

## 3. Scope of Opportunity

- **LA daily — 111k volume** — [Polymarket: Highest temperature in Los Angeles on April 7, 2026](https://polymarket.com/event/highest-temperature-in-los-angeles-on-april-7-2026)
    - Kalshi at 160k for today
- **Shanghai daily — 150k volume** — [Polymarket: Highest temperature in Shanghai on April 9, 2026](https://polymarket.com/event/highest-temperature-in-shanghai-on-april-9-2026)

### Simple Math on Resolutions

> [!todo] Placeholder — content to be filled in. This is the actual scoping of the project.

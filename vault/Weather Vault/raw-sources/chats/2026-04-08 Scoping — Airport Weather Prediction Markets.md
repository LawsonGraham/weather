---
tags: [research-chat, scoping, prediction-markets, data-sources]
date: 2026-04-08
source: Cursor / Claude chat
related: "[[Project Scope]]"
---

# Scoping Chat — Airport Weather Prediction Markets

> Full research conversation that produced the project scoping. The critical reframe happens in the final section: this is a **prediction-market trading problem**, not a general weather-forecasting problem. The whole stack and evaluation target shifts accordingly.

## TL;DR — Key Conclusions

- **Target use case**: trading daily weather markets on Kalshi (and similar). Binary contracts like "Will NYC daily high exceed 72°F on April 9?"
- **Core alpha is calibration, not accuracy**: you only need to be more right than the market, not perfectly right. Output `P(high > threshold)`, not point forecasts.
- **Competition on Kalshi weather is softer than financial markets**: ~40% casual retail, ~30% API consumers (Tomorrow.io/AccuWeather), ~20% simple NWS MOS users, ~10% genuinely quantitative. Very few running the full stack.
- **Highest-alpha markets**: precipitation occurrence and precipitation timing (NWP models weakest here, competition thinnest). Daily-high temperature is more crowded/efficient.
- **Single highest-value upgrade vs a deterministic pipeline**: use **HRRR ensemble (HRRRx)** — 36 free members = free empirical distribution to price binaries against.
- **Edge comes from**: (1) calibrated probabilities, (2) knowing when you have edge vs when to sit out, (3) reacting to new HRRR runs faster than the market reprices (~15–45 min window), (4) regime-aware models (marine layer, convective suppression, cold-front timing).
- **Data costs ~$0 for Phases 1–2.** Only real line item is Synoptic (~$500–1k/mo) and it's deferrable to Phase 3. ECMWF commercial ($50k+/yr) is not needed for same-day/next-day use cases — HRRR already wins there.
- **You should NOT be comparing yourself to Tomorrow.io** for generic-location forecasts. You target specific named airports with decades of labeled ground truth — that's the one domain where a specialist learned model beats a generic API.

---

## Q1 — What industry relies most on the weather?

> I am including news/reporting. I'm trying to get an idea of what the most critical industry is regarding modeling the weather.

Strongest case: **energy**, with agriculture a very close second depending on framing.

- **🏆 Energy** — grid operators balance supply and demand in real time. Temperature drives heating/cooling load; wind and solar output depend directly on atmospheric conditions. A forecast miss can cause grid instability affecting millions. Duke Energy reported a 15% drop in running costs from more accurate forecasts. The dependency intensifies as renewables grow.
- **🌾 Agriculture** — largest workforce exposure (60% in low-income countries). ~$21.94B in 2023 crop/rangeland losses from significant natural disasters. Uses weather across the full crop lifecycle.
- **✈️ Aviation** — most operationally binary (a flight either goes or doesn't). Route planning, fuel load, and safety all hinge on forecast accuracy.
- **Other contenders**: insurance/reinsurance (prices catastrophic risk), shipping/maritime, construction, defense.

**Bottom line**: energy grid management edges out in systemic risk and dollar terms. Agriculture has more total exposure but more lead time. Aviation has zero tolerance for error but operates locally/short-range.

---

## Q2 — For the day-to-single-month range specifically, what industries?

Ranking shifts significantly from long-range:

- **⚡ Energy (1–30 days) — #1**. Day-ahead: demand forecasting, generation commitment, spot prices. 3–7 days: maintenance, fuel procurement. 2–4 weeks: reservoir management, gas storage. Errors in day-ahead forecasts directly move electricity prices.
- **✈️ Aviation (1–10 days) — #1 tie**. Almost exclusively operates in this window. Beyond ~10 days, forecasts aren't actionable.
- **🚢 Shipping & logistics (1–14 days)** — route optimization for maritime and trucking lives entirely in this window.
- **🏗️ Construction (1–14 days)** — pour scheduling, crane ops, scaffold safety.
- **🌾 Agriculture drops in priority** — most critical forecasts are seasonal (3–6 months out), not day-to-month.

**Clearest answer**: tight race between energy trading/grid ops and aviation. Energy edges it out because financial exposure is continuous (spot electricity markets price every 5–30 minutes), forecast errors propagate systemically, and both demand and supply (wind/solar) are weather-driven simultaneously.

---

## Q3 — Who actually does the modeling and collects weather data?

The *producers* of weather intelligence is a much smaller, more specialized set than the consumers.

- **🏛️ National met agencies (NOAA/NWS, ECMWF, Met Office, JMA)** — the foundation layer. Operate observational infrastructure (satellites, radiosondes, buoys, radar) and run global NWP models. Effectively a public good, heavily government funded.
- **✈️ Aviation — most proprietary operational modeling**. Airlines maintain dedicated meteorology departments. EUROCONTROL, FAA, and airline ops centers run their own nowcasting and route weather systems. The Weather Company (IBM) and Jeppesen exist almost entirely to serve aviation. Aviation drove investment in turbulence, icing, and convective nowcasting.
- **⚡ Energy — fastest-growing private capability**. ISOs (ERCOT, PJM), utilities, energy traders all run or license proprietary load/generation models. Hedge funds pay enormous sums for weather alpha. Atmospheric G2, Jupiter Intelligence, DTN built specifically for energy weather.
- **🌊 Maritime / oil & gas — deep offshore modeling**. StormGeo, BMT ARGOSS model waves, currents, storm tracks at resolution national agencies don't bother with.
- **🎖️ Military — historically biggest driver**. USAF Weather Agency, Navy Fleet Weather run independent global modeling. Funded much early NWP research.

Hierarchy:
```
National Agencies (NOAA, ECMWF)     ← observational data + global models
        ↓
Private Weather Companies (DTN,     ← downscaling + sector specialization
StormGeo, TWC)
        ↓
Industry Internal Teams (airline    ← last-mile proprietary modeling
met depts, energy trading desks)
```

---

## Q4 — For city-based weather modeling specifically, which industries align?

City-based (urban) modeling is a subdiscipline called **urban meteorology** or **urban microclimate modeling**. Dramatically narrows the field.

### Why city-scale is different

Standard NWP runs at 10–25km. Cities require **sub-kilometer or sub-100m** resolution. Urban environments create:
- Urban Heat Island (UHI) effect
- Wind channeling through street canyons
- Localized flooding from impervious surfaces
- Pollution/air quality dispersion tied to building geometry
- Microclimatic variation block-by-block

Requires different data (building heights, land cover, albedo) and different modeling approaches.

### Industries that actually need and build this

- **🚗 Ride-share / autonomous vehicles** — emerging. Waymo, Uber need hyperlocal real-time urban weather at intersection level.
- **⚡ Urban energy / utilities — #1 most established**. Block-by-block temperature variation for load forecasting. District heating/cooling operators need UHI-aware models. Con Edison, PG&E have funded urban meteorology for decades. Rooftop solar forecasting in dense cities requires building-shadow and canyon-wind modeling.
- **🏙️ City governments & urban planners — biggest institutional funders**. Heat emergency planning, flood risk, air quality. Singapore, London, NYC have invested heavily in digital twin weather-aware urban models.
- **🏗️ Real estate & urban construction** — wind tunnel CFD for pedestrian comfort and structural loads. Legally required in many cities before planning permission. Arup, RWDI do city-scale wind/thermal modeling.
- **📦 Last-mile logistics** — UPS, FedEx, Amazon use city-scale weather to route around localized flooding/icing.
- **🏥 Public health** — urban heat mortality modeling.

---

## Q5 — For 1–30 day urban forecasts, which industries have the MOST ACCURATE/ADVANCED models?

Honest answer: most industries are consumers, not producers. When you filter for *frontier capability*, the field collapses to a very small number of actors.

### Tier 1 — Frontier

- **🏛️ National met agencies (NOAA, ECMWF, Met Office, DWD)** — still undisputed on underlying physics. Met Office Unified Model and ECMWF IFS run at increasingly fine resolution with urban parameterization. NOAA's RRFS (Rapid Refresh Forecast System) designed for high-res CONUS including urban areas.
- **🎖️ Military (USAF, DARPA)** — quietly the most advanced in applied urban modeling. USAF Weather Agency runs COAMPS, funds research into urban canyon wind modeling for operational planning in cities. Access to reconnaissance and sensor data civilians don't have.

### Tier 2 — Sophisticated private capability

- **⚡ Energy sector (ISO/RTO operators + trading firms)** — ERCOT, PJM, MISO license extremely refined urban load-weather coupling. A 1°F error in a major metro = hundreds of megawatts of forecast error. Proprietary energy trading firms have built weather teams that rival small met agencies because forecast alpha is worth hundreds of millions.
- **🌍 Reinsurance (Swiss Re, Munich Re, RMS/Moody's, Verisk)** — often overlooked but genuinely at the frontier. Model urban weather at fine resolution for catastrophe risk pricing. A mis-modeled urban flood or heat event costs billions.

### Tier 3 — Catching up fast

- **🤖 Tech / AI weather startups** — Google DeepMind (GraphCast), Nvidia (FourCastNet), Huawei (Pangu-Weather). ML-based global models now competitive with or beating ECMWF on standard metrics — but not yet optimized for city-scale. Tomorrow.io is probably the most advanced pure-play commercial urban weather company.

| Rank | Actor | Why |
|---|---|---|
| 1 | ECMWF / Met Office / NOAA | Best global models, increasingly fine urban resolution, best data assimilation |
| 2 | Military (USAF/DARPA) | Most advanced applied urban canyon modeling, unique data access |
| 3 | Reinsurance (RMS/Verisk, Munich Re) | Best proprietary urban hazard models, billions in validation incentive |
| 4 | Energy ISOs + trading firms | Best urban temperature accuracy, financially incentivized at fine margins |
| 5 | AI labs (DeepMind, Nvidia) | Best ML skill scores globally, closing gap on urban resolution |

**Key takeaway**: the most accurate urban models are still inside national met agencies, but the most financially motivated private frontier is **reinsurance and energy trading** — because they lose real money when their models are wrong.

---

## Q6 — City/granular time-level data: what works at seconds-to-minutes resolution?

> I've found Tomorrow.io, NCEI, Wunderground, AccuWeather, xWeather, weather-milliseconds (GitHub), PRISM. What else? What works for city-level granular data?

### ✅ Tomorrow.io — best commercial option for city-level

- Query by lat/lng (any point in a city)
- Historical goes back ~4 years, API-accessible
- Timesteps: **1-minute for real-time, 1-hour for historical**
- 80+ data layers
- **Catch**: minute-level historical is expensive/enterprise tier. Free tier is hourly historical.

### ✅ NOAA ISD (via NCEI) — best free historical at scale

- 20k+ stations globally, hourly and sub-hourly surface observations
- ASOS stations in cities report every **5 minutes** with special reports on weather changes
- Temp, dew point, wind, pressure, visibility, precip, clouds
- Back to 1901 for some stations; reliable from 1970s+
- Available on AWS S3 as open data — bulk ingestible
- **Catch**: station-based not grid-based. You get the airport or official station, not a spatial grid.

### ⚠️ Wunderground (PWS Network) — highest density but messy

- 250k+ personal weather stations worldwide with 5-min data per station
- Densest urban coverage available — multiple stations per neighborhood
- **Catch 1**: historical data not easy to access programmatically; API blocks automated access.
- **Catch 2**: PWS quality is highly variable — consumer-grade hardware, inconsistent placement.
- Now owned by IBM/TWC; paywalled API.

### ⚠️ PRISM — wrong tool

- Grid-based climate at ~4km resolution, **daily is finest timestep**. Not useful for sub-hourly.

### ⚠️ AccuWeather / xWeather — forecast focused, historical expensive or redundant

### Missing from the list — two important sources

- **[NOAA ASOS direct](https://mesonet.agron.iastate.edu/request/asos/1min.phtml)** — raw ASOS gives 1-minute METARs for ~900 US airport stations including every major city. Highest quality minute-level data that exists, free, direct.
- **MesoWest / Synoptic Data API** — aggregates observations from thousands of networks (ASOS, RAWS, DOT road sensors, university networks, RWIS) into one API. Densest possible urban station coverage with minute-level data where available. Generous free research tier. **Probably the single most underrated source for this use case.**

### Practical stack

```
Minutes/sub-hourly (recent, <~1 month)
└── Tomorrow.io real-time API  OR  Wunderground PWS (5-min, noisy)

Hourly (historical, years back)
└── NOAA ISD on AWS S3  ← primary workhorse, free, bulk
    └── Filter to ASOS stations in target cities

Daily baselines / anomaly context
└── PRISM  OR  NOAA GHCN daily
```

---

## Q7 — Of these 4 sources, which expose the necessary info for historical ground truth at 1-year granularity?

Sources under consideration: Tomorrow.io, NCEI, IEM ASOS 1-minute, Synoptic Data.

### 🥇 IEM ASOS 1-Minute — best free ground truth

- **1-minute observations** for visibility, 2-min average and 5-sec peak wind speed/dir, RVR for ~900 US stations
- Reporting intervals: temp/dew point every 1 min, wind every 2 min, pressure every 1 min, precip every 1 min, present weather every 1 min
- Archive back to 2000, updated daily with ~18–36 hr delay from NCEI
- Free, bulk CSV, scriptable API
- Every major US city has an ASOS station (at the airport). **Single point per city**, but extremely high temporal fidelity.
- **Verdict**: primary ground truth source.

### 🥇 Synoptic Data — best for dense city coverage

- Access to 170k+ public/private stations, 320+ networks, 160 variables
- Temporal resolution varies by station — many 5-min, some 1-min, some hourly
- Time series API returns 5-min intervals, queryable by **lat/lon radius** (pull all stations within X miles of a city center)
- Real-time QC (range checks, rate-of-change, percentile outlier detection)
- Pricing: up to 1 year of historical on base tier; commercial plans for concurrency
- **Verdict**: best for **spatial density within a city**. Where IEM gives you one high-quality point, Synoptic aggregates dozens of networks giving real intra-city variation.

### 🥈 NCEI API — authoritative but clunkier

- Canonical archive that IEM and Synoptic both pull from upstream
- ISD integrates 100+ original sources, 14k+ active stations
- Hourly is primary via standard API; sub-hourly exists but requires direct S3/FTP
- Best accessed via AWS S3 for bulk historical
- **Verdict**: backup/cross-validation.

### 🥉 Tomorrow.io — best for forward-looking + extra variables

- Historical ~4 years, lat/lng queryable
- 1-min real-time; 1-hr historical on standard tiers
- Most comprehensive variable set (80+ including solar radiation, lightning, ice, pollen, flood risk)
- Proprietary model-blended data, not raw obs
- **Verdict**: not primary for historical minute-level — complementary for variables obs networks don't capture.

### Practical architecture

```
Ground truth (minute-level, 1-year historical)
├── PRIMARY:   IEM ASOS 1-min  → free, 1-min, 900 US stations, city airports
└── AUGMENT:   Synoptic API    → 5-min, intra-city density, 160 vars, QC'd

Model-blended variables (hourly, gap-filling)
└── NCEI ISD on AWS S3 / Tomorrow.io historical API

Forward-looking / forecast validation
└── Tomorrow.io
```

**Decision point**: single station vs spatial coverage.
- One accurate minute-level time series per city → IEM ASOS is sufficient and free.
- Spatial variation across neighborhoods → Synoptic is the only API that gives that at sub-hourly with QC, but costs money at scale.

---

## Q8 — My goal is to predict weather at the airports of a few major US cities, highest precision possible

**Airports are the ideal target.** They are:
- Where ASOS stations physically live — ground truth IS the target location, no interpolation
- Most densely observed points in the US weather network
- Where aviation-grade METARs are generated — highest QC'd surface obs that exist
- Where NWS and FAA both have strong incentive to model accurately

Signal-to-noise on training data is as good as it gets.

### Variables have very different forecast skill profiles

| Variable | Difficulty | Skill horizon |
|---|---|---|
| Temperature | Easy | 1–14 days reliably |
| Dew point / humidity | Easy–medium | 1–7 days |
| Wind speed/direction | Medium | 1–5 days |
| Precipitation occurrence | Hard | 1–3 days |
| Precipitation intensity | Very hard | Hours |
| Visibility / ceiling | Extremely hard | 0–6 hours (nowcast only) |
| Thunderstorm timing | Very hard | 0–2 hours |

The hardest variables (visibility, ceiling, convective precip) are also the most operationally critical for aviation — an active research frontier.

### Ideal stack for this problem

See the [[Project Scope]] doc for the layered version. Summary:

1. **Layer 1 — Ground truth**: IEM ASOS 1-minute
2. **Layer 2 — NWP model output**: NOAA HRRR primary (3km CONUS, hourly, 18hr horizon), GFS secondary, NAM tertiary
3. **Layer 3 — METAR**: rich qualitative features (ceiling, present weather, pressure tendency, RMK decoding)
4. **Layer 4 — Upstream spatial obs (Synoptic)**: mesoscale context
5. **Layer 5 — NEXRAD**: precipitation + convection nowcasting
6. **Layer 6 — TAF**: benchmark you need to beat

### Architecture (conceptual)

```
NWP model output (HRRR/GFS/NAM)     ← physics-based prior
        +
Recent observations (ASOS/METAR)    ← bias correction signal
        +
Upstream spatial obs (Synoptic)     ← mesoscale context
        +
Radar (NEXRAD)                      ← convective nowcast
        ↓
Post-processing model (ML or MOS)
        ↓
Calibrated probabilistic forecast
```

**Why HRRR is central**: it already ingests all available observations via data assimilation every hour. You're not starting from scratch — you're correcting a very good physics model's systematic biases at specific airport locations.

### Suggested first build

1. Pull 1 year of IEM ASOS 1-min for target airports → `y`
2. Pull corresponding HRRR forecast archives from S3 → strongest `X` features (grid cell nearest each airport at each valid time)
3. Train a bias-correction model (gradient boosted trees work well) that learns systematic errors HRRR makes at each specific airport
4. Add METAR observations as features for the hours leading up to forecast valid time
5. Expand to Synoptic spatial context once baseline works

This approach is well-validated — essentially what commercial aviation weather providers do, and it consistently outperforms raw NWP by a meaningful margin at specific point locations.

---

## Q9 — Explain Layer 2 (NWP model output) — what, how, where

### What NWP is

Physics simulations of the atmosphere. NOAA ingests all available observations (satellites, radiosondes, ASOS, aircraft, buoys) and initializes a simulation of the entire atmosphere. The model runs forward, solving fluid dynamics equations on a 3D grid.

**You don't build this. You consume it.**

### Why HRRR

| Model | Grid | Update freq | Horizon | Best for |
|---|---|---|---|---|
| **HRRR** | **3km** | **Every 1hr** | **18hr (48hr 4×/day)** | **Your use case** |
| NAM | 12km | Every 6hr | 84hr | Regional, medium |
| GFS | 13km | Every 6hr | 16 days | Global, long |
| RAP | 13km | Every 1hr | 21hr | Like HRRR but coarser |

HRRR specifics:
- 3km grid → each airport sits in a well-resolved cell, no significant interpolation error
- Hourly updates → always a fresh run within the last 60 minutes
- Assimilates ASOS, radar, satellites, aircraft reports at every cycle
- **Explicitly simulates convection** rather than parameterizing it — critical for precipitation accuracy

### Variables (surface level, most relevant for airports)

```
TMP    - 2m temperature
DPT    - 2m dew point
RH     - relative humidity
UGRD   - U wind component (east-west)
VGRD   - V wind component (north-south)
GUST   - surface wind gust
PRATE  - precipitation rate
APCP   - accumulated precipitation
TCDC   - total cloud cover
LCDC   - low cloud cover (determines ceiling)
VIS    - visibility
CAPE   - convective available potential energy
REFC   - composite reflectivity (simulated radar)
MSLP   - mean sea level pressure
```

Also vertical profile data (temp, wind, humidity at multiple pressure levels) — matters for ceiling and turbulence.

### Data structure

Each run produces a set of GRIB2 files:

```
hrrr.t00z.wrfsfcf00.grib2   ← analysis (hour 0, current conditions)
hrrr.t00z.wrfsfcf01.grib2   ← forecast hour 1
hrrr.t00z.wrfsfcf02.grib2   ← forecast hour 2
...
hrrr.t00z.wrfsfcf18.grib2   ← forecast hour 18
```

`t00z` = initialized at 00:00 UTC. 24 runs per day. Each file covers full CONUS at 3km (~150–300MB). **You only need the grid cell(s) near your target airports** — a tiny subset.

### Where to get it

**Historical archive** (1-year backfill):
- AWS S3: `s3://noaa-hrrr-bdp-pds/hrrr.YYYYMMDD/conus/hrrr.tHHz.wrfsfcfFF.grib2`
- Also via HTTPS: `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/`
- Free, no auth required, archive back to July 2014

**Real-time / near-realtime**:
- NOAA Nomads: `https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/`
- Last ~48 hours of operational runs

### How to extract (practically)

Don't download 200MB files for a handful of points. Use **byte-range requests** — GRIB2 has `.idx` files that tell you byte offsets for each variable.

**Easiest path: Herbie** (`pip install herbie-data`):

```python
from herbie import Herbie

# Get HRRR run from 2025-01-15, init 00z, forecast hour 6
H = Herbie("2025-01-15 00:00", model="hrrr", fxx=6)

# Download just 2m temp field
H.download("TMP:2 m")

# Or get it as an xarray dataset
ds = H.xarray("TMP:2 m")

# Extract value at SFO
sfo_lat, sfo_lon = 37.6213, -122.3790
sfo_temp = ds.sel(latitude=sfo_lat, longitude=sfo_lon, method='nearest')
```

Herbie handles byte-range requests, caching, and xarray conversion. Fastest path to usable data.

### Key conceptual point

Your training dataset looks like this per airport:

```
timestamp | hrrr_init_time | forecast_hour | hrrr_tmp | hrrr_dpt | hrrr_vis | ... | actual_tmp | actual_vis | ...
```

The model learns: "when HRRR says X at this airport, reality tends to be Y." That systematic offset is what you're capturing. It encodes local effects the 3km model still misses — terrain, sea breeze patterns, urban heat, fog formation unique to that airport.

This is exactly how operational **MOS (Model Output Statistics)** works — you'd be building a learned, ML version of it.

---

## Q10 — Explain Layer 3 (METAR observations)

### What METARs are

**Meteorological Aerodrome Report** — the standardized format ASOS stations transmit every hour at :54, plus **SPECI** (special reports) whenever conditions change significantly. Highest quality, most standardized surface observations that exist. Generated specifically for aviation safety → strict QC, consistent formatting, explicit missing-data codes.

Distinction from Layer 1: the 1-minute ASOS gives precise time series of physical measurements. METARs give you the **interpreted state** — sky condition layers, present weather type, remarks about pressure tendency, peak winds.

### Example METARs

```
KSFO 081856Z 28018KT 10SM FEW015 BKN030 12/08 A2992
RMK AO2 SLP132 T01220083
```

Decoded:
- `KSFO` — SFO airport
- `081856Z` — day 8, 18:56 UTC
- `28018KT` — wind 280° at 18kt
- `10SM` — visibility 10 statute miles
- `FEW015` — few clouds at 1,500ft AGL
- `BKN030` — broken ceiling at 3,000ft AGL
- `12/08` — temp 12°C, dew point 8°C
- `A2992` — altimeter 29.92 inHg
- `RMK` — remarks section
- `AO2` — station type (with precip discriminator)
- `SLP132` — sea level pressure 1013.2 hPa
- `T01220083` — precise temp 12.2°C / dew point 8.3°C

Bad-weather example:
```
KJFK 081754Z 02022G35KT 1/4SM +TSRA BR FEW005 OVC010CB 08/07 A2968
RMK AO2 PK WND 02038/1731 PRESRR RAB10 TSB05 SLP046 P0023 T00780067
```

- `02022G35KT` — wind 020° at 22kt gusting to 35kt
- `1/4SM` — visibility 1/4 mile
- `+TSRA` — heavy thunderstorm with rain
- `BR` — mist
- `OVC010CB` — overcast at 1,000ft, cumulonimbus
- `PK WND 02038/1731` — peak wind 38kt at 17:31
- `PRESRR` — pressure rising rapidly
- `RAB10` — rain began at :10
- `TSB05` — thunderstorm began at :05
- `P0023` — 0.23" precip this hour

### ASOS 1-min vs METAR — complementary

| | ASOS 1-min (Layer 1) | METAR (Layer 3) |
|---|---|---|
| Resolution | 1 minute | ~1 hour + specials |
| Temperature | Continuous numeric | Hourly + precise in RMK |
| Wind | Every 2 min numeric | 2-min avg + peak in RMK |
| Sky condition | ❌ | ✅ Multiple layers with height |
| Ceiling | ❌ directly | ✅ Explicitly coded |
| Present weather type | Limited | ✅ Explicit codes (RA/SN/FG/TS) |
| Pressure tendency | Raw values | ✅ PRESRR/PRESFR coded |
| Precip amount | 1-min accumulation | ✅ Hourly total in RMK |
| Thunderstorm onset | Indirect | ✅ TSB/TSE times in RMK |
| QC level | Good | Highest |

**Ceiling and sky condition layers** are particularly valuable — they determine IFR landing eligibility and are notoriously hard to predict. Having them as features in the hours leading up to your forecast target is extremely powerful.

### SPECIs are critical

Between scheduled hourly METARs, ASOS automatically generates a SPECI when:
- Visibility crosses a threshold (3mi, 1mi)
- Ceiling crosses a threshold (3000ft, 1500ft, 1000ft, 500ft)
- Wind speed/gusts change significantly
- A tornado/waterspout/funnel is observed
- Precipitation type changes

**Extremely high-value signals** — they mark the exact moment conditions changed. For nowcasting, time-elapsed-since-last-SPECI and its contents are strong features.

### Where to get METAR data

- **[IEM](https://mesonet.agron.iastate.edu/request/download.phtml)** — easiest, best archive, global coverage, decades of history. Scriptable endpoint: `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=KSFO&data=all&year1=2025&month1=1&day1=1&year2=2026&month2=1&day2=1&tz=UTC&format=comma&latlon=yes&direct=yes`. Returns decoded CSV including SPECIs.
- **NCEI** — authoritative bulk archive via `https://www.ncei.noaa.gov/access/services/data/v1?dataset=global-hourly&stations=...`
- **aviationweather.gov API** — raw METAR strings, good for real-time pipelines, limited historical depth

### Parsing

```python
# pip install metar
from metar.Metar import Metar

obs = Metar("KSFO 081856Z 28018KT 10SM FEW015 BKN030 12/08 A2992 RMK AO2 SLP132 T01220083")

obs.temp.value()        # 12.2
obs.dewpt.value()       # 8.3
obs.wind_speed.value()  # 18
obs.wind_dir.value()    # 280
obs.vis.value()         # 10 (miles)
obs.sky_conditions()    # [('FEW', 1500, None), ('BKN', 3000, None)]
obs.press.value()       # 1013.2
```

### RMK fields to extract as features

```
SLP               - sea level pressure (more precise than altimeter)
T                 - precise temp/dewpt (4 decimal places)
PK WND            - peak wind since last hour
PRESRR/PRESFR     - pressure rising/falling rapidly
RAB/RAE           - rain began/ended times
TSB/TSE           - thunderstorm began/ended times
P                 - hourly precipitation amount
6                 - 3/6 hour precipitation amount
I                 - ice accumulation
```

### How METARs fit the feature set

```
# Most recent METAR before T (lagged)
metar_T-1h_ceiling
metar_T-1h_visibility
metar_T-1h_present_weather_code
metar_T-1h_wind_speed / wind_dir
metar_T-1h_temp / dewpt
metar_T-1h_slp
metar_T-1h_precip_1h

# Change signals (delta between last two METARs)
delta_ceiling_1h
delta_visibility_1h
delta_slp_1h          ← pressure tendency is very predictive

# SPECI indicators
minutes_since_last_speci   ← if recent, conditions are volatile
last_speci_type            ← what triggered it

# Ceiling layer structure
sky_layer_count
lowest_layer_height
lowest_layer_coverage      ← FEW/SCT/BKN/OVC as ordinal
```

**Pressure tendency** (delta SLP over 1–3 hours) is one of the strongest single features for predicting whether conditions are improving or deteriorating — a direct measure of atmospheric dynamics driving weather change.

**Ceiling layer structure** encodes the cloud regime and is the primary determinant of IFR/VFR conditions, the most operationally critical airport weather variable.

---

## Q11 — Build timeline: how long to produce proprietary accurate real-time predictions?

**Short answer**: 3–6 months to beat TAF meaningfully on temperature. 6–12 months for a genuinely proprietary, multi-variable, real-time system.

Timeline is gated by a few hard problems, not just engineering time.

### Phase 1 — Data infrastructure (3–6 weeks; likely 2–3 weeks for someone with data infra experience)

Pure engineering, no ML yet:
- S3 pipelines for HRRR historical backfill (1yr × 24 runs/day × ~20 variables)
- IEM ASOS + METAR historical pull
- **Alignment problem**: join HRRR forecast valid times to ASOS observations without leaking future information. Where most people lose a week.
- Storage decision: ClickHouse or Parquet/S3
- Schema: `(airport, init_time, forecast_hour, valid_time, hrrr_vars..., observed_vars...)`

### Phase 2 — Baseline model, temperature only (3–4 weeks)

Temperature is the easiest variable and right place to start. HRRR is already very good — you're correcting small systematic bias.

```
Features:  HRRR 2m temp forecast, forecast hour, month, hour of day, airport ID
Target:    ASOS observed temp at valid time
Model:     XGBoost or LightGBM
```

Expect RMSE ~1.2°C with just HRRR features, improving to ~0.8–0.9°C with full feature set. TAF temperature skill is roughly 1.0–1.2°C RMSE at 6hr, degrading to ~2°C at 24hr. **You can beat TAF on temperature within this phase.**

Critical: proper train/test splitting **must be by time, never random**. Weather has strong autocorrelation — random splits give fraudulently good metrics.

### Phase 3 — Add METAR features + more variables (4–6 weeks)

Adding ceiling, visibility, present weather type, pressure tendency. Complexity jumps.

**Ceiling and visibility are a different class of problem**:
- Highly non-Gaussian (bimodal — either clear or in the soup)
- Driven by local microclimatic effects HRRR systematically misrepresents (SFO marine layer, JFK coastal fog)
- Extremely airport-specific — same synoptic pattern produces fog at SFO but not OAK 15 miles away

Expect 4–6 weeks to get ceiling/visibility competitive with TAF, and you may not fully beat it — NWS forecasters have decades of local knowledge baked into TAF issuance.

### Phase 4 — Real-time inference pipeline (3–4 weeks)

Where "historical model" becomes "live system":
- HRRR ingest: poll NOMADS hourly, detect new run availability
- Parse + extract relevant grid cells within ~10 min of run completion
- METAR ingest: poll aviationweather.gov every 5–10 min
- Feature assembly: join live HRRR + recent METARs
- Model serving: inference with confidence intervals
- Output: time series of predictions updating each hour
- Alerting: detect significant forecast shifts between runs

**Latency target matters**. HRRR takes ~45 min to complete after initialization. A 00z run initialized at midnight is available around 00:45z. Pipeline should detect availability, download subsets, assemble features, and run inference within 5–10 min of data availability.

### Phase 5 — Proprietary edge (ongoing, 2–4 months)

- **Per-airport microclimate modeling** — separate models or airport embeddings. NWS TAFs do this implicitly via local forecaster knowledge; you learn it from data.
- **Ensemble disagreement as uncertainty signal** — HRRR/GFS/NAM often disagree. Spread = uncertainty. Lets you produce calibrated probabilistic forecasts rather than point estimates. Genuine edge over deterministic TAF.
- **NEXRAD integration for nowcasting** — 0–3hr precip, storm approach. Separate modeling problem.
- **Rapid update on SPECI** — when a SPECI fires, immediately re-run inference. Static TAF can't do this.

### Real bottlenecks (what actually gates the timeline)

1. **Evaluation discipline** — easy to build a model that overfits temporal patterns. Proper backtesting across seasons, years, severe weather events takes rigor.
2. **Rare event performance** — trained on mostly benign weather. Fog/thunderstorm/ice events require oversampling, separate models, or explicit regime detection.
3. **Calibration** — "15°C ± 1.2°C, 90% CI" is more useful than "15°C." Separate modeling step, matters for operational use.
4. **HRRR latency and outages** — live pipeline needs graceful degradation when the latest run is 3hrs late.

### Realistic timeline summary

```
Weeks 1–3    Data pipelines, ASOS + HRRR backfill, alignment
Weeks 4–7    Baseline temperature model, beats TAF on temp
Weeks 8–13   METAR features, ceiling/visibility, precip occurrence
Weeks 14–17  Real-time inference pipeline, live updates
Months 5–9   Proprietary edges — per-airport tuning, ensembles, nowcasting
Month 9+     Continuous improvement, rare events, calibration
```

- **3 months** → something genuinely working and defensible on temperature.
- **6 months** → real-time system competitive with TAF across most variables.
- **12 months** → genuinely proprietary with operational history.

**Most important early action**: pick **one airport, one variable (temperature)**, get the full pipeline working end-to-end before expanding. The temptation to boil the ocean before a working baseline is the most common failure mode.

---

## Q12 — Is Layers 1+2 good enough to beat everyone without proprietary models?

**Short answer**: Layers 1+2 puts you in the top tier but **probably not at the frontier**.

### What you get with Layers 1+2 alone

HRRR + ASOS bias correction is essentially a learned version of **MOS (Model Output Statistics)** — which [NWS already publishes for free](https://www.weather.gov/mdl/mos_home). GFS-MOS and NAM-MOS are public. HRRR-based MOS equivalents exist commercially.

If your model is just "correct HRRR bias using historical observations at the airport" — you're rebuilding something that already exists publicly. You'd be competitive with the average commercial provider but **not differentiated**. Serious competitors in a pricing/prediction market context are likely already doing this.

### Competitive landscape for day-high temperature and precip occurrence

**Tier 1 — what everyone has access to**
- Raw HRRR/GFS output (free, public)
- NWS MOS forecasts (free, public)
- TAF for airports (free, public)
- Basic bias correction (relatively straightforward)

→ If this is your competition, Layers 1+2 beats them.

**Tier 2 — what serious competitors have**
- Multi-model ensemble (HRRR + GFS + NAM + ECMWF) with learned weighting
- METAR ceiling/visibility/pressure tendency as features
- Synoptic upwind obs for spatial context
- Calibrated probabilistic output (not just point forecast)

→ Layers 3+4 become necessary to compete.

**Tier 3 — genuine frontier**
- ECMWF access (~$50k+/year commercial license)
- Proprietary observation networks (Understory, Tomorrow.io's own sensors)
- Ensemble spread as uncertainty signal
- Per-location microclimate modeling with 5+ years of training data
- Nowcast integration for same-day high corrections

### Specific problem: day high temp vs precipitation

**Day high temperature** — most crowded, most efficiently priced. HRRR is genuinely excellent; systematic bias at most airports < 1°C. Everyone serious is already doing HRRR bias correction. To beat the field you need: more training data history (5+ years not 1), seasonal stratification, regime-aware modeling, ensemble spread as feature. **Layers 1+2 gets you close but marginal gains come from 3+4.**

**Precipitation occurrence** — where significant alpha still exists, and where Layers 1+2 are more obviously insufficient:
- HRRR systematically over-forecasts light precip and mis-times convective events
- Critical same-day signal is current METAR present weather + pressure tendency + upstream obs
- NEXRAD approach vectors are far superior to NWP for 0–6hr precip onset
- Layers 1+2 ≈ TAF-level skill. Beating TAF meaningfully requires Layers 3+5.

### The ECMWF problem

Biggest edge serious commercial forecasters have that you won't with just public data: **ECMWF (European Centre for Medium-Range Weather Forecasts)**. Consistently outperforms all US models including HRRR on 3–7 day forecasts. Not free for commercial use (~$50k+/yr), though ECMWF open data exists at reduced resolution.

**For same-day/next-day (your day-high case), HRRR actually beats or matches ECMWF** due to higher resolution and hourly updates. ECMWF's edge is 3–10 days. So for this specific use case the ECMWF gap matters less than you'd think.

### Honest competitive assessment

```
Layers 1+2 only
→ Beats naive forecasters and raw model consumers
→ Roughly equivalent to NWS MOS (publicly available)
→ Probably not enough to win a competitive pricing market

Layers 1+2+3
→ Meaningfully better ceiling/visibility/precipitation
→ Pressure tendency gives regime-change detection
→ Competitive with serious commercial providers on 0–12hr

Layers 1+2+3+4 (Synoptic spatial)
→ Upwind context gives genuine edge on convective events
→ Starts to look proprietary for specific airports
→ Likely top-tier for same-day predictions

Layers 1–5 + per-airport tuning + ensemble spread
→ Genuinely frontier for airport-specific day-high and precip
→ Proprietary in the sense that replication requires significant effort
```

**The largest exploitable inefficiency right now**: probably not temperature (efficiently priced) but **precipitation timing and intensity on a same-day basis** — because that's where NWP is weakest and where METAR + NEXRAD signals give edge that others without those layers won't have.

---

## Q13 — What costs money in terms of data?

More favorable than you might expect.

### Completely free

- **Layer 1 — IEM ASOS 1-min** — Iowa State public service. No auth, no meaningful rate limits, bulk downloadable.
- **Layer 2 — HRRR / GFS / RAP / NAM on AWS** — NOAA Open Data Dissemination. S3 public buckets, no auth. **Caveat**: if running EC2 in `us-east-1` (same region), egress free. Other region or local, standard egress (~$0.09/GB). A 1-year HRRR backfill of subset variables for a few airports is ~50–200GB → $5–20 one-time, essentially nothing.
- **Layer 3 — IEM METAR** — same system as ASOS, global coverage, decades of history.
- **Layer 6 — TAF / aviationweather.gov** — free. NWS MOS also free.
- **NEXRAD radar on AWS** — `s3://noaa-nexrad-level2/`, same NOAA open data program.

### Costs money

**Synoptic Data (Layer 4)** — only layer in the core stack with real cost.

| Tier | Cost | What you get |
|---|---|---|
| Free/dev | $0 | Very limited calls, recent data only |
| Basic commercial | ~$500–2,000/month | 1yr history, reasonable call volume, multiple networks |
| Full commercial | Custom | Multi-year history, high concurrency, all networks |

Free tier too limited for production. For a few airports with reasonable query frequency, ~$500–1,000/month to do it properly.

**Mitigation**: for a handful of US airports, you can approximate Synoptic's value by pulling ASOS networks directly from IEM for surrounding stations — free, more engineering work, less comprehensive than Synoptic's aggregated 320 networks.

**ECMWF commercial license** — $50k+/yr commercial, ECMWF open data exists at reduced resolution for free. Not necessary for day-high/same-day use case.

### Indirect costs

- **Compute** — HRRR GRIB2 processing is CPU-intensive. 1-year backfill across 5 airports: ~10–20 hours on a spot EC2 instance (c5.2xlarge ~$0.15/hr) → $2–3 one-time. Ongoing inference per run: milliseconds.
- **Storage** — extracted training dataset ~5–20GB for 1yr × 5 airports. S3 essentially $0. ClickHouse on small EC2: $50–100/month if self-hosted.
- **ECMWF Open Data** — free tier: 0.25° resolution, 6-hourly, subset of variables. Registration required at `https://data.ecmwf.int/`. Worth including as a free upgrade to Layer 2 ensemble.

### Summary

```
Layer 1  IEM ASOS 1-min          FREE
Layer 2  HRRR/GFS/NAM on AWS     FREE (tiny egress cost on backfill)
Layer 3  IEM METAR               FREE
Layer 4  Synoptic Data           $500–1,000/month  ← only real cost
Layer 5  NEXRAD on AWS           FREE
Layer 6  TAF / NWS MOS           FREE

ECMWF open data (bonus)          FREE (limited resolution)
ECMWF commercial (optional)      $50k+/year — skip for now

Compute (EC2 for backfill)       $5–20 one-time
Ongoing inference compute        Negligible
Storage                          $50–100/month
```

**Total monthly: $600–1,200, almost entirely Synoptic.** Critically — you can **defer Synoptic entirely** for Phase 1+2. First 2–3 months (pipelines, baseline temp model, METAR enrichment) costs essentially nothing.

---

## Q14 — Is this competitive with Tomorrow.io's $50/mo APIs?

### What Tomorrow.io actually is

**Not a modeling company at its core** — a data aggregation and delivery company with a good UI/API layer on top. Underlying weather model blends HRRR, GFS, NAM, ECMWF (same public models you'd use), their own proprietary satellite constellation (recent, still maturing), commercial observation networks, some ML post-processing.

The $50/mo tier is a **consumer/developer product**: hourly forecasts at lat/lon, ~80 variables, clean REST API. No raw model access, no uncertainty estimates, no custom training.

### Direct comparison

| | Tomorrow.io $50/mo | What you'd build |
|---|---|---|
| Temperature day-high | Good, HRRR-based | Comparable or better (bias-corrected to specific airport) |
| Precipitation occurrence | Good | Comparable at 12hr+, better at 0–6hr with NEXRAD |
| Ceiling / visibility | Moderate | Better — airport-specific training |
| Uncertainty / confidence | ❌ None | ✅ Calibrated intervals |
| Airport-specific tuning | ❌ Generic point | ✅ Trained on that exact station |
| Update frequency | Hourly | Hourly (same HRRR cadence) |
| Raw model access | ❌ | ✅ |
| Explainability | ❌ Black box | ✅ Full |
| Custom variables | ❌ | ✅ |
| Cost at scale | Scales fast | Mostly fixed infra |

### Honest truth

For a **generic location** (downtown lat/lon, neighborhood, random coordinate), Tomorrow.io is genuinely excellent and hard to beat for the price. Global coverage, clean API, engineering done.

**But you are not targeting generic locations.** You target specific named airports with decades of labeled observation history, ASOS sensors physically on-site, METAR records going back to the 1970s. **This is the single best-case scenario for a learned bias correction model.** Tomorrow.io's generic model cannot exploit that station-specific history the way your trained model can.

> Analogy: Tomorrow.io is a general practitioner. What you're building is a specialist who has seen every patient at that specific clinic for 20 years.

### Where Tomorrow.io wins anyway

- **Coverage** — 10,000 locations globally? Tomorrow.io wins trivially. You're building something narrow and airport-specific.
- **Operational reliability** — SLAs, uptime guarantees, redundancy. Your pipeline will have outages, HRRR ingestion failures, staleness issues early on.
- **Time to value** — Tomorrow.io data in 5 minutes. Yours takes months.
- **Non-airport locations** — no ASOS station → Tomorrow.io has interpolated coverage. You don't.

### Real competitive question

*Who is your actual end user and what do they need?*

- Need generic weather at any location → Tomorrow.io wins, don't compete
- Need calibrated probabilistic forecasts at specific airports → you win
- Need uncertainty quantification (CIs) → you win, Tomorrow.io doesn't offer this at any tier
- Need raw model access + explainability → you win
- Need custom variables or derived signals → you win
- Need precipitation timing precision at 0–3hr → you win with NEXRAD
- Need clean API for 50 locations globally in 5 min → Tomorrow.io wins

### Bottom line

Tomorrow.io's $50/mo product is better than what most people could build, because most people don't have the time or expertise.

It is **not better than what you specifically would build for airport-specific prediction**, because:
- You're training on the exact ground truth sensor at the target location
- You're correcting model bias specific to that airport's microclimate
- You're producing calibrated uncertainty, not just point forecasts
- You have METAR sky/ceiling/pressure history that encodes local regime behavior
- Your model improves continuously as you accumulate more local labeled data

The gap isn't model sophistication — it's **coverage and operational reliability**. They win on breadth. You win on depth at specific locations.

For pricing weather outcomes at major US airports, your system should outperform Tomorrow.io meaningfully within 3–4 months and the gap widens over time.

---

## Q15 — CRITICAL REFRAME: I'm trying to compete in prediction markets, trading daily weather markets

> This reframes everything.

### The actual problem

You're not building a weather service. **You're building a quantitative edge in a prediction market.** The question is not "is my forecast accurate" — it's "is my forecast more accurate than the market's implied probability, *consistently enough to extract alpha*."

Completely different optimization target.

### How weather prediction markets work

- **Kalshi** — most liquid US weather markets. Daily high temperature at major cities (usually major airports). Precipitation occurrence (yes/no). Resolves against official NWS observations. Contracts like *"Will NYC daily high exceed 72°F on April 9?"*
- **Forecast.it / Metaculus / Manifold** — lower liquidity, less serious competition.
- **Weather futures (CME)** — HDD/CDD futures, longer horizon, monthly aggregates. More sophisticated competition (energy traders). Not where you start.

**Key insight**: Kalshi weather markets are relatively new and relatively inefficient compared to financial markets. Competition is not uniformly sophisticated.

### Who you're actually competing against

This is the most important thing to understand before building anything.

```
~40%  Casual retail (vibes-based, Weather Channel consumers)
~30%  Semi-serious (AccuWeather/Tomorrow.io API consumers)
~20%  Systematic but unsophisticated (simple NWS MOS users)
~10%  Genuinely quantitative (what you're building)
```

The market price reflects the weighted average. The casual majority creates persistent mispricing the quantitative minority exploits.

**Good news**: the 10% quantitative tier is NOT running anything close to the full stack described. Most run HRRR bias correction at best. Very few have METAR regime features, calibrated uncertainty, or NEXRAD nowcasting.

### What actually generates alpha

Academic forecast accuracy (RMSE vs TAF) is NOT the same as prediction-market alpha. Think in terms of:

**1. Calibrated probabilities vs point forecasts**
The market prices a binary: *"does high exceed X°F"*. Your model needs to output `P(high > X)`, not just the expected high. Requires:
- Probabilistic model, not point forecast
- Proper calibration (if you say 70%, it should happen 70% of the time)
- Full distribution of possible outcomes, not just the mean
- HRRR ensemble (**HRRRx**, 36 members) gives you a free distribution estimate

**2. Edge concentrated in specific conditions**
Alpha won't be uniform. Concentrated in:

```
High edge situations:
- Marine layer days at SFO (market underprices fog persistence)
- Convective days at ORD/DFW (market underprices t-storm temp suppression)
- Cold front passage timing (market misprices by 3–6 hours consistently)
- Overnight low radiative cooling (HRRR warm-biases clear nights)

Low edge situations:
- Benign synoptic pattern, clear sky, light wind
- Market is efficiently priced here — don't trade
```

Your model needs to **know when it has edge and when it doesn't**. Betting into efficient markets destroys EV.

**3. Speed of update**
Kalshi markets stay open until resolution. HRRR updates every hour. **Your edge is reacting to new model runs faster than the market reprices.**

If a 12z HRRR run shows a cold front arriving 6hrs earlier than the 06z run, and the market hasn't repriced yet — tradeable signal. **Window is probably 15–45 minutes** before sophisticated participants reprice.

This makes your **real-time pipeline architecture** not just nice-to-have but the **core alpha source**.

**4. Market microstructure**
- Wide bid-ask spreads (sometimes 3–8 cents on a $1 contract)
- Limited liquidity at size
- Predictable repricing patterns around NWS forecast updates (11am/5pm local)

Understanding when the market is stale vs fresh is itself edge.

### Revised data stack priority for trading

```
Tier 1 — Must have (directly drives P&L)
├── HRRR ensemble (HRRRx) — free distribution estimate
├── IEM ASOS historical — calibration of your probability model
├── METAR current conditions — nowcast signal
└── Market prices themselves — the signal you're trying to beat

Tier 2 — High value (sharpens edge in specific conditions)
├── GFS ensemble (GEFS, 31 members) — longer horizon distribution
├── ECMWF open data — independent model for ensemble disagreement
├── Pressure tendency from METAR — regime change detection
└── NEXRAD — same-day convective suppression of temperature

Tier 3 — Nice to have (marginal improvement)
├── Synoptic upwind obs — mesoscale context
├── NWS MOS as baseline — benchmark for market efficiency
└── TAF — what the market is probably anchored to
```

**Single highest-value addition vs original plan: HRRR ensemble (HRRRx).** 36 members = free empirical distribution to price binaries against. On AWS S3 alongside regular HRRR: `s3://noaa-hrrr-bdp-pds/` under `hrrr.YYYYMMDD/ensprod/`.

### The actual model you need to build

Not a forecast model. **A probability calibration model.**

```python
# What you're actually estimating:
P(daily_high > threshold | HRRR_ensemble, METAR_current, historical_bias, airport)

# Inputs:
- HRRR ensemble mean and spread for daily high at airport
- HRRR ensemble fraction of members exceeding threshold (naive probability)
- Historical bias of HRRR at this airport in this season/regime
- Current METAR conditions (marine layer present? frontal passage imminent?)
- Hours until market resolution
- Current market price (implied probability)

# Output:
- Your probability estimate
- Confidence in that estimate
- Edge = your_probability - market_implied_probability
- Bet if |edge| > transaction_cost_threshold
```

Critical distinction vs pure forecast accuracy: **you only need to be more right than the market, not perfectly right.**

### Kelly sizing and risk management

```python
# Simplified Kelly for binary market
# b = odds received (e.g. market at 0.40 → $1.50 per $1 if buying YES)
# p = your estimated probability
# q = 1 - p

kelly_fraction = (b * p - q) / b

# Always fractional Kelly (0.25x–0.5x) for model uncertainty
bet_size = 0.25 * kelly_fraction * bankroll
```

**Model uncertainty should directly modulate position sizing.** Benign clear days with tight HRRR ensemble → size up. Convective days with wide spread → size down or skip.

### Honest alpha by market

| Market | Edge availability | Competition level | Notes |
|---|---|---|---|
| Daily high temp, major cities | Medium | Medium-high | Most crowded, HRRR-efficient |
| Daily high temp, edge cases | **High** | Low-medium | Marine layer, convective suppression |
| Precipitation occurrence | **High** | Medium | NWP weakest here, most alpha |
| **Precipitation timing** | **Very high** | **Low** | Almost no one modeling this well |
| Wind speed thresholds | Medium | Low | Undertraded, less competition |

**Precipitation occurrence** is probably your highest-alpha market — hardest to model, competition thinnest, often anchored to crude NWS PoP forecasts known to be poorly calibrated.

### What to build first given this context

```
Week 1–2:
  Open Kalshi account, paper trade weather markets manually
  Understand market mechanics, resolution criteria, liquidity
  Identify which cities/variables are most liquid

Week 3–4:
  Pull HRRR ensemble (HRRRx) for target cities
  Build naive probability estimator: fraction of members exceeding threshold
  Compare to market prices historically — identify systematic gaps

Week 5–8:
  Add ASOS historical for bias correction
  Build calibrated probability model
  Backtest against historical Kalshi prices if obtainable

Week 9–12:
  Add METAR current conditions as nowcast features
  Build live pipeline: HRRR ingest → probability update → compare to market
  Start live trading small size

Month 4+:
  Add NEXRAD for precipitation
  Refine per-city models
  Scale position sizing as edge is confirmed
```

### Most important thing

> **The weather forecasting literature is not the relevant benchmark anymore. The relevant benchmark is market implied probability vs your probability.**

A model that is only slightly better than HRRR raw output but is **well-calibrated, fast to update, and knows when it has edge** will make money.

A model that produces beautiful RMSE improvements but bets uniformly will **lose to transaction costs and spread**.

The edge in this market right now is less about building a better weather model than the world has ever seen — and more about being the **most rigorous probabilistic thinker in a market full of people anchored to deterministic point forecasts**.

That's a much more achievable goal on your timeline.

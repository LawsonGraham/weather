---
name: weather-data
description: "Reference for all weather data sources used in this repo — HRRR, ASOS, METAR, NEXRAD, TAF, Synoptic, GFS. Use when working on data ingest, feature engineering, schema decisions, or debugging data pipelines. Read this skill before writing any data-layer code."
allowed-tools: Read, Grep, Glob, WebFetch
---

# Weather data stack reference

This repo uses the 6-layer data stack defined in `vault/Weather Vault/Project Scope.md`. Always read Project Scope for full scoping; this skill is a quick reference plus repo conventions.

## Layer summary

| Layer | Source | Use | Cost | Access |
|---|---|---|---|---|
| 1 Ground truth | IEM ASOS 1-min | `y` for training; minute-level observations | Free | `mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py` |
| 2 NWP | NOAA HRRR (+ HRRRx ensemble) | Strongest `X` features, 3km CONUS, hourly | Free | `s3://noaa-hrrr-bdp-pds/` via Herbie |
| 3 METAR | IEM METAR | Ceiling, visibility, present-wx, pressure tendency | Free | `mesonet.agron.iastate.edu/cgi-bin/request/asos.py` |
| 4 Spatial | Synoptic API | Upwind mesoscale context | ~$500–1k/mo | `api.synopticdata.com/v2/stations/timeseries` |
| 5 Radar | NEXRAD Level 2 | 0–3hr precip nowcasting | Free | `s3://noaa-nexrad-level2/` |
| 6 Benchmark | TAF | NWS forecast baseline to beat | Free | `aviationweather.gov/api/data/taf` |

Layer 4 (Synoptic) is deferrable — skip until Phase 3. Layers 1, 2, 3, 6 are the walking-skeleton set.

## Conventions for this repo

### Storage — see `.claude/skills/data-script/SKILL.md` for the canonical contract

- **`data/raw/<source>/`** — immutable originals, one subdir per upstream source. Directory name matches `scripts/<source>/`.
  - Every `raw/<source>/` must have `MANIFEST.json` (schema v1, in the data-script skill) and a `download.log`.
  - Download scripts must be idempotent: check `MANIFEST.json.download.status` before doing work. Copy `.claude/skills/data-script/template.py` for every new source.
- **`data/interim/<step>/`** — cleaned, filtered, or decoded intermediates. Organized by transformation step, not by source.
- **`data/processed/<task>/`** — final model- or backtest-ready artifacts. Organized by downstream task.
- The whole `data/` tree is gitignored. **Never commit** GRIB2, Parquet, CSV, or NetCDF files.
- Never hand-edit anything in `raw/`. If you need a transformation, write `scripts/<source>/transform.py` that emits to `data/interim/` or `data/processed/`.

### Identifiers

- **Airports**: ICAO codes (`KSFO`, `KJFK`, `KORD`, `KDFW`, `KLAX`)
- **Time**: UTC internally, always. Convert to local only at the market-resolution boundary.
- **HRRR run identifier**: `init_time` (UTC datetime) + `fxx` (forecast hour int)

### Alignment (causality is load-bearing)

- Join HRRR forecasts to ASOS observations by `(station, valid_time)` where `valid_time = init_time + fxx hours`.
- **Strictly causal**: no observation from `t >= valid_time` may leak into features predicting the observation at `valid_time`.
- Use `init_time` as the "as-of" reference when constructing feature sets — whatever was known at `init_time` is fair game; anything after is leakage.

### Recommended libraries (add with `uv add <pkg>`)

- `herbie-data` — HRRR/GFS GRIB2 subset access via byte-range (don't reinvent)
- `metar` — METAR string parsing
- `xarray` + `cfgrib` — gridded data (cfgrib bundles eccodes wheels on macOS)
- `polars` (preferred) or `pandas` — tabular data
- `duckdb` — local analytical queries over Parquet
- `nexradaws` + `arm-pyart` — NEXRAD (Phase 4, not yet)
- `SynopticPy` — Synoptic API (Phase 3, not yet; paid)

All already declared in `pyproject.toml` except the Phase 3/4 ones — add those with `uv add <pkg>` when you get to those phases.

## Pitfalls

1. **Don't pull full HRRR domain files** — use byte-range via Herbie. Full files are ~200MB each.
2. **CONUS only** — HRRR does not cover Shanghai, London, etc. Use GFS or ECMWF open data for international.
3. **Time-based splits only** — never random splits for time series.
4. **Don't mock the data layer in tests** — mock/prod divergence is a project risk per vault notes. Test against real (sampled) data.
5. **Market resolution windows matter** — a Kalshi "daily high" might be midnight-to-midnight local, which is NOT the same as UTC-day. Resolve this per market in config, not in pipeline code.
6. **Use the HRRRx ensemble** — 36 members gives you a free empirical distribution for calibrated probabilities. This is the single highest-value upgrade vs a deterministic pipeline per vault scoping.

## When this skill is invoked

- By architect or implementer during the pipeline when planning or writing data-layer code
- Directly by the user asking data-layer questions
- By the `weather-data-expert` subagent as a reference while it works

If the question is deeper than this reference covers, delegate to the `weather-data-expert` subagent.

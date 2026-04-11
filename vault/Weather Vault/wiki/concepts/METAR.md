---
tags: [concept, data-source, layer-3, iem, metar, observations]
date: 2026-04-11
related: "[[Project Scope]], [[index]]"
---

# METAR

**METAR** (Aviation Routine Weather Report) is the hourly + special-observation aviation weather report issued by every staffed airport worldwide. It's **Layer 3** of our Phase 1 data stack — the rich qualitative observation layer that sits between ASOS 1-minute (sensor-level ground truth, Layer 1) and HRRR NWP (forecast features, Layer 2).

Unlike ASOS 1-min — which is a stream of bare numeric sensor values — a METAR is a structured human-readable encoding that carries explicit **sky condition layers**, **present-weather type codes** (`RA`, `SN`, `FG`, `TS`, ...), **pressure tendency flags**, and a free-text **RMK (remarks) block** containing a dozen more high-precision fields an instrument feed alone doesn't expose.

## Why it matters for trading

- **Daily-high / daily-low contracts resolve against the `4/`-group in the 00Z METAR** — the official 24-hour extreme the station archives. That is *literally* the settlement value for Polymarket / Kalshi daily-temperature markets. We don't need to reconstruct it from ASOS samples; it's sitting in the RMK block.
- **Pressure tendency (PRESRR / PRESFR / 5-group) is a direct frontal-passage signal.** A rapidly falling barometer precedes wind shifts, cloud-deck drops, and precipitation onset — things HRRR sometimes misses in the 0–3 hr window because its initialisation lags the synoptic timing.
- **Thunderstorm begin/end (TSB/TSE)** pins convective onset to the minute, which matters for any same-day precipitation contract.
- **0.1°C-precision temperature and dewpoint** from the T-group beat the integer-°F tmpf/dwpf columns for threshold-based temperature contracts where a half-degree matters.

## Source

- **Provider:** Iowa State University Iowa Environmental Mesonet (IEM). Same upstream host as the ASOS 1-minute feed, but a different CGI endpoint.
- **Endpoint:** `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py` (form UI at [download.phtml](https://mesonet.agron.iastate.edu/request/download.phtml))
- **Auth / rate limits:** none, free, polite use only
- **Historical depth:** decades (back to the 1990s for most US airports)
- **Formats requested:** `data=all`, `format=comma`, `report_type=3,4` (routine + SPECI)
- **Parser:** [`python-metar`](https://pypi.org/project/metar/) — pinned in `pyproject.toml`

## Pipeline

Everything lives under `scripts/iem_metar/`:

- **`download.py`** — per-(station, calendar-month) CSV under `data/raw/iem_metar/<STATION>/YYYY-MM.csv`. Idempotent at the file level; always refetches the current month because it's still partial. Handles IEM's `#DEBUG:` comment-line preamble in the response validator.
- **`transform.py`** — CSV → Parquet under `data/processed/iem_metar/<STATION>/YYYY-MM.parquet`. Zstd compression (~3x). v2 decodes the RMK block via `python-metar` + regex and appends 16 columns; see the [schema](#schema) section.
- **`validate.py`** — file completeness, schema + types, row counts, timestamp monotonicity, tmpf bounds, raw-METAR non-null, `wxcodes` frequency sample.

Typical Phase 1 run (KNYC + KLGA, 2025-12-20 → present):

```
data/raw/iem_metar/           ~1.3 MB   (10 CSVs)
data/processed/iem_metar/     ~400 KB   (10 parquets, ~3.2x compression)
6200 rows, 46 columns
```

## Schema

**46 columns = 30 pre-RMK decoded by IEM + 16 RMK decoded by our transform.**

### IEM-decoded (30 columns)

| Group | Columns |
|---|---|
| Identity | `station`, `valid` (Datetime[μs, UTC]) |
| Temperature | `tmpf` (int °F), `dwpf` (int °F), `relh`, `feel` |
| Wind | `drct`, `sknt`, `gust`, `peak_wind_gust`, `peak_wind_drct`, `peak_wind_time` |
| Pressure | `alti`, `mslp` (same as SLP from RMK, IEM pre-decodes it) |
| Visibility | `vsby` |
| Precip | `p01i` (1-hr, trace `T` → 0.0001) |
| Sky condition | `skyc1..4` (`FEW`/`SCT`/`BKN`/`OVC`/`CLR`), `skyl1..4` (ft AGL) |
| Present weather | `wxcodes` (e.g. `-SN BR`, `-TSRA`, `-RASN`) |
| Ice | `ice_accretion_1hr`, `ice_accretion_3hr`, `ice_accretion_6hr` |
| Snow | `snowdepth` |
| Raw | `metar` (full MET AR string — the source-of-truth for RMK decoding) |

### RMK-decoded (16 columns, v2 transform)

| Column | Source group | Notes |
|---|---|---|
| `temp_c_rmk` / `dewpt_c_rmk` | `T` group (`Tnnnndnnn`) | 0.1°C precision. Coverage: ~99.97% of rows. |
| `slp_mb_rmk` | `SLP` group (`SLPnnn`) | 0.1 mb precision. Redundant with IEM's `mslp` but fresh from raw. |
| `max_temp_6hr_c` / `min_temp_6hr_c` | `1`/`2` groups | **Reported at 00/06/12/18Z synoptic hours only.** |
| `max_temp_24hr_c` / `min_temp_24hr_c` | `4` group | **Reported at 00Z only — the daily extreme used by markets.** |
| `precip_6hr_in` | `6` group | Absent when zero; presence is itself a signal. |
| `precip_24hr_in` | `7` group | 00Z only, absent when zero. |
| `snowdepth_in_rmk` | `4/nnn` group | Snowpack depth, inches. |
| `press_tendency_3hr_mb` / `..._code` | `5tppp` group | Magnitude in 0.1 mb + WMO character code `t ∈ 0..8`. |
| `presrr` / `presfr` | `PRESRR` / `PRESFR` regex | Boolean. Rapid pressure rise/fall — frontal-passage proxy. |
| `tsb_minute` / `tse_minute` | `TSBhhmm` / `TSEhhmm` regex | Thunderstorm begin / end minutes-past-hour. |

### Key RMK groups, by example

```
KLGA 272151Z 07012KT 10SM FEW050 BKN110 BKN160 BKN250 08/M04 A3008
     RMK AO2 SLP187 T00781044 10089 20033 53012 P0001 60005 70023 4/008
```

- `SLP187` → 1018.7 mb
- `T00781044` → temp +7.8°C, dewpt −4.4°C
- `10089` → 6hr max temp +8.9°C
- `20033` → 6hr min temp +3.3°C
- `53012` → 3hr press tendency code 3, magnitude 1.2 mb
- `P0001` → 1hr precip 0.01"
- `60005` → 6hr precip 0.05"
- `70023` → 24hr precip 0.23"
- `4/008` → snowdepth 8"

## Relationship to other layers

- **Layer 1 (ASOS 1-min):** Different cadence (1-min numeric stream vs hourly + event-triggered structured reports). Join key is `(station, valid_utc)`. METAR gives you the official station-recorded daily extremes; ASOS 1-min gives you the high-resolution trajectory between METARs.
- **Layer 2 (HRRR):** METAR is nowcast-leaning observation; HRRR is the forecast. The diff between HRRR's predicted `tmp2m` at `valid=00Z` and METAR's `max_temp_24hr_c` at 00Z is a direct bias signal for the HRRR bias-correction model.
- **Layer 6 (TAF):** TAFs are the NWS *forecasts* for the same airport; METARs are the ground-truth they're being evaluated against. The `metar[P]arser` already handles TAF — same library, different endpoint.

## Schema quirks we hit

- **`#DEBUG:` preamble.** The IEM CGI returns a block of comment lines before the CSV header. The downloader's response validator strips them before asserting the header shape; the transform uses polars' `comment_prefix="#"` to drop them at read time.
- **Trace precipitation.** IEM emits `T` as the sentinel for trace precip in `p01i`. The transform maps it to `0.0001 in` so the column stays numeric without losing the qualitative signal.
- **SPECI reports skip most RMK groups.** Special observations fire on rapid change and typically carry only the trigger-relevant fields. Expect `slp_mb_rmk`, `max_temp_*`, and precip groups to be null on SPECI rows even when hourly routine METARs have them.
- **`press_tendency` is not a python-metar attribute**, despite the library having a regex handler for the `5tppp` group. We parse it ourselves with a regex to pull both the magnitude and the character code.

## Market-relevance shortcut list

- **Daily high/low temperature contracts** → `max_temp_24hr_c` / `min_temp_24hr_c` at 00Z.
- **"Will it rain today" contracts** → `precip_24hr_in` at 00Z + `wxcodes` during the day.
- **Frontal-passage timing** → `presrr` / `presfr` / `press_tendency_3hr_mb`.
- **Convective onset contracts** → `tsb_minute` / `tse_minute` / `wxcodes =~ TS`.
- **Threshold-of-the-day contracts (e.g. "high > 75°F")** → `temp_c_rmk` (precise) instead of integer `tmpf`.

## Related

- [[Project Scope]] — Layer 3 definition and Phase 1 build order
- `scripts/iem_metar/download.py` — stage 1 fetcher
- `scripts/iem_metar/transform.py` — stage 2 CSV → Parquet + RMK decoding
- `scripts/iem_metar/validate.py` — post-run sanity check
- `.claude/skills/data-script/SKILL.md` — contract every data script follows

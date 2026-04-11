# `iem_asos_1min` — IEM ASOS 1-minute observations

Downloads per-minute surface weather observations from Iowa State's
[Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/) for
user-specified ASOS stations and date ranges. Free, no auth, no API key.

**Upstream form:** https://mesonet.agron.iastate.edu/request/asos/1min.phtml
**Upstream CGI:**  https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py

## Why this source

Per [[Project Scope]] and `CLAUDE.md` the 1-minute ASOS archive is our
**Layer 1 ground truth** (`y`) for target airports. It's the highest-quality
minute-level surface observation that exists, it's free, and IEM has 2000+
coverage for most major US airports with a ~24 hr delay.

## Station IDs — read this first

IEM uses the **3-character** station identifier (`NYC`, `LGA`, `JFK`, `SFO`,
`LAX`, `ORD`, `DFW`), **not** the 4-character ICAO form with the `K` prefix.
The downloader accepts both and auto-strips the prefix, so `KNYC` and `NYC`
are equivalent on the CLI — but the directory on disk and the values stored
in the manifest use the 3-char form.

Two NYC-area gotchas:

- **`NYC`** is the Central Park station (`NEW YORK CITY` in IEM's catalog),
  not JFK. It is a real ASOS installation with 1-minute data available.
- **`LGA`** is LaGuardia. `JFK` is separate and also available.

To sanity-check a station before backfilling a year of data, hit the form
page in a browser or curl a single hour and inspect the output:

```sh
curl -sS 'https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?station=NYC&tz=UTC&year1=2025&month1=6&day1=1&hour1=0&minute1=0&year2=2025&month2=6&day2=1&hour2=1&minute2=0&vars=tmpf&sample=1min&what=download&delim=comma&gis=no' | head
```

If the server replies `Unknown station provided: ...`, the ID is wrong or
the station doesn't have 1-min data archived.

## Variables

The CGI supports these 1-minute fields (exact names — case-sensitive):

| Field        | Meaning                               |
| ------------ | ------------------------------------- |
| `tmpf`       | Air temperature, °F                   |
| `dwpf`       | Dew point, °F                         |
| `sknt`       | 1-min wind speed, knots               |
| `drct`       | 1-min wind direction, degrees         |
| `gust_sknt`  | Gust speed, knots                     |
| `gust_drct`  | Gust direction, degrees               |
| `pres1`      | Pressure sensor 1, inches Hg          |
| `pres2`      | Pressure sensor 2, inches Hg          |
| `pres3`      | Pressure sensor 3, inches Hg          |
| `precip`     | Precip accumulation, inches           |
| `ptype`      | Present-weather type (short code)     |
| `vis1_coeff` | Visibility extinction coeff, sensor 1 |
| `vis1_nd`    | Visibility "not dark" flag, sensor 1  |
| `vis2_*`     | Sensor 2                              |
| `vis3_*`     | Sensor 3                              |

The script's **default variable list** is the numeric core that the
forecasting model will actually train on:

```
tmpf dwpf sknt drct gust_sknt gust_drct pres1 precip ptype
```

The redundant pressure channels (`pres2`, `pres3`) and the three visibility
sensors are omitted by default because they're sparsely populated and noisy
in the 1-min archive. Override with `--vars` if you need them.

## Output layout

```
data/raw/iem_asos_1min/
├── MANIFEST.json
├── download.log
├── LGA/
│   ├── 2025-06.csv
│   ├── 2025-07.csv
│   └── ...
└── NYC/
    └── ...
```

- One CSV per `(station, calendar month)`. File name is `YYYY-MM.csv`.
- Every CSV starts with the header
  `station,station_name,valid(UTC),<var>,<var>,...` and rows sorted by
  station then time.
- All timestamps are **UTC** (`tz=UTC` in the CGI request), matching the
  repo convention in `CLAUDE.md`.

## Idempotency and re-runs

File-level: a `(station, month)` CSV already present on disk is skipped on
subsequent runs, with two deliberate exceptions:

1. **The month containing "today" (UTC) is always re-fetched.** That month
   is necessarily partial — if you re-run the downloader tomorrow, you'd
   expect the newest rows to appear.
2. `--force` re-downloads every month in the requested range, overwriting
   existing CSVs.
3. `--fresh` deletes `data/raw/iem_asos_1min/` entirely before running.
   Implies `--force`.

This differs from the archive-style downloaders in this directory: there is
no single "the download is complete" manifest gate, because the dataset is
inherently incremental. The manifest records the most recent run's spec and
inventory; `status: complete` means "the most recent run finished without
error."

## CLI

```sh
uv run python scripts/download/iem_asos_1min/script.py \
    --stations NYC LGA \
    --start 2025-06-01 \
    --end 2026-04-10
```

Flags:

| Flag           | Default       | Meaning                                           |
| -------------- | ------------- | ------------------------------------------------- |
| `--stations`   | (required)    | One or more IEM station IDs (3-char).             |
| `--start`      | (required)    | Start date `YYYY-MM-DD`, UTC, inclusive.          |
| `--end`        | today (UTC)   | End date `YYYY-MM-DD`, UTC, inclusive.            |
| `--vars`       | default list  | Override the variable set.                        |
| `--force`      | off           | Re-download everything in range.                  |
| `--fresh`      | off           | Delete target dir first; implies `--force`.       |

## Chunking strategy

The CGI handles multi-month requests fine for a single station (benchmarked
at ~5 MB/month at 9 vars for two NY airports together, ~20 s wall time),
but the script chunks by **(station, month)** regardless for three reasons:

1. Per-file idempotency — the smallest sensible resume unit is one file.
2. Disk layout — per-station directories are the obvious shard for
   downstream model code that trains per-airport.
3. Politeness — smaller requests are more transient-failure tolerant, and
   the CGI is a shared public resource.

Between requests the script sleeps briefly and retries transient failures
with exponential backoff. On "Unknown station provided" it fails fast —
that's a user error, not a transient.

## Cost

**$0.** IEM is a free public service run by Iowa State with no API key, no
rate-limit cliff, and no egress fees. The only etiquette is "don't hammer
it" — which the chunking + sleep in this script already takes care of.

## Known quirks

- The CGI interprets the end time as "up to and including" the specified
  `(year2, month2, day2, hour2, minute2)`. We request `23:59` on the last
  day of each month so the final minute of the day is included.
- Response body starts with `Unknown station provided: <ID>` (200 OK, not
  an HTTP error) when the station ID is wrong. The script detects this and
  raises instead of writing a corrupt CSV.
- `ptype` is a short code, not a number (e.g. `NP`, `P?`, `?3`). Treat as
  categorical when loading into polars / pandas.
- Rows can appear as `M` (missing) for individual sensors — preserve as
  nulls in the interim layer, don't drop them.

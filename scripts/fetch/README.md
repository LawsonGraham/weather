# `scripts/fetch/` — fetcher script convention

Fetchers **discover** what exists in an upstream system.  They produce
small, structured catalogs (typically CSV) that drive downstream
downloaders.

Fetchers are distinct from downloaders:

| | fetcher | downloader |
| --- | --- | --- |
| **purpose** | discover identifiers | retrieve bulk data given identifiers |
| **output size** | small (KB–MB) | large (MB–GB) |
| **output lifetime** | semi-permanent (committed to repo) | raw snapshot (gitignored) |
| **cadence** | rarely (one-time / monthly) | per batch run |
| **example** | "what weather markets exist?" | "give me the trade history for this slug" |

## Layout

```
scripts/fetch/
├── README.md                                 (this file)
└── <fetcher_name>/
    ├── README.md
    └── script.py                             (self-contained — no shared utility)
```

Each fetcher is **self-contained** (no `_common.py`), same convention as
downloaders.  Copy an existing fetcher as your template when writing a
new one.

## Contract

1. **Output goes to a specified destination.**  Commonly the repo root
   (e.g. `weather-market-slugs/polymarket.csv`) when the result is a
   semi-permanent catalog, or `data/interim/<fetcher_name>/` when the
   result is an ephemeral step in a pipeline.
2. **Cache upstream API responses** under `data/interim/<fetcher_name>/raw/`
   (or a similar gitignored location) so re-runs don't re-hit the API.
   Accept `--refresh` to bypass the cache.
3. **Be idempotent.**  A second run with the same inputs should produce
   the same output.
4. **Log to stdout.**  Fetchers are usually short enough that a log file
   is unnecessary; stdout is sufficient.

## Dependencies

Stdlib only for HTTP (via `urllib.request`) until the project virtualenv
is bootstrapped.  `pyarrow`/`pandas` are available at system Python level
for local data wrangling.

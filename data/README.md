# `data/` — conventions

> This directory is **gitignored**. Only `data/README.md` is tracked (via a `!data/README.md` exception in `.gitignore`). Everything under `raw/`, `interim/`, `processed/` lives on disk only.

## Layout

```
data/
├── README.md         (this file — the convention)
├── raw/              (immutable originals — NEVER edit in place)
│   └── <source>/
│       ├── MANIFEST.json
│       ├── download.log
│       └── <source files...>
├── interim/          (cleaned / filtered / decoded intermediate data)
│   └── <step>/
└── processed/        (final, model- or backtest-ready artifacts)
    └── <task>/
```

### `raw/` — ground-truth originals

- One subdirectory per upstream source, named identically to the download script (`scripts/download/<source>.sh` → `data/raw/<source>/`).
- **Treat as immutable.** If you need to clean, filter, decode, or reformat, write the result to `interim/` or `processed/` and leave `raw/` alone.
- Every `raw/<source>/` has a `MANIFEST.json` (provenance + status) and a `download.log` (tee'd stdout from the download script).
- If `MANIFEST.json` is missing, the dataset was not downloaded via a tracked script and its provenance is unknown — re-run the canonical downloader.

### `interim/` — cleaned / decoded / filtered

- Outputs from transformation scripts that take `raw/` as input.
- Organize by **transformation step**, not by source, e.g. `interim/weather_markets_only/` for the weather-filter cut of the prediction-market dataset.
- May be deleted and regenerated freely — should always be reproducible from `raw/` via a tracked script.

### `processed/` — model / backtest ready

- The final, stable artifacts a model or backtest consumes.
- Organized by **downstream task**, e.g. `processed/airport_forecast_features/`, `processed/backtest_snapshots/`.
- Like `interim/`, these are reproducible from `raw/` and should not be hand-edited.

## MANIFEST.json schema

Every `raw/<source>/` directory has one. Schema (version `1`):

```json
{
  "manifest_version": 1,
  "source_name": "prediction_market_analysis",
  "description": "Kalshi + Polymarket markets & trades Parquet dataset",
  "upstream": {
    "repo": "https://github.com/jon-becker/prediction-market-analysis",
    "url":  "https://s3.jbecker.dev/data.tar.zst"
  },
  "script": {
    "path":    "scripts/download/prediction_market_analysis.sh",
    "version": 1
  },
  "download": {
    "started_at":    "2026-04-10T20:50:00Z",
    "completed_at":  "2026-04-10T21:15:00Z",
    "archive_bytes":   38654705664,
    "extracted_bytes": 107374182400,
    "archive_sha256":  null,
    "status":          "complete"
  },
  "target": {
    "raw_dir":  "data/raw/prediction_market_analysis",
    "contents": ["kalshi/", "polymarket/"]
  },
  "notes": "Dataset snapshot as of upstream main branch at fetch time."
}
```

### Status values

- `in_progress` — download started, not yet finished. Resuming is not automatic; decide whether to `--force` re-download or manually recover.
- `complete`    — download + extraction verified. Idempotent re-runs will skip.
- `failed`      — download or extraction errored. Re-run with `--force` after fixing the root cause.

## Idempotency rule

Every download script must check `MANIFEST.json.download.status` before doing any work:

- If `complete`: print "already downloaded, skipping" and exit 0.
- If `in_progress` or `failed`: print a warning and require `--force` to retry (prevents accidental double-downloads on shared state).
- If no manifest: proceed with a fresh download.

## Rules of the road

1. **Never hand-edit anything in `raw/`.** If you need a transformation, write a script that emits to `interim/` or `processed/`.
2. **Always `MANIFEST.json`.** If you ingested something without one, document it and back-fill.
3. **One source per subdirectory of `raw/`.** If a download produces multiple logical sources, split them into multiple `raw/<source>/` dirs and manifests.
4. **Log everything.** Every download script tees stdout to `download.log`.
5. **Assume others will delete `data/` freely.** Everything must be reproducible from tracked scripts. If a step can't be reproduced, it shouldn't live here.

See `scripts/download/README.md` for the download-script convention.

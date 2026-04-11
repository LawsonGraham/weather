# `scripts/download/` — download script convention

Every data source we ingest has **exactly one** downloader here, and each
downloader lives in its own subfolder.  That subfolder is the canonical way
to produce `data/raw/<source>/`.  If you downloaded something by hand or via
an upstream script, either wrap it in a downloader here or remove it.

## Layout

```
scripts/download/
├── README.md                              (this file)
└── <source_name>/
    ├── README.md                          (source-specific notes: upstream URLs, quirks, schema corrections)
    └── script.py                          (self-contained Python downloader)
```

**Rule:** `<source_name>` in the scripts folder must exactly match the
directory name under `data/raw/<source_name>/`.  Matching names make the
relationship between script and data obvious at a glance.

## Contract — every downloader must

1. **Target a single source** named `<source_name>` and populate
   `data/raw/<source_name>/`.
2. **Write `data/raw/<source>/MANIFEST.json`** with `status: "in_progress"`
   at start, `"complete"` on success, `"failed"` on any error.  Schema:
   see `data/README.md`.
3. **Tee all output** to `data/raw/<source>/download.log` via the inlined
   `run_streamed()` subprocess helper.
4. **Be idempotent.** Before doing any work, read the existing manifest.
   If `status == "complete"`, print a skip message and exit 0.  If
   `in_progress` or `failed`, refuse to run unless `--force` is passed.
5. **Support `--force`** (bypass idempotency; keep partial archive so the
   downloader can resume) and **`--fresh`** (also delete the partial;
   implies `--force`).
6. **Fail loudly.**  Wrap the body in `try/except BaseException` so any
   crash flips the manifest to `"failed"` before re-raising.
7. **Check preconditions up front:** required binaries (`require_cmd`),
   free disk (`require_disk_gib`), network reachability if relevant.
8. **Never touch `data/interim/` or `data/processed/`.**  Downloads are
   strictly raw.

## No shared utility module

Each downloader is **self-contained** — all helpers inlined.  There is no
`_common.py` import.  An earlier version of this convention had a shared
`_common.py` with `DownloadManifest` as a context manager.  We removed it
because:

- Every downloader then depended on a hidden module you had to remember
  existed.  Self-contained scripts are more obvious.
- Scripts diverge over time anyway (API pagination, auth, schema quirks);
  the shared API becomes a compatibility burden.
- The helper surface is small (~150 lines of plumbing per downloader), so
  duplication is cheap and the clarity wins.

**When writing a new downloader: copy
`scripts/download/prediction_market_analysis/script.py` as your template.**
Rename, adjust the source metadata constants, and swap the body of
`download_archive()` / `extract_archive()` for whatever your upstream
needs.  The skeleton sections (CLI args, idempotency gate, manifest
lifecycle, logging, subprocess streaming, preconditions) stay identical.

## Dependencies

Download scripts use **Python stdlib only** until the project virtualenv
is bootstrapped.  Shell out to `aria2c` / `curl` / `zstd` / `tar` /
`gunzip` / `xz` via `subprocess` for heavy I/O — reimplementing those in
pure Python is slower and less robust.

Downloaders that need to paginate a REST API (Polymarket Gamma, Kalshi,
Synoptic, IEM) can use `urllib.request` (stdlib) for now; we'll switch to
`httpx` once the project virtualenv is up and `httpx` is a tracked
dependency.

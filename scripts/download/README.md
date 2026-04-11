# `scripts/download/` — download script convention

Every data source we ingest has **exactly one** downloader here, and each
downloader lives in its own subfolder. That subfolder is the canonical way to
produce `data/raw/<source>/`. If you downloaded something by hand or via an
upstream script, either wrap it in a downloader here or remove it.

## Layout

```
scripts/download/
├── README.md                              (this file — the contract)
├── _common.py                             (stdlib-only shared helpers)
└── <source_name>/                         (one folder per source; same name as data/raw/<source_name>/)
    ├── README.md                          (source-specific notes: upstream URLs, quirks, schema corrections)
    ├── script.py                          (the downloader — always Python)
    └── (any source-specific helpers, schemas, sample files, etc.)
```

**Rule:** `<source_name>` in the scripts folder must exactly match the
directory name under `data/raw/`. Matching names make the relationship between
script and data obvious at a glance.

## Contract — every downloader must

1. **Target a single source** named `<source_name>` and populate
   `data/raw/<source_name>/`.
2. **Write `data/raw/<source>/MANIFEST.json`** following the v1 schema (see
   `data/README.md`). Start with `status: "in_progress"`, flip to
   `"complete"` on success, or `"failed"` on error. All three state
   transitions are handled automatically by the `DownloadManifest` context
   manager in `_common.py`.
3. **Tee all output** to `data/raw/<source>/download.log` (the
   `configure_logging()` helper handles this).
4. **Be idempotent.** Before doing any work, read the existing manifest via
   `DownloadManifest.check_already_complete(...)`. If `complete`, print a
   skip message and exit 0. If `in_progress` or `failed`, refuse to run
   unless `--force` is passed.
5. **Support `--force`** (bypass idempotency; keep any partial archive so
   the downloader can resume) and **`--fresh`** (also delete the partial;
   implies `--force`).
6. **Fail loudly.** Unhandled exceptions bubble out of the manifest context
   manager, which flips the manifest to `failed` and re-raises.
7. **Check preconditions up front:** required binaries via `require_cmd(...)`,
   free disk via `require_disk_gib(...)`, network reachability if relevant.
8. **Never touch `data/interim/` or `data/processed/`.** Downloads are
   strictly raw.

## Why Python (and not bash)

- Manifest I/O, JSON handling, string parsing, and error recovery are
  cleaner in Python than in bash. The bash version of `_common.sh` had a
  `python3 -c` embedded for every manifest write — we were already
  half-Python.
- Some downloads will paginate APIs (Kalshi, Polymarket, Synoptic, IEM
  ASOS); those must be Python anyway, so consistency wins.
- The context-manager pattern (`with DownloadManifest(...) as m: ...`) is
  significantly less error-prone than bash's `trap EXIT` approach — the
  manifest lifecycle is scoped to the block, and there's no risk of
  forgetting to call `trap_failure_for`.
- Bash is still a fine tool for shelling out to `aria2c`/`curl`/`zstd`/`tar`
  — we just invoke those from Python via `subprocess` + `run_and_stream()`.

## `_common.py` — shared helpers

Import sibling-style from a per-source script:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import (
    DownloadManifest,
    configure_logging,
    require_cmd,
    require_disk_gib,
    dir_bytes,
    file_bytes,
    run_and_stream,
)
```

Provides:

- **`DownloadManifest`** — context manager that writes the initial
  in-progress manifest on entry, flips to `failed` on exception or forgotten
  `complete()`, and flips to `complete` when the body calls
  `manifest.complete(...)`. Also exposes `check_already_complete()` for the
  idempotency gate.
- **`configure_logging(log_path)`** — sets up the `download` logger to tee
  timestamped lines to stdout and the specified file.
- **`require_cmd(*cmds)`** — exit with a clean error if any binary is
  missing.
- **`require_disk_gib(n, path)`** — exit if the filesystem backing `path`
  has less than `n` GiB free; logs the actual free space on success.
- **`file_bytes(path)`** / **`dir_bytes(path)`** — portable size queries.
- **`run_and_stream(cmd, log_path)`** — run a subprocess and tee its
  combined stdout/stderr to both the terminal and the log file; raises
  `CalledProcessError` on non-zero exit.
- **`utc_now()`** — ISO 8601 UTC timestamp string.

## Writing a new downloader — skeleton

```python
#!/usr/bin/env python3
"""Download the <source_name> dataset from <upstream>."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _common import (
    DownloadManifest,
    configure_logging,
    dir_bytes,
    file_bytes,
    require_cmd,
    require_disk_gib,
    run_and_stream,
)

SOURCE_NAME = "<source_name>"
UPSTREAM_REPO = "https://..."
UPSTREAM_URL  = "https://..."
SCRIPT_PATH = f"scripts/download/{SOURCE_NAME}/script.py"
SCRIPT_VERSION = 1
DESCRIPTION = "<one sentence>"
REQUIRED_DISK_GIB = 10   # tune per source

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR   = REPO_ROOT / "data" / "raw" / SOURCE_NAME
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
LOG_PATH  = RAW_DIR / "download.log"
TARGET_REL = f"data/raw/{SOURCE_NAME}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=f"Download the {SOURCE_NAME} dataset.")
    p.add_argument("--force", action="store_true", help="bypass idempotency; keep partial")
    p.add_argument("--fresh", action="store_true", help="delete partial then retry (implies --force)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    force = args.force or args.fresh

    if not force and MANIFEST_PATH.exists() and DownloadManifest.check_already_complete(MANIFEST_PATH, force=False):
        print(f"{SOURCE_NAME} already downloaded; skipping. Pass --force to re-download.")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    log = configure_logging(LOG_PATH)

    require_cmd("curl")               # tune per source
    require_disk_gib(REQUIRED_DISK_GIB, REPO_ROOT)

    with DownloadManifest(
        manifest_path=MANIFEST_PATH,
        source_name=SOURCE_NAME,
        description=DESCRIPTION,
        upstream_repo=UPSTREAM_REPO,
        upstream_url=UPSTREAM_URL,
        script_path=SCRIPT_PATH,
        script_version=SCRIPT_VERSION,
        target_rel=TARGET_REL,
    ) as manifest:
        # ... source-specific download + extract logic ...
        manifest.complete(archive_bytes=..., extracted_bytes=..., contents=[...])
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## Dependencies

Download scripts use **Python stdlib only** until we set up a project
virtualenv. Shell out to `aria2c`/`curl`/`zstd`/`tar`/`gunzip`/`xz` via
`subprocess` for the heavy I/O — reimplementing those in pure Python is
slower and less robust.

Downloaders that need to paginate a REST API (Kalshi, Polymarket, Synoptic,
IEM) will switch to `httpx` once the project virtualenv is bootstrapped; at
that point the download scripts can be promoted from stdlib to
`pyproject.toml`-tracked dependencies.

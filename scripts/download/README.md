# `scripts/download/` — download script convention

Every data source we ingest has **exactly one** download script here. That script is the canonical way to produce `data/raw/<source>/`. If you downloaded something by hand or via an upstream script, either wrap it in a download script here or remove it.

## Contract

Every download script must:

1. **Target a single source** named `<source>` and populate `data/raw/<source>/`. The script file is `<source>.sh` or `<source>.py` — same basename as the raw dir.
2. **Write `data/raw/<source>/MANIFEST.json`** following the schema documented in `data/README.md`. Start with `status: "in_progress"` at the start of a run, mark `complete` on success, `failed` on error (via an EXIT trap).
3. **Tee all output** to `data/raw/<source>/download.log`.
4. **Be idempotent**: before doing any work, read the existing manifest (if any). If `download.status == "complete"`, print "already downloaded, skipping" and exit 0. If `in_progress` or `failed`, print a warning and exit 1 unless `--force` is passed.
5. **Support `--force`** to bypass the idempotency check and re-download.
6. **Fail loudly**: `set -euo pipefail` in bash, unhandled exceptions in Python. Never silently swallow errors.
7. **Check preconditions up front**: required binaries, required disk space (source-dependent), network reachability. Use the helpers in `_common.sh`.
8. **Never touch `data/interim/` or `data/processed/`.** Downloads are strictly raw.

## Shared helpers — `_common.sh`

Source from bash scripts via:

```bash
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$REPO_ROOT/scripts/download/_common.sh"
```

Provides:

- `log INFO|WARN|ERROR "message"` — timestamped line to stdout AND to the log file set by `set_log_file`
- `set_log_file <path>` — route subsequent `log` output to an additional file
- `die "message"` — log ERROR and `exit 1`
- `require_cmd <cmd>...` — exit if any binary is missing
- `require_disk_gib <gib> <path>` — exit if `path`'s filesystem has less than `gib` GiB free
- `trap_failure_for <manifest_path>` — install an EXIT trap that sets the manifest's status to `failed` on abnormal exit
- `manifest_init --source <name> --description <desc> --repo <url> --url <url> --script <path> --version <n> --target <raw_dir>` — write initial MANIFEST.json with `download.status = "in_progress"`
- `manifest_set <manifest_path> <dot.path> <value>` — update a field (e.g. `manifest_set path download.status complete`)
- `manifest_complete <manifest_path>` — set status to `complete`, fill `completed_at`, sizes
- `bytes_of <path>` — portable byte count
- `dir_bytes_of <path>` — total bytes under a directory
- `utc_now` — ISO 8601 UTC timestamp

All JSON manipulation uses `python3` (always present on macOS) rather than `jq` to avoid an extra dependency.

## Script skeleton

```bash
#!/bin/bash
# scripts/download/<source>.sh — one-line description
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$REPO_ROOT/scripts/download/_common.sh"

SOURCE_NAME="<source>"
UPSTREAM_REPO="https://github.com/..."
UPSTREAM_URL="https://..."
SCRIPT_PATH="scripts/download/${SOURCE_NAME}.sh"
SCRIPT_VERSION=1
DESCRIPTION="<one sentence>"
REQUIRED_DISK_GIB=50     # set per source

RAW_DIR="$REPO_ROOT/data/raw/$SOURCE_NAME"
MANIFEST="$RAW_DIR/MANIFEST.json"
LOG_FILE="$RAW_DIR/download.log"

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        *) die "unknown argument: $arg" ;;
    esac
done

# Preconditions
require_cmd curl python3 tar zstd
require_disk_gib "$REQUIRED_DISK_GIB" "$REPO_ROOT"

# Idempotency check
if [ -f "$MANIFEST" ] && [ "$FORCE" -eq 0 ]; then
    status=$(python3 -c "import json; print(json.load(open('$MANIFEST'))['download']['status'])")
    case "$status" in
        complete)    log INFO "already downloaded ($SOURCE_NAME), skipping"; exit 0 ;;
        in_progress) die "manifest shows in_progress — another run may be active, or it crashed. Use --force to retry." ;;
        failed)      die "previous run failed. Investigate, then re-run with --force." ;;
    esac
fi

mkdir -p "$RAW_DIR"
set_log_file "$LOG_FILE"
log INFO "starting download of $SOURCE_NAME"

manifest_init \
    --source "$SOURCE_NAME" \
    --description "$DESCRIPTION" \
    --repo "$UPSTREAM_REPO" \
    --url "$UPSTREAM_URL" \
    --script "$SCRIPT_PATH" \
    --version "$SCRIPT_VERSION" \
    --target "data/raw/$SOURCE_NAME"

trap_failure_for "$MANIFEST"

# --- source-specific download + extract goes here ---

manifest_complete "$MANIFEST"
log INFO "done: $SOURCE_NAME"
```

## Python flavor

Python download scripts follow the same contract: `MANIFEST.json` with the same schema, `download.log` in the same place, `--force` flag, idempotency check, trap/except to mark failed. A Python equivalent of `_common.sh` will live at `scripts/download/_common.py` when the first Python downloader is written (not now).

#!/bin/bash
# scripts/download/prediction_market_analysis.sh
#
# Pulls jon-becker/prediction-market-analysis's pre-collected Parquet dataset
# (Kalshi + Polymarket markets & trades, ~36 GiB compressed) and extracts it
# into data/raw/prediction_market_analysis/.
#
# Upstream: https://github.com/jon-becker/prediction-market-analysis
# Archive:  https://s3.jbecker.dev/data.tar.zst
#
# After extraction the directory layout is:
#   data/raw/prediction_market_analysis/
#   ├── MANIFEST.json
#   ├── download.log
#   ├── kalshi/
#   │   ├── markets/
#   │   └── trades/
#   └── polymarket/
#       ├── blocks/
#       ├── markets/
#       └── trades/
#
# Usage:
#   scripts/download/prediction_market_analysis.sh           # idempotent
#   scripts/download/prediction_market_analysis.sh --force   # re-download

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$REPO_ROOT/scripts/download/_common.sh"

SOURCE_NAME="prediction_market_analysis"
UPSTREAM_REPO="https://github.com/jon-becker/prediction-market-analysis"
UPSTREAM_URL="https://s3.jbecker.dev/data.tar.zst"
SCRIPT_PATH="scripts/download/${SOURCE_NAME}.sh"
SCRIPT_VERSION=1
DESCRIPTION="Kalshi + Polymarket markets & trades Parquet dataset (~36 GiB compressed), from jon-becker/prediction-market-analysis"
# Budget: 36 GiB archive + ~150 GiB extracted headroom. 200 is a cautious number
# that matches what we'll actually need on the host filesystem.
REQUIRED_DISK_GIB=200

RAW_DIR="$REPO_ROOT/data/raw/$SOURCE_NAME"
MANIFEST="$RAW_DIR/MANIFEST.json"
LOG_FILE="$RAW_DIR/download.log"
ARCHIVE_PATH="$RAW_DIR/data.tar.zst"

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h|--help)
            sed -n '1,30p' "$0"
            exit 0
            ;;
        *) die "unknown argument: $arg" ;;
    esac
done

# --- preconditions -------------------------------------------------------
require_cmd curl python3 tar zstd
require_disk_gib "$REQUIRED_DISK_GIB" "$REPO_ROOT"

# --- idempotency check --------------------------------------------------
if [ -f "$MANIFEST" ] && [ "$FORCE" -eq 0 ]; then
    status="$(python3 -c "import json; print(json.load(open('$MANIFEST'))['download']['status'])")"
    case "$status" in
        complete)
            echo "$(utc_now) [INFO] $SOURCE_NAME already downloaded (status=complete); skipping. Pass --force to re-download."
            exit 0
            ;;
        in_progress)
            die "manifest status is in_progress — another run may be active, or a previous run crashed. Investigate $MANIFEST, then re-run with --force."
            ;;
        failed)
            die "previous run failed (see $LOG_FILE). Investigate and re-run with --force."
            ;;
        *)
            die "manifest has unexpected status '$status' in $MANIFEST"
            ;;
    esac
fi

# --- set up raw dir + logging -------------------------------------------
mkdir -p "$RAW_DIR"
set_log_file "$LOG_FILE"
log INFO "starting download of $SOURCE_NAME"
log INFO "upstream: $UPSTREAM_URL"
log INFO "target:   $RAW_DIR"
log INFO "force:    $FORCE"

# --- initialize manifest + trap failure ---------------------------------
manifest_init \
    --source "$SOURCE_NAME" \
    --description "$DESCRIPTION" \
    --repo "$UPSTREAM_REPO" \
    --url "$UPSTREAM_URL" \
    --script "$SCRIPT_PATH" \
    --version "$SCRIPT_VERSION" \
    --target "data/raw/$SOURCE_NAME"

trap_failure_for "$MANIFEST"

# --- download -----------------------------------------------------------
# If --force and an archive already exists, nuke it and start clean.
if [ "$FORCE" -eq 1 ] && [ -f "$ARCHIVE_PATH" ]; then
    log WARN "--force: removing existing archive $ARCHIVE_PATH"
    rm -f "$ARCHIVE_PATH"
fi

log INFO "downloading (this is ~36 GiB and may take a while)..."

# Prefer aria2c for parallel chunks if installed, otherwise curl. curl -C -
# gives resume-on-reconnect semantics for free.
if command -v aria2c >/dev/null 2>&1; then
    log INFO "using aria2c with 16 parallel connections"
    aria2c -x 16 -s 16 -c --dir="$RAW_DIR" --out="data.tar.zst" "$UPSTREAM_URL" \
        2>&1 | tee -a "$LOG_FILE"
else
    log INFO "using curl (install aria2c for faster parallel download)"
    # -L follow redirects, -C - resume, --retry for transient errors, -# progress bar
    curl -L -C - --retry 5 --retry-delay 10 -# -o "$ARCHIVE_PATH" "$UPSTREAM_URL" \
        2>&1 | tee -a "$LOG_FILE"
fi

archive_bytes="$(bytes_of "$ARCHIVE_PATH")"
log INFO "download complete: $archive_bytes bytes"
manifest_set "$MANIFEST" download.archive_bytes "$archive_bytes"

# --- extract ------------------------------------------------------------
log INFO "extracting (zstd → tar)..."

# The upstream archive extracts to a top-level ./data/ tree (kalshi/, polymarket/, ...).
# We want its contents under RAW_DIR directly, not data/data/. Extract into a
# staging dir, then move contents up.
STAGE_DIR="$RAW_DIR/.extract_stage"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

( cd "$STAGE_DIR" && zstd -d "$ARCHIVE_PATH" --stdout | tar -xf - ) \
    2>&1 | tee -a "$LOG_FILE"

# Figure out the top-level directory the archive produced. Upstream's script
# extracts into a `data/` tree, but don't hardcode that — use whatever the
# archive actually contains.
shopt -s nullglob dotglob
top_entries=("$STAGE_DIR"/*)
shopt -u nullglob dotglob

if [ ${#top_entries[@]} -eq 0 ]; then
    die "extraction produced no files — check $LOG_FILE"
fi

if [ ${#top_entries[@]} -eq 1 ] && [ -d "${top_entries[0]}" ]; then
    # Single top-level directory (expected: `data/`). Move its contents up one
    # level into RAW_DIR.
    inner="${top_entries[0]}"
    log INFO "archive root is $(basename "$inner"); promoting its contents into $RAW_DIR"
    shopt -s nullglob dotglob
    for entry in "$inner"/*; do
        dest="$RAW_DIR/$(basename "$entry")"
        if [ -e "$dest" ] && [ "$FORCE" -eq 0 ]; then
            die "refusing to overwrite existing $dest (run with --force)"
        fi
        rm -rf "$dest"
        mv "$entry" "$dest"
    done
    shopt -u nullglob dotglob
else
    # Multiple top-level entries — move each up.
    log INFO "archive has ${#top_entries[@]} top-level entries; promoting directly into $RAW_DIR"
    for entry in "${top_entries[@]}"; do
        dest="$RAW_DIR/$(basename "$entry")"
        if [ -e "$dest" ] && [ "$FORCE" -eq 0 ]; then
            die "refusing to overwrite existing $dest (run with --force)"
        fi
        rm -rf "$dest"
        mv "$entry" "$dest"
    done
fi

rm -rf "$STAGE_DIR"

# --- cleanup + record final state ---------------------------------------
log INFO "removing archive $ARCHIVE_PATH"
rm -f "$ARCHIVE_PATH"

# Record what top-level directories ended up in the raw dir (excluding
# manifest/log/sentinel files).
contents_json="$(python3 - "$RAW_DIR" <<'PY'
import json, os, sys
raw = sys.argv[1]
skip = {"MANIFEST.json", "download.log", ".extract_stage"}
entries = sorted(
    e + ("/" if os.path.isdir(os.path.join(raw, e)) else "")
    for e in os.listdir(raw)
    if e not in skip and not e.startswith(".")
)
print(json.dumps(entries))
PY
)"
manifest_set "$MANIFEST" target.contents "$contents_json"

extracted_bytes="$(dir_bytes_of "$RAW_DIR")"
log INFO "extracted tree size: $extracted_bytes bytes"

manifest_complete "$MANIFEST" "$archive_bytes" "$extracted_bytes"
log INFO "done: $SOURCE_NAME"

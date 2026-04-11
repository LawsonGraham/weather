#!/bin/bash
# scripts/download/_common.sh
# Shared helpers for download scripts. Source from downloaders:
#   REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
#   source "$REPO_ROOT/scripts/download/_common.sh"
#
# See scripts/download/README.md for the download script contract.

# Do not `set -e` here — individual scripts set their own options. We only
# define functions here.

_COMMON_LOG_FILE=""

utc_now() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

set_log_file() {
    _COMMON_LOG_FILE="$1"
    mkdir -p "$(dirname "$_COMMON_LOG_FILE")"
    touch "$_COMMON_LOG_FILE"
}

log() {
    local level="$1"
    shift
    local msg="$*"
    local line
    line="$(utc_now) [$level] $msg"
    echo "$line"
    if [ -n "$_COMMON_LOG_FILE" ]; then
        echo "$line" >> "$_COMMON_LOG_FILE"
    fi
}

die() {
    log ERROR "$*"
    exit 1
}

require_cmd() {
    local missing=()
    for cmd in "$@"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        die "missing required binaries: ${missing[*]}. Install them and retry."
    fi
}

# require_disk_gib <gib> <path>
# Exit if the filesystem backing <path> has less than <gib> GiB free.
require_disk_gib() {
    local need_gib="$1"
    local path="$2"
    # df -k gives 1024-byte blocks, portable enough for macOS + Linux.
    local avail_kb
    avail_kb="$(df -k "$path" | awk 'NR==2 {print $4}')"
    local avail_gib=$(( avail_kb / 1024 / 1024 ))
    if [ "$avail_gib" -lt "$need_gib" ]; then
        die "insufficient disk: need ${need_gib} GiB on $path, have ${avail_gib} GiB"
    fi
    log INFO "disk check ok: ${avail_gib} GiB free on $path (need ${need_gib})"
}

# bytes_of <path> — byte count of a single file
bytes_of() {
    local path="$1"
    if [ ! -e "$path" ]; then
        echo 0
        return
    fi
    # macOS stat uses -f; GNU stat uses -c. Try both.
    if stat -f%z "$path" >/dev/null 2>&1; then
        stat -f%z "$path"
    else
        stat -c%s "$path"
    fi
}

# dir_bytes_of <path> — total bytes under a directory
dir_bytes_of() {
    local path="$1"
    if [ ! -d "$path" ]; then
        echo 0
        return
    fi
    # du -sk gives 1024-byte blocks; convert to bytes.
    local kb
    kb="$(du -sk "$path" | awk '{print $1}')"
    echo $(( kb * 1024 ))
}

# manifest_init — write initial MANIFEST.json with status=in_progress
#
# Flags:
#   --source <name>
#   --description <desc>
#   --repo <upstream repo url>
#   --url <upstream data url>
#   --script <script path, relative to repo root>
#   --version <int>
#   --target <raw_dir, relative to repo root>
#
# Writes to data/raw/<source>/MANIFEST.json under $REPO_ROOT.
manifest_init() {
    local source_name="" description="" repo="" url="" script_path="" script_version="" target=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --source)      source_name="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --repo)        repo="$2"; shift 2 ;;
            --url)         url="$2"; shift 2 ;;
            --script)      script_path="$2"; shift 2 ;;
            --version)     script_version="$2"; shift 2 ;;
            --target)      target="$2"; shift 2 ;;
            *) die "manifest_init: unknown arg $1" ;;
        esac
    done

    local raw_dir="$REPO_ROOT/$target"
    local manifest="$raw_dir/MANIFEST.json"
    mkdir -p "$raw_dir"

    python3 - "$manifest" "$source_name" "$description" "$repo" "$url" "$script_path" "$script_version" "$target" "$(utc_now)" <<'PY'
import json, sys
(manifest, source_name, description, repo, url, script_path, script_version, target, started_at) = sys.argv[1:]
doc = {
    "manifest_version": 1,
    "source_name": source_name,
    "description": description,
    "upstream": {"repo": repo, "url": url},
    "script": {"path": script_path, "version": int(script_version)},
    "download": {
        "started_at": started_at,
        "completed_at": None,
        "archive_bytes": None,
        "extracted_bytes": None,
        "archive_sha256": None,
        "status": "in_progress",
    },
    "target": {"raw_dir": target, "contents": []},
    "notes": "",
}
with open(manifest, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PY
    log INFO "manifest initialized: $manifest (status=in_progress)"
}

# manifest_set <manifest_path> <dot.path> <value_json>
# Set a field by dotted path. Value is parsed as JSON; if parse fails, used as string.
manifest_set() {
    local manifest="$1"
    local dot_path="$2"
    local value="$3"
    python3 - "$manifest" "$dot_path" "$value" <<'PY'
import json, sys
manifest, dot_path, raw_value = sys.argv[1:]
try:
    value = json.loads(raw_value)
except json.JSONDecodeError:
    value = raw_value
with open(manifest) as f:
    doc = json.load(f)
cursor = doc
parts = dot_path.split(".")
for p in parts[:-1]:
    cursor = cursor.setdefault(p, {})
cursor[parts[-1]] = value
with open(manifest, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PY
}

# manifest_complete <manifest_path> [archive_bytes] [extracted_bytes]
manifest_complete() {
    local manifest="$1"
    local archive_bytes="${2:-}"
    local extracted_bytes="${3:-}"
    local completed_at
    completed_at="$(utc_now)"

    manifest_set "$manifest" download.completed_at "\"$completed_at\""
    manifest_set "$manifest" download.status "\"complete\""
    if [ -n "$archive_bytes" ]; then
        manifest_set "$manifest" download.archive_bytes "$archive_bytes"
    fi
    if [ -n "$extracted_bytes" ]; then
        manifest_set "$manifest" download.extracted_bytes "$extracted_bytes"
    fi
    log INFO "manifest marked complete: $manifest"
}

# trap_failure_for <manifest_path>
# Install an EXIT trap that marks the manifest as failed if the script exits
# non-zero. Should be called AFTER manifest_init, so status=in_progress is the
# baseline state until the script explicitly calls manifest_complete.
trap_failure_for() {
    local manifest="$1"
    _TRAP_MANIFEST="$manifest"
    trap '_common_on_exit $?' EXIT
}

_common_on_exit() {
    local exit_code="$1"
    if [ "$exit_code" -ne 0 ] && [ -n "${_TRAP_MANIFEST:-}" ] && [ -f "$_TRAP_MANIFEST" ]; then
        # Only flip in_progress → failed; leave terminal states alone.
        local current
        current="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['download']['status'])" "$_TRAP_MANIFEST" 2>/dev/null || echo unknown)"
        if [ "$current" = "in_progress" ]; then
            manifest_set "$_TRAP_MANIFEST" download.status '"failed"'
            log ERROR "script exited with code $exit_code; manifest marked failed"
        fi
    fi
}

#!/usr/bin/env python3
"""Download the prediction-market-analysis Kalshi + Polymarket dataset.

Upstream: https://github.com/jon-becker/prediction-market-analysis
Archive:  https://s3.jbecker.dev/data.tar.zst  (~36 GiB compressed)

Lands under ``data/raw/prediction_market_analysis/`` with this layout::

    data/raw/prediction_market_analysis/
    ├── MANIFEST.json
    ├── download.log
    ├── kalshi/
    │   ├── markets/
    │   └── trades/
    └── polymarket/
        ├── blocks/
        ├── legacy_trades/
        ├── markets/
        └── trades/

Usage::

    python3 scripts/download/prediction_market_analysis/script.py           # idempotent
    python3 scripts/download/prediction_market_analysis/script.py --force   # resume or retry
    python3 scripts/download/prediction_market_analysis/script.py --fresh   # delete partial + retry

Implementation notes:

- We shell out to ``aria2c`` (preferred) or ``curl`` for the download because
  reimplementing HTTP/2 resume semantics in Python would be a waste.  Both
  tools handle the Cloudflare R2 HTTP/2 stream resets far better than
  ``urllib.request.urlretrieve``.
- We shell out to ``zstd | tar`` for extraction for the same reason.
- Everything else (manifest, logging, idempotency, trap-on-failure) is plain
  Python stdlib via ``_common.DownloadManifest``.

Previous bash version (``scripts/download/prediction_market_analysis.sh`` +
``scripts/download/_common.sh``) was removed; Python is the new convention
for all download scripts.  See ``scripts/download/README.md``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Make sibling module importable without turning the scripts/ dir into a package.
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

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "prediction_market_analysis"
UPSTREAM_REPO = "https://github.com/jon-becker/prediction-market-analysis"
UPSTREAM_URL = "https://s3.jbecker.dev/data.tar.zst"
SCRIPT_PATH = f"scripts/download/{SOURCE_NAME}/script.py"
# Bump when the download *logic* changes in a way that affects what's on disk.
# v1 — initial bash script
# v2 — bash script, fixed --force to preserve partial (enables resume)
# v3 — ported to Python, per-source folder layout
SCRIPT_VERSION = 3
DESCRIPTION = (
    "Kalshi + Polymarket markets & trades Parquet dataset (~36 GiB compressed), "
    "from jon-becker/prediction-market-analysis"
)
# Budget: ~36 GiB archive + ~55 GiB extracted + headroom.  200 is cautious but
# keeps us honest about space on the target filesystem.
REQUIRED_DISK_GIB = 200


# --- path resolution ------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
LOG_PATH = RAW_DIR / "download.log"
ARCHIVE_PATH = RAW_DIR / "data.tar.zst"
STAGE_DIR = RAW_DIR / ".extract_stage"
TARGET_REL = f"data/raw/{SOURCE_NAME}"


# --- CLI ------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            f"Download the {SOURCE_NAME} dataset (~36 GiB) from {UPSTREAM_URL} into {TARGET_REL}/."
        )
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="bypass the 'already downloaded' idempotency check; keeps any "
        "existing partial archive so the downloader can resume",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help="also delete any existing partial archive before retrying (implies --force)",
    )
    return p.parse_args()


# --- download + extract helpers ------------------------------------------- #


def _download(log) -> None:
    """Pull the archive.  Prefers ``aria2c`` (parallel connections + robust
    HTTP/2 retry) and falls back to ``curl --http1.1`` when aria2c isn't
    installed.
    """
    if ARCHIVE_PATH.exists():
        log.info(
            "found existing partial archive (%d bytes); will attempt to resume",
            file_bytes(ARCHIVE_PATH),
        )

    if shutil.which("aria2c"):
        log.info("using aria2c with 16 parallel connections (resume: -c)")
        cmd = [
            "aria2c",
            "--continue=true",
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=1M",
            "--max-tries=10",
            "--retry-wait=10",
            "--connect-timeout=30",
            "--timeout=60",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            f"--dir={RAW_DIR}",
            "--out=data.tar.zst",
            UPSTREAM_URL,
        ]
    else:
        log.info("using curl (install aria2c for faster parallel download)")
        cmd = [
            "curl",
            "-L",
            "--http1.1",
            "-C",
            "-",
            "--retry",
            "10",
            "--retry-delay",
            "10",
            "--connect-timeout",
            "30",
            "-#",
            "-o",
            str(ARCHIVE_PATH),
            UPSTREAM_URL,
        ]
    run_and_stream(cmd, LOG_PATH)


def _extract(log) -> None:
    """Decompress the archive into a staging dir, then promote its contents
    up into RAW_DIR so we end up with ``data/raw/prediction_market_analysis/{kalshi,polymarket}``
    rather than a nested ``data/`` directory.
    """
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True)

    log.info("extracting (zstd → tar) into staging dir %s", STAGE_DIR)
    # Use a shell pipeline to avoid buffering the entire 50+ GiB stream
    # through Python memory.  Quoting is deliberate; we control all values.
    cmd = f'zstd -d "{ARCHIVE_PATH}" --stdout | tar -xf - -C "{STAGE_DIR}"'
    subprocess.run(["bash", "-c", cmd], check=True)

    # Figure out the archive's top-level shape and promote its contents.
    top = sorted(STAGE_DIR.iterdir())
    if not top:
        raise RuntimeError(f"extraction produced no files — check {LOG_PATH}")

    if len(top) == 1 and top[0].is_dir():
        inner = top[0]
        log.info("archive root is %s/; promoting its contents", inner.name)
        _promote(inner, log)
    else:
        log.info("archive has %d top-level entries; promoting directly", len(top))
        _promote(STAGE_DIR, log)

    shutil.rmtree(STAGE_DIR, ignore_errors=True)


def _promote(src: Path, log) -> None:
    """Move every child of *src* up one level into ``RAW_DIR``."""
    for entry in sorted(src.iterdir()):
        dest = RAW_DIR / entry.name
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        shutil.move(str(entry), str(dest))
        log.info("promoted: %s", entry.name)


def _top_level_contents() -> list[str]:
    """List RAW_DIR's top-level entries (excluding housekeeping files).  Dirs
    get a trailing slash for clarity in the manifest.
    """
    skip = {"MANIFEST.json", "download.log", ".extract_stage"}
    entries = []
    for p in sorted(RAW_DIR.iterdir()):
        if p.name in skip or p.name.startswith("."):
            continue
        entries.append(p.name + ("/" if p.is_dir() else ""))
    return entries


# --- main ------------------------------------------------------------------ #


def main() -> int:
    args = parse_args()
    force = args.force or args.fresh

    # Early idempotency check — before we touch anything on disk.
    if (
        not force
        and MANIFEST_PATH.exists()
        and DownloadManifest.check_already_complete(MANIFEST_PATH, force=False)
    ):
        print(
            f"{SOURCE_NAME} already downloaded (status=complete); skipping. "
            f"Pass --force to re-download."
        )
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    log = configure_logging(LOG_PATH)

    # If we get here and the manifest says in_progress / failed, the check
    # above re-raises; so at this point either force=True or the manifest
    # doesn't exist.  Either way, proceed.
    if force and MANIFEST_PATH.exists():
        # --force: still validate the status isn't in_progress under us.
        # (A rogue concurrent run would be very bad.)
        try:
            existing = json.loads(MANIFEST_PATH.read_text())
            log.info(
                "manifest already exists; overwriting (previous status: %s)",
                existing.get("download", {}).get("status"),
            )
        except (OSError, ValueError):
            log.warning("manifest exists but is unreadable; overwriting")

    log.info("starting download of %s", SOURCE_NAME)
    log.info("upstream: %s", UPSTREAM_URL)
    log.info("target:   %s", RAW_DIR)
    log.info("force:    %s  fresh: %s", args.force, args.fresh)

    # Preconditions
    require_cmd("python3", "tar", "zstd")
    if not shutil.which("aria2c"):
        require_cmd("curl")
    require_disk_gib(REQUIRED_DISK_GIB, REPO_ROOT)

    # --fresh wipes any existing partial.  Plain --force keeps the partial to
    # enable resume (see CHANGELOG note on v2: the old bug was deleting it).
    if args.fresh and ARCHIVE_PATH.exists():
        log.warning("--fresh: removing existing archive %s", ARCHIVE_PATH)
        ARCHIVE_PATH.unlink()

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
        log.info("downloading (~36 GiB, this may take a while)...")
        _download(log)

        archive_bytes = file_bytes(ARCHIVE_PATH)
        log.info("download complete: %d bytes", archive_bytes)
        manifest.set("download.archive_bytes", archive_bytes)

        _extract(log)

        log.info("removing archive %s", ARCHIVE_PATH)
        ARCHIVE_PATH.unlink(missing_ok=True)

        contents = _top_level_contents()
        extracted_bytes = dir_bytes(RAW_DIR)
        log.info("extracted tree size: %d bytes", extracted_bytes)
        manifest.complete(
            archive_bytes=archive_bytes,
            extracted_bytes=extracted_bytes,
            contents=contents,
        )
        log.info("done: %s", SOURCE_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Download <source> from <upstream_url> into data/raw/<source>/.

Usage:
    uv run python scripts/<source>/download.py [--force] [--fresh] [--dry-run]

Replace the metadata block + do_work() body. Keep the rest unchanged.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "REPLACE_ME"
UPSTREAM_REPO = "https://..."
UPSTREAM_URL = "https://..."
SCRIPT_VERSION = 1
DESCRIPTION = "<one sentence>"
REQUIRED_DISK_GIB = 10
REQUIRED_BINARIES: tuple[str, ...] = ("curl",)

# --- path layout ----------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
LOG_PATH = RAW_DIR / "download.log"
TARGET_REL = f"data/raw/{SOURCE_NAME}"


# --- logging (UTC ISO 8601, tee to stdout + log file) ---------------------- #


class _UtcFormatter(logging.Formatter):
    def formatTime(  # noqa: N802 — override of stdlib logging.Formatter.formatTime
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        return datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure_logging(log_path: Path, *, verbose: bool = False) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(SOURCE_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    fmt = _UtcFormatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- preconditions --------------------------------------------------------- #


def check_preconditions(log: logging.Logger) -> None:
    missing = [c for c in REQUIRED_BINARIES if shutil.which(c) is None]
    if missing:
        raise SystemExit(f"missing required binaries: {', '.join(missing)}. install and retry.")
    usage = shutil.disk_usage(REPO_ROOT)
    avail_gib = usage.free / (1024**3)
    if avail_gib < REQUIRED_DISK_GIB:
        raise SystemExit(
            f"insufficient disk: need {REQUIRED_DISK_GIB} GiB on {REPO_ROOT}, "
            f"have {avail_gib:.1f} GiB"
        )
    log.info("disk ok: %.1f GiB free (need %d)", avail_gib, REQUIRED_DISK_GIB)


# --- manifest lifecycle ---------------------------------------------------- #


class DownloadManifest(AbstractContextManager):
    def __init__(self, *, started_at: str) -> None:
        self.started_at = started_at
        self._completed = False
        self._log = logging.getLogger(SOURCE_NAME)

    @staticmethod
    def check_already_complete(path: Path, *, force: bool) -> bool:
        if not path.exists() or force:
            return False
        doc = json.loads(path.read_text())
        status = doc.get("download", {}).get("status")
        if status == "complete":
            return True
        if status == "in_progress":
            raise SystemExit(
                f"manifest status is 'in_progress' at {path} — another run may be "
                f"active, or a previous run crashed. investigate, then re-run with --force."
            )
        if status == "failed":
            raise SystemExit(
                f"previous run failed (see {path.parent}/download.log). "
                f"investigate and re-run with --force."
            )
        raise SystemExit(f"manifest at {path} has unexpected status: {status!r}")

    def _initial(self) -> dict[str, Any]:
        return {
            "manifest_version": 1,
            "source_name": SOURCE_NAME,
            "description": DESCRIPTION,
            "upstream": {"repo": UPSTREAM_REPO, "url": UPSTREAM_URL},
            "script": {
                "path": f"scripts/{SOURCE_NAME}/download.py",
                "version": SCRIPT_VERSION,
            },
            "download": {
                "started_at": self.started_at,
                "completed_at": None,
                "archive_bytes": None,
                "extracted_bytes": None,
                "status": "in_progress",
            },
            "target": {"raw_dir": TARGET_REL, "contents": []},
            "notes": "",
        }

    def _read(self) -> dict[str, Any]:
        return json.loads(MANIFEST_PATH.read_text())

    def _write(self, doc: dict[str, Any]) -> None:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(doc, indent=2) + "\n")

    def complete(
        self,
        *,
        archive_bytes: int | None = None,
        extracted_bytes: int | None = None,
        contents: list[str] | None = None,
    ) -> None:
        doc = self._read()
        doc["download"]["completed_at"] = utc_now()
        doc["download"]["status"] = "complete"
        if archive_bytes is not None:
            doc["download"]["archive_bytes"] = archive_bytes
        if extracted_bytes is not None:
            doc["download"]["extracted_bytes"] = extracted_bytes
        if contents is not None:
            doc["target"]["contents"] = contents
        self._write(doc)
        self._completed = True

    def __enter__(self) -> DownloadManifest:
        self._write(self._initial())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self._flip_failed(reason=f"{exc_type.__name__}: {exc_val}")
            return False
        if not self._completed:
            self._flip_failed(reason="complete() never called")
        return False

    def _flip_failed(self, *, reason: str) -> None:
        try:
            doc = self._read()
            doc["download"]["status"] = "failed"
            doc["download"]["completed_at"] = utc_now()
            doc["notes"] = (doc.get("notes") or "") + f"\nfailed: {reason}"
            self._write(doc)
            self._log.error("manifest marked failed: %s", reason)
        except Exception:
            # never let the cleanup path raise on top of the original exception
            pass


# --- do the work ----------------------------------------------------------- #


def do_work(args: argparse.Namespace, manifest: DownloadManifest, log: logging.Logger) -> None:
    """Replace this with source-specific fetch + extract logic.

    Contract:
    - On success: populate RAW_DIR, then call manifest.complete(archive_bytes=..., ...)
    - On failure: raise; the context manager flips the manifest to `failed`
    - On --dry-run: print the plan and return without mutations
    """
    if args.dry_run:
        log.info("dry-run: would download from %s into %s", UPSTREAM_URL, RAW_DIR)
        return

    # ... source-specific download logic ...

    manifest.complete(archive_bytes=0, extracted_bytes=0, contents=[])


# --- CLI ------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=DESCRIPTION)
    p.add_argument("--force", action="store_true", help="bypass idempotency; keep partial state")
    p.add_argument(
        "--fresh", action="store_true", help="wipe partial state then retry (implies --force)"
    )
    p.add_argument("--dry-run", action="store_true", help="show plan; do not mutate")
    p.add_argument("--verbose", "-v", action="store_true", help="more log output")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.force = args.force or args.fresh

    if DownloadManifest.check_already_complete(MANIFEST_PATH, force=args.force):
        print(f"{SOURCE_NAME} already complete; pass --force to re-download")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    log = configure_logging(LOG_PATH, verbose=args.verbose)
    check_preconditions(log)

    if args.fresh:
        log.info("--fresh: wiping partial state")
        # ... source-specific cleanup ...

    with DownloadManifest(started_at=utc_now()) as manifest:
        do_work(args, manifest, log)
        log.info("done: %s", SOURCE_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())

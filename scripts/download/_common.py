"""Shared helpers for download scripts. Stdlib only — no third-party deps.

Every downloader lives at `scripts/download/<source_name>/script.py` and
imports from this module via::

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
        utc_now,
    )

The contract (mirroring `data/README.md`):

- Every download creates `data/raw/<source>/MANIFEST.json` (schema v1).
- `status` starts as ``"in_progress"``, flips to ``"complete"`` on success,
  or ``"failed"`` on error (handled automatically by the `DownloadManifest`
  context manager).
- All log output is timestamped and tee'd to `data/raw/<source>/download.log`.
- Scripts are idempotent: if a manifest already exists with
  ``status == "complete"``, the downloader skips.  ``--force`` bypasses the
  check; ``--fresh`` additionally wipes any partial archive.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "MANIFEST_VERSION",
    "DownloadManifest",
    "configure_logging",
    "dir_bytes",
    "file_bytes",
    "require_cmd",
    "require_disk_gib",
    "run_and_stream",
    "utc_now",
]

MANIFEST_VERSION = 1
_DEFAULT_LOG_NAME = "download.log"


# --------------------------------------------------------------------------- #
# Basic helpers                                                               #
# --------------------------------------------------------------------------- #


def utc_now() -> str:
    """ISO 8601 UTC timestamp with seconds precision, e.g. ``2026-04-11T04:12:00Z``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_bytes(path: Path) -> int:
    """Byte size of a single file.  Returns 0 if the path doesn't exist."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def dir_bytes(path: Path) -> int:
    """Total byte size under a directory (recursive).  Returns 0 if missing."""
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except (FileNotFoundError, PermissionError):
            pass
    return total


def require_cmd(*cmds: str) -> None:
    """Exit with a clear error if any of the named binaries are missing."""
    missing = [c for c in cmds if shutil.which(c) is None]
    if missing:
        raise SystemExit(
            f"missing required binaries: {', '.join(missing)}. Install them and retry."
        )


def require_disk_gib(need_gib: int, path: Path) -> None:
    """Exit if the filesystem backing *path* has less than *need_gib* GiB free."""
    usage = shutil.disk_usage(path)
    avail_gib = usage.free / (1024**3)
    if avail_gib < need_gib:
        raise SystemExit(
            f"insufficient disk: need {need_gib} GiB on {path}, have {avail_gib:.1f} GiB"
        )
    logging.getLogger("download").info(
        "disk check ok: %.1f GiB free on %s (need %d)", avail_gib, path, need_gib
    )


def run_and_stream(cmd: list[str], log_path: Path) -> None:
    """Run *cmd* and stream combined stdout/stderr live to both the terminal
    and the download log file.  Raises ``subprocess.CalledProcessError`` on
    non-zero exit.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_f:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert proc.stdout is not None
        # Bind to a local so the narrowed (non-Optional) type flows into the
        # lambda below; pyright doesn't carry the assertion across closures.
        stdout = proc.stdout
        for chunk in iter(lambda: stdout.read(4096), b""):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            log_f.write(chunk)
        proc.wait()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)


# --------------------------------------------------------------------------- #
# Logging                                                                     #
# --------------------------------------------------------------------------- #


class _UtcFormatter(logging.Formatter):
    """Emits ``2026-04-11T04:12:00Z [LEVEL] message`` to match bash-era logs."""

    def formatTime(  # noqa: N802 — overrides stdlib logging.Formatter.formatTime
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        return datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure_logging(log_path: Path, *, level: int = logging.INFO) -> logging.Logger:
    """Configure the ``download`` logger to tee to stdout and a log file.

    Idempotent: calling twice won't duplicate handlers.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("download")
    logger.setLevel(level)
    logger.propagate = False

    formatter = _UtcFormatter("%(asctime)s [%(levelname)s] %(message)s")

    # Only add handlers once.
    existing_paths = {
        getattr(h, "baseFilename", None)
        for h in logger.handlers
        if isinstance(h, logging.FileHandler)
    }
    if str(log_path.resolve()) not in existing_paths:
        fh = logging.FileHandler(log_path)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    ):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    return logger


# --------------------------------------------------------------------------- #
# Manifest context manager                                                    #
# --------------------------------------------------------------------------- #


class DownloadManifest(AbstractContextManager):
    """Manifest lifecycle for a single download.

    On ``__enter__``: writes an initial ``MANIFEST.json`` with
    ``download.status = "in_progress"``.

    On ``__exit__``:

    - If ``complete()`` was called, status is already ``"complete"``; no-op.
    - If an exception bubbled up, flip to ``"failed"``, log, and re-raise.
    - If no exception but ``complete()`` was never called, flip to ``"failed"``
      with a warning (guards against the caller forgetting).

    Use it like::

        with DownloadManifest(
            manifest_path=manifest_path,
            source_name="prediction_market_analysis",
            description="...",
            upstream_repo="...",
            upstream_url="...",
            script_path="scripts/download/prediction_market_analysis/script.py",
            script_version=1,
            target_rel="data/raw/prediction_market_analysis",
        ) as manifest:
            ...  # do work
            manifest.complete(archive_bytes=..., extracted_bytes=...)
    """

    def __init__(
        self,
        *,
        manifest_path: Path,
        source_name: str,
        description: str,
        upstream_repo: str,
        upstream_url: str,
        script_path: str,
        script_version: int,
        target_rel: str,
    ) -> None:
        self.manifest_path = manifest_path
        self.source_name = source_name
        self.description = description
        self.upstream_repo = upstream_repo
        self.upstream_url = upstream_url
        self.script_path = script_path
        self.script_version = script_version
        self.target_rel = target_rel
        self._completed = False
        self._log = logging.getLogger("download")

    # -------------------- class-level helpers --------------------

    @staticmethod
    def check_already_complete(manifest_path: Path, *, force: bool) -> bool:
        """Return True if the script should skip (manifest exists + status complete).

        Also surfaces informative errors when status is ``in_progress`` or
        ``failed`` and force is False.
        """
        if not manifest_path.exists():
            return False
        with open(manifest_path) as f:
            doc = json.load(f)
        status = doc.get("download", {}).get("status")
        if force:
            return False
        if status == "complete":
            return True
        if status == "in_progress":
            raise SystemExit(
                f"manifest status is 'in_progress' at {manifest_path} — "
                f"another run may be active, or a previous run crashed. "
                f"Investigate, then re-run with --force."
            )
        if status == "failed":
            raise SystemExit(
                f"previous run failed (see download.log next to {manifest_path}). "
                f"Investigate and re-run with --force."
            )
        raise SystemExit(f"manifest at {manifest_path} has unexpected status: {status!r}")

    # -------------------- instance methods --------------------

    def _initial_doc(self) -> dict[str, Any]:
        return {
            "manifest_version": MANIFEST_VERSION,
            "source_name": self.source_name,
            "description": self.description,
            "upstream": {
                "repo": self.upstream_repo,
                "url": self.upstream_url,
            },
            "script": {
                "path": self.script_path,
                "version": self.script_version,
            },
            "download": {
                "started_at": utc_now(),
                "completed_at": None,
                "archive_bytes": None,
                "extracted_bytes": None,
                "archive_sha256": None,
                "status": "in_progress",
            },
            "target": {
                "raw_dir": self.target_rel,
                "contents": [],
            },
            "notes": "",
        }

    def _read(self) -> dict[str, Any]:
        with open(self.manifest_path) as f:
            return json.load(f)

    def _write(self, doc: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")

    def set(self, dot_path: str, value: Any) -> None:
        """Set a nested field by dotted path, e.g. ``'download.archive_bytes'``."""
        doc = self._read()
        cursor = doc
        parts = dot_path.split(".")
        for p in parts[:-1]:
            cursor = cursor.setdefault(p, {})
        cursor[parts[-1]] = value
        self._write(doc)

    def get(self, dot_path: str) -> Any:
        doc = self._read()
        cursor: Any = doc
        for p in dot_path.split("."):
            cursor = cursor[p]
        return cursor

    def complete(
        self,
        *,
        archive_bytes: int | None = None,
        extracted_bytes: int | None = None,
        contents: Iterable[str] | None = None,
    ) -> None:
        """Mark the manifest as complete.  Must be called before ``__exit__``
        for the download to be considered successful.
        """
        doc = self._read()
        dl = doc["download"]
        dl["completed_at"] = utc_now()
        dl["status"] = "complete"
        if archive_bytes is not None:
            dl["archive_bytes"] = archive_bytes
        if extracted_bytes is not None:
            dl["extracted_bytes"] = extracted_bytes
        if contents is not None:
            doc["target"]["contents"] = list(contents)
        self._write(doc)
        self._completed = True
        self._log.info("manifest marked complete: %s", self.manifest_path)

    # -------------------- context manager protocol --------------------

    def __enter__(self) -> DownloadManifest:
        self._write(self._initial_doc())
        self._log.info("manifest initialized: %s (status=in_progress)", self.manifest_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            # A real exception bubbled out of the with-block.
            self._flip_failed(reason=f"{exc_type.__name__}: {exc_val}")
            return False  # re-raise
        if not self._completed:
            # Caller exited cleanly but never called complete(). Treat as a bug.
            self._flip_failed(reason="complete() was never called")
            return False
        return False  # normal exit

    def _flip_failed(self, *, reason: str) -> None:
        try:
            doc = self._read()
            if doc.get("download", {}).get("status") == "in_progress":
                doc["download"]["status"] = "failed"
                doc["download"]["completed_at"] = utc_now()
                doc["notes"] = (doc.get("notes") or "") + f"\nfailed: {reason}"
                self._write(doc)
                self._log.error("manifest marked failed: %s (%s)", self.manifest_path, reason)
        except Exception:
            # Never let the cleanup path raise on top of the original exception.
            pass

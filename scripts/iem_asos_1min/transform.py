#!/usr/bin/env python3
"""Transform IEM ASOS 1-minute CSVs into Parquet.

Reads ``data/raw/iem_asos_1min/<STATION>/YYYY-MM.csv`` and writes
``data/processed/iem_asos_1min/<STATION>/YYYY-MM.parquet``.  The layout mirrors
raw one-for-one so a single glob gives every station/month, and
``pl.scan_parquet("data/processed/iem_asos_1min/**/*.parquet")`` yields the
full long-form observation table.

Per-file idempotent: a target parquet whose mtime is newer than its source CSV
is skipped on re-runs.  ``--force`` rewrites every file in the selection;
``--fresh`` wipes ``data/processed/iem_asos_1min/`` first and implies
``--force``.

Missing IEM values (``M``) become ``null`` in Parquet.  Timestamps parse to
``Datetime[us, UTC]``.  All numeric channels are cast to ``Float64`` — wind
direction included, so missings round-trip cleanly.

Usage::

    uv run python scripts/iem_asos_1min/transform.py
    uv run python scripts/iem_asos_1min/transform.py --stations LGA NYC
    uv run python scripts/iem_asos_1min/transform.py --force
    uv run python scripts/iem_asos_1min/transform.py --fresh

Self-contained: all helpers inlined. Follows the data-script skill contract.
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
from typing import Any, Literal, cast

import polars as pl

ParquetCompression = Literal["zstd", "snappy", "lz4", "uncompressed"]

# --- metadata -------------------------------------------------------------- #

STEP_NAME = "iem_asos_1min_parquet"
SOURCE_NAME = "iem_asos_1min"
SCRIPT_VERSION = 1
DESCRIPTION = (
    "Transform IEM ASOS 1-minute raw CSVs into per-(station,month) Parquet "
    "under data/processed/iem_asos_1min/. Missing values (M) → null, "
    "valid(UTC) parsed to Datetime[us, UTC]."
)
REQUIRED_DISK_GIB = 2

# Numeric channels the IEM 1-min feed can deliver. Only those actually
# present in a given CSV's header are cast — subsets (e.g. --vars on the
# download side) are handled transparently.
NUMERIC_COLS: tuple[str, ...] = (
    "tmpf",
    "dwpf",
    "sknt",
    "drct",
    "gust_sknt",
    "gust_drct",
    "pres1",
    "pres2",
    "pres3",
    "precip",
)

# --- paths ----------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
PROCESSED_DIR = REPO_ROOT / "data" / "processed" / SOURCE_NAME
MANIFEST_PATH = PROCESSED_DIR / "MANIFEST.json"
LOG_PATH = PROCESSED_DIR / "transform.log"
SCRIPT_REL = f"scripts/{SOURCE_NAME}/transform.py"
TARGET_REL = f"data/processed/{SOURCE_NAME}"


# --- logging --------------------------------------------------------------- #


class _UtcFormatter(logging.Formatter):
    def formatTime(  # noqa: N802 — override stdlib
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        return datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure_logging(log_path: Path, *, verbose: bool = False) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(STEP_NAME)
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
    if not RAW_DIR.exists():
        raise SystemExit(
            f"raw input missing: {RAW_DIR}. run scripts/{SOURCE_NAME}/download.py first."
        )
    avail_gib = shutil.disk_usage(REPO_ROOT).free / (1024**3)
    if avail_gib < REQUIRED_DISK_GIB:
        raise SystemExit(
            f"insufficient disk: need {REQUIRED_DISK_GIB} GiB, have {avail_gib:.1f} GiB"
        )
    log.info("disk ok: %.1f GiB free", avail_gib)


# --- manifest lifecycle ---------------------------------------------------- #


class TransformManifest(AbstractContextManager):
    def __init__(self, *, started_at: str, args: argparse.Namespace) -> None:
        self.started_at = started_at
        self.args = args
        self._completed = False
        self._log = logging.getLogger(STEP_NAME)

    @staticmethod
    def check_already_complete(path: Path, *, force: bool) -> bool:
        if not path.exists() or force:
            return False
        doc = json.loads(path.read_text())
        status = doc.get("transform", {}).get("status")
        if status == "complete":
            return True
        if status == "in_progress":
            raise SystemExit(
                f"manifest status 'in_progress' at {path}. another run may be active, or the "
                f"previous run crashed. investigate, then re-run with --force."
            )
        if status == "failed":
            raise SystemExit(
                f"previous run failed (see {path.parent}/transform.log). re-run with --force."
            )
        raise SystemExit(f"manifest at {path} has unexpected status: {status!r}")

    def _initial(self) -> dict[str, Any]:
        return {
            "manifest_version": 1,
            "source_name": STEP_NAME,
            "description": DESCRIPTION,
            "upstream": {"raw_dir": f"data/raw/{SOURCE_NAME}"},
            "script": {"path": SCRIPT_REL, "version": SCRIPT_VERSION},
            "transform": {
                "started_at": self.started_at,
                "completed_at": None,
                "status": "in_progress",
                "inputs": {
                    "stations": self.args.stations,
                    "force": bool(self.args.force),
                    "fresh": bool(self.args.fresh),
                    "compression": self.args.compression,
                },
                "stats": {},
            },
            "target": {"raw_dir": TARGET_REL, "contents": []},
            "notes": "",
        }

    def _read(self) -> dict[str, Any]:
        return json.loads(MANIFEST_PATH.read_text())

    def _write(self, doc: dict[str, Any]) -> None:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(doc, indent=2, default=str) + "\n")

    def set_stat(self, key: str, value: Any) -> None:
        doc = self._read()
        doc["transform"]["stats"][key] = value
        self._write(doc)

    def complete(self, *, stats: dict[str, Any], contents: list[str]) -> None:
        doc = self._read()
        doc["transform"]["completed_at"] = utc_now()
        doc["transform"]["status"] = "complete"
        doc["transform"]["stats"].update(stats)
        doc["target"]["contents"] = contents
        self._write(doc)
        self._completed = True

    def __enter__(self) -> TransformManifest:
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
            doc["transform"]["status"] = "failed"
            doc["transform"]["completed_at"] = utc_now()
            doc["notes"] = (doc.get("notes") or "") + f"\nfailed: {reason}"
            self._write(doc)
            self._log.error("manifest marked failed: %s", reason)
        except Exception:
            pass


# --- core transform -------------------------------------------------------- #


def discover_sources(stations_filter: list[str] | None) -> list[tuple[str, Path]]:
    """Return a sorted list of ``(station, csv_path)`` pairs under RAW_DIR."""
    out: list[tuple[str, Path]] = []
    wanted = {s.upper() for s in stations_filter} if stations_filter else None
    for station_dir in sorted(p for p in RAW_DIR.iterdir() if p.is_dir()):
        station = station_dir.name
        if wanted is not None and station.upper() not in wanted:
            continue
        for csv_path in sorted(station_dir.glob("*.csv")):
            out.append((station, csv_path))
    return out


def target_path_for(station: str, csv_path: Path) -> Path:
    return PROCESSED_DIR / station / f"{csv_path.stem}.parquet"


def is_up_to_date(src: Path, dst: Path) -> bool:
    if not dst.exists():
        return False
    try:
        return dst.stat().st_mtime >= src.stat().st_mtime and dst.stat().st_size > 0
    except FileNotFoundError:
        return False


def transform_one(src: Path, dst: Path, compression: ParquetCompression) -> int:
    """Read one IEM 1-min CSV, cast types, write Parquet. Returns row count."""
    df = pl.read_csv(
        src,
        null_values=["M", ""],
        infer_schema_length=0,  # read everything as Utf8; we cast explicitly below
    )

    if "valid(UTC)" in df.columns:
        df = df.rename({"valid(UTC)": "valid"})

    cast_exprs: list[pl.Expr] = []
    for c in NUMERIC_COLS:
        if c in df.columns:
            cast_exprs.append(pl.col(c).cast(pl.Float64, strict=False))
    if "valid" in df.columns:
        cast_exprs.append(
            pl.col("valid").str.strptime(
                pl.Datetime("us", time_zone="UTC"),
                format="%Y-%m-%d %H:%M",
                strict=False,
            )
        )
    if cast_exprs:
        df = df.with_columns(cast_exprs)

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    df.write_parquet(tmp, compression=compression)
    tmp.replace(dst)
    return df.height


def all_parquet_contents() -> list[str]:
    return sorted(p.relative_to(PROCESSED_DIR).as_posix() for p in PROCESSED_DIR.rglob("*.parquet"))


# --- main ------------------------------------------------------------------ #


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=DESCRIPTION,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--force", action="store_true", help="rewrite every parquet, even if up to date")
    p.add_argument(
        "--fresh", action="store_true", help=f"wipe {TARGET_REL}/ first (implies --force)"
    )
    p.add_argument("--dry-run", action="store_true", help="print plan; do not mutate")
    p.add_argument("--verbose", "-v", action="store_true", help="DEBUG log level")
    p.add_argument(
        "--stations",
        nargs="+",
        default=None,
        metavar="ID",
        help="restrict to these station IDs (e.g. LGA NYC). Default: all present.",
    )
    p.add_argument(
        "--compression",
        default="zstd",
        choices=["zstd", "snappy", "lz4", "uncompressed"],
        help="Parquet compression codec",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.force = args.force or args.fresh

    if TransformManifest.check_already_complete(MANIFEST_PATH, force=args.force):
        print(f"{STEP_NAME} already complete; pass --force to rebuild")
        return 0

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    log = configure_logging(LOG_PATH, verbose=args.verbose)
    check_preconditions(log)

    if args.fresh:
        log.info("--fresh: wiping %s", PROCESSED_DIR)
        for child in PROCESSED_DIR.iterdir():
            if child.name == "transform.log":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    sources = discover_sources(args.stations)
    if not sources:
        raise SystemExit(
            f"no CSVs found under {RAW_DIR}"
            + (f" for stations {args.stations}" if args.stations else "")
        )

    log.info(
        "found %d source CSVs across %d stations (compression=%s)",
        len(sources),
        len({s for s, _ in sources}),
        args.compression,
    )

    if args.dry_run:
        for station, csv_path in sources:
            dst = target_path_for(station, csv_path)
            status = "skip" if is_up_to_date(csv_path, dst) and not args.force else "write"
            log.info(
                "dry-run: %s %s → %s",
                status,
                csv_path.relative_to(RAW_DIR),
                dst.relative_to(PROCESSED_DIR),
            )
        return 0

    with TransformManifest(started_at=utc_now(), args=args) as manifest:
        written = 0
        skipped = 0
        total_rows = 0
        for i, (station, csv_path) in enumerate(sources, 1):
            dst = target_path_for(station, csv_path)
            if not args.force and is_up_to_date(csv_path, dst):
                skipped += 1
                log.debug("skip %s (up to date)", csv_path.relative_to(RAW_DIR))
            else:
                rows = transform_one(csv_path, dst, cast(ParquetCompression, args.compression))
                written += 1
                total_rows += rows
                log.info(
                    "wrote %s (%d rows, %d bytes)",
                    dst.relative_to(PROCESSED_DIR),
                    rows,
                    dst.stat().st_size,
                )
            if i % 25 == 0 or i == len(sources):
                log.info(
                    "progress: %d/%d files (written=%d skipped=%d rows=%d)",
                    i,
                    len(sources),
                    written,
                    skipped,
                    total_rows,
                )

        manifest.complete(
            stats={
                "files_seen": len(sources),
                "files_written": written,
                "files_skipped": skipped,
                "rows_written": total_rows,
                "stations": sorted({s for s, _ in sources}),
            },
            contents=all_parquet_contents(),
        )
        log.info(
            "done: files_written=%d files_skipped=%d rows_written=%d",
            written,
            skipped,
            total_rows,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

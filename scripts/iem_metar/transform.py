#!/usr/bin/env python3
"""Transform IEM METAR CSVs into Parquet.

Reads ``data/raw/iem_metar/<STATION>/YYYY-MM.csv`` and writes
``data/processed/iem_metar/<STATION>/YYYY-MM.parquet``. Mirrors the asos-1min
transform layout one-for-one so a single glob gives every station/month, and
``pl.scan_parquet("data/processed/iem_metar/**/*.parquet")`` yields the full
hourly + SPECI observation table.

Per-file idempotent: a target parquet whose mtime is newer than its source CSV
is skipped on re-runs. ``--force`` rewrites every file in the selection;
``--fresh`` wipes ``data/processed/iem_metar/`` first and implies ``--force``.

Handles the IEM METAR CGI's quirk of prepending ``#DEBUG:`` comment lines before
the CSV header — polars is invoked with ``comment_prefix="#"`` so those rows
are silently discarded. Missing values (``M``) become ``null``; trace precip
(``T``) is emitted verbatim by the downloader but coerced to ``0.0001`` (the
conventional METAR trace value, in inches) during the cast. Timestamps parse to
``Datetime[us, UTC]``. Numeric channels are cast to ``Float64``. The raw
``metar`` string is preserved unmodified so downstream code can run the
``metar`` parser on remarks (SLP, TT, PRESRR/PRESFR, TSB/E, ...).

Usage::

    uv run python scripts/iem_metar/transform.py
    uv run python scripts/iem_metar/transform.py --stations LGA NYC
    uv run python scripts/iem_metar/transform.py --force
    uv run python scripts/iem_metar/transform.py --fresh

Self-contained: all helpers inlined. Follows the data-script skill contract.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import shutil
import sys
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
from metar import Metar  # type: ignore[import-untyped]

ParquetCompression = Literal["zstd", "snappy", "lz4", "uncompressed"]

# --- metadata -------------------------------------------------------------- #

STEP_NAME = "iem_metar_parquet"
SOURCE_NAME = "iem_metar"
SCRIPT_VERSION = 3
DESCRIPTION = (
    "Transform IEM METAR raw CSVs (hourly + SPECI) into per-(station,month) "
    "Parquet under data/processed/iem_metar/. Missing values (M) → null, trace "
    "precip / ice ('T' in p01i and ice_accretion_*) → 0.0001, valid parsed to "
    "Datetime[us, UTC]. v2 adds RMK remark decoding (temp/dewpt_c, SLP, 6h/24h "
    "max/min temp, 6h/24h precip, snow depth, PRESRR/PRESFR flags, TSB/TSE "
    "timing) via the `metar` parser. v3 fixes silent trace-sentinel loss in "
    "the ice_accretion columns."
)
REQUIRED_DISK_GIB = 2

# Numeric columns the IEM METAR `data=all` feed delivers. Cast to Float64 so
# nulls round-trip cleanly; strict=False lets unexpected values become null
# rather than aborting the file. Only columns actually present in a given CSV
# are cast, so trimmed column sets are handled transparently.
NUMERIC_COLS: tuple[str, ...] = (
    "lon",
    "lat",
    "elevation",
    "tmpf",
    "dwpf",
    "relh",
    "drct",
    "sknt",
    "p01i",
    "alti",
    "mslp",
    "vsby",
    "gust",
    "skyl1",
    "skyl2",
    "skyl3",
    "skyl4",
    "ice_accretion_1hr",
    "ice_accretion_3hr",
    "ice_accretion_6hr",
    "peak_wind_gust",
    "peak_wind_drct",
    "feel",
    "snowdepth",
)

# String columns kept as Utf8. The raw `metar` string is the source-of-truth
# for remark decoding (SLP, TT, PRESRR/PRESFR, TSB/E, PK WND, ...) via the
# `metar` parser package — do NOT mangle it here.
STRING_COLS: tuple[str, ...] = (
    "station",
    "skyc1",
    "skyc2",
    "skyc3",
    "skyc4",
    "wxcodes",
    "metar",
)

# Trace-precip sentinel used by IEM. Replaced with 0.0001 in to keep the
# column numeric while preserving the qualitative signal. The same sentinel
# appears in every precip-like column the METAR CGI emits; empirically
# verified against the full 2025-12-20..2026-04-11 window, the complete list
# of trace-eligible columns is {p01i, ice_accretion_1hr, ice_accretion_3hr,
# ice_accretion_6hr}.
TRACE_SENTINEL = "T"
TRACE_VALUE_INCHES = 0.0001
TRACE_COLS: tuple[str, ...] = (
    "p01i",
    "ice_accretion_1hr",
    "ice_accretion_3hr",
    "ice_accretion_6hr",
)

# --- RMK remark decoding --------------------------------------------------- #

# Output schema for decoded remark fields. Polars needs a struct dtype so the
# map_elements call has a concrete return type; it's also the authoritative
# column-order / type spec for the downstream `with_columns` expansion.
RMK_STRUCT_DTYPE = pl.Struct(
    {
        # Best-available temperature/dewpoint in degC. python-metar fuses the
        # main-body TT/TD integer with the RMK T-group 0.1-degC override when
        # the latter is present; SPECIs without a T-group fall back to the
        # main-body integer. Either way this is the canonical degC source and
        # is never lower-precision than tmpf/dwpf (which are integer degF).
        "temp_c": pl.Float64,
        "dewpt_c": pl.Float64,
        # Sea-level pressure from the RMK SLP-group, 0.1 mb. IEM also decodes
        # this and exposes it as `mslp`; the two columns are exactly equal on
        # every row where either is non-null (verified empirically). Kept as
        # a cross-check / integrity column in case upstream parsing diverges.
        "slp_mb_rmk": pl.Float64,
        "max_temp_6hr_c": pl.Float64,  # 1-group — present at 00/06/12/18Z
        "min_temp_6hr_c": pl.Float64,  # 2-group — present at 00/06/12/18Z
        "max_temp_24hr_c": pl.Float64,  # 4-group — present at 00Z
        "min_temp_24hr_c": pl.Float64,  # 4-group — present at 00Z
        "precip_6hr_in": pl.Float64,  # 6-group
        "precip_24hr_in": pl.Float64,  # 7-group
        "snowdepth_in_rmk": pl.Float64,  # 4/xxx group
        "press_tendency_3hr_mb": pl.Float64,  # 5-group magnitude (0.1 mb)
        "press_tendency_3hr_code": pl.Int64,  # 5-group character code (0..8)
        "presrr": pl.Boolean,  # PRESRR flag (pressure rising rapidly)
        "presfr": pl.Boolean,  # PRESFR flag (pressure falling rapidly)
        "tsb_minute": pl.Int64,  # thunderstorm begin, minutes-past-hour
        "tse_minute": pl.Int64,  # thunderstorm end, minutes-past-hour
    }
)

# RMK-only regexes for fields the `metar` package does NOT expose as
# attributes. Applied to the full raw METAR string; matches are case-
# sensitive per METAR spec.
_RE_PRESRR = re.compile(r"\bPRESRR\b")
_RE_PRESFR = re.compile(r"\bPRESFR\b")
# Thunderstorm begin / end times. Format is TSBhhmm or TSBmm (hour optional,
# minutes always 2 digits). Same for TSE. See FMH-1 §12.6.8.
_RE_TSB = re.compile(r"\bTSB(\d{2})?(\d{2})\b")
_RE_TSE = re.compile(r"\bTSE(\d{2})?(\d{2})\b")


def _safe_val(obj: Any, unit: str | None) -> float | None:
    """Extract ``.value(unit)`` from a python-metar Datatypes object, returning
    None on any failure (missing attr, parse glitch, divide-by-zero, ...).
    """
    if obj is None:
        return None
    try:
        return float(obj.value(unit)) if unit else float(obj.value())
    except Exception:
        return None


def parse_metar_remarks(raw: str | None) -> dict[str, Any]:
    """Parse a raw METAR string and return the RMK-derived fields as a dict.

    All fields default to ``None`` / ``False``. If the ``metar`` parser raises
    (malformed report, unrecognised group), the fields it couldn't populate
    stay ``None``; the booleans still get filled via regex on the raw string.
    """
    out: dict[str, Any] = {
        "temp_c": None,
        "dewpt_c": None,
        "slp_mb_rmk": None,
        "max_temp_6hr_c": None,
        "min_temp_6hr_c": None,
        "max_temp_24hr_c": None,
        "min_temp_24hr_c": None,
        "precip_6hr_in": None,
        "precip_24hr_in": None,
        "snowdepth_in_rmk": None,
        "press_tendency_3hr_mb": None,
        "press_tendency_3hr_code": None,
        "presrr": False,
        "presfr": False,
        "tsb_minute": None,
        "tse_minute": None,
    }
    if not raw:
        return out

    # Regex-only fields — independent of the metar parser.
    if _RE_PRESRR.search(raw):
        out["presrr"] = True
    if _RE_PRESFR.search(raw):
        out["presfr"] = True
    if (m_tsb := _RE_TSB.search(raw)) is not None:
        out["tsb_minute"] = int(m_tsb.group(2))
    if (m_tse := _RE_TSE.search(raw)) is not None:
        out["tse_minute"] = int(m_tse.group(2))

    # 3-hour pressure-tendency group: ``5tppp`` where t ∈ 0..8 and ppp is 0.1mb.
    # python-metar has a handler but doesn't expose a normalised attribute on
    # the Metar object, so parse it ourselves. Require a word boundary and a
    # trailing space or end-of-string so we don't match mid-remark numeric
    # runs like ``51057``'s possible confusion with temperature codes.
    m_pt = re.search(r"(?:^|\s)5([0-8])(\d{3})(?:\s|$)", raw)
    if m_pt is not None:
        out["press_tendency_3hr_code"] = int(m_pt.group(1))
        out["press_tendency_3hr_mb"] = int(m_pt.group(2)) / 10.0

    # Everything below comes from python-metar's attribute surface. Wrap the
    # parse in a broad try — METAR has many quirky producers and the parser
    # can raise on unknown groups; we prefer "fewer fields, still decoded"
    # over "one bad METAR nukes the whole file".
    try:
        m = Metar.Metar(raw, strict=False)
    except Exception:
        return out

    out["temp_c"] = _safe_val(getattr(m, "temp", None), "C")
    out["dewpt_c"] = _safe_val(getattr(m, "dewpt", None), "C")
    out["slp_mb_rmk"] = _safe_val(getattr(m, "press_sea_level", None), "MB")
    out["max_temp_6hr_c"] = _safe_val(getattr(m, "max_temp_6hr", None), "C")
    out["min_temp_6hr_c"] = _safe_val(getattr(m, "min_temp_6hr", None), "C")
    out["max_temp_24hr_c"] = _safe_val(getattr(m, "max_temp_24hr", None), "C")
    out["min_temp_24hr_c"] = _safe_val(getattr(m, "min_temp_24hr", None), "C")
    out["precip_6hr_in"] = _safe_val(getattr(m, "precip_6hr", None), "IN")
    out["precip_24hr_in"] = _safe_val(getattr(m, "precip_24hr", None), "IN")
    # python-metar's snowdepth is a Datatypes.precipitation object; prefer
    # .value('IN'), fall back to float() on the object itself for older
    # builds that expose a plain numeric.
    snowdepth_obj = getattr(m, "snowdepth", None)
    if snowdepth_obj is not None:
        try:
            out["snowdepth_in_rmk"] = float(snowdepth_obj.value("IN"))
        except Exception:
            with contextlib.suppress(Exception):
                out["snowdepth_in_rmk"] = float(snowdepth_obj)

    return out


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
    """Read one IEM METAR CSV, cast types, write Parquet. Returns row count."""
    # comment_prefix="#" drops the IEM CGI's `#DEBUG:` header block.
    # infer_schema_length=0 reads everything as Utf8; we cast explicitly below.
    df = pl.read_csv(
        src,
        null_values=["M", ""],
        comment_prefix="#",
        infer_schema_length=0,
    )

    # Rename any legacy `valid(UTC)` header to plain `valid` just in case.
    if "valid(UTC)" in df.columns:
        df = df.rename({"valid(UTC)": "valid"})

    cast_exprs: list[pl.Expr] = []

    # Trace-eligible columns (precip + ice accretion): the IEM METAR CGI
    # emits the string 'T' as the trace sentinel. Replace with 0.0001 in to
    # keep the column numeric without losing the qualitative signal.
    for c in TRACE_COLS:
        if c in df.columns:
            cast_exprs.append(
                pl.when(pl.col(c) == TRACE_SENTINEL)
                .then(pl.lit(TRACE_VALUE_INCHES))
                .otherwise(pl.col(c).cast(pl.Float64, strict=False))
                .alias(c)
            )

    for c in NUMERIC_COLS:
        if c in df.columns and c not in TRACE_COLS:
            cast_exprs.append(pl.col(c).cast(pl.Float64, strict=False))

    if "valid" in df.columns:
        cast_exprs.append(
            pl.col("valid").str.strptime(
                pl.Datetime("us", time_zone="UTC"),
                format="%Y-%m-%d %H:%M",
                strict=False,
            )
        )

    # peak_wind_time is also a timestamp when present.
    if "peak_wind_time" in df.columns:
        cast_exprs.append(
            pl.col("peak_wind_time")
            .str.strptime(
                pl.Datetime("us", time_zone="UTC"),
                format="%Y-%m-%d %H:%M",
                strict=False,
            )
            .alias("peak_wind_time")
        )

    # String columns: no-op cast to Utf8 so the schema is explicit and stable.
    for c in STRING_COLS:
        if c in df.columns:
            cast_exprs.append(pl.col(c).cast(pl.Utf8, strict=False))

    if cast_exprs:
        df = df.with_columns(cast_exprs)

    # RMK remark decoding — append decoded fields as new columns. Only runs
    # if a `metar` column is present (it always is for the IEM data=all feed,
    # but this guards trimmed schemas).
    if "metar" in df.columns:
        df = df.with_columns(
            pl.col("metar")
            .map_elements(parse_metar_remarks, return_dtype=RMK_STRUCT_DTYPE)
            .alias("_rmk")
        ).unnest("_rmk")

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

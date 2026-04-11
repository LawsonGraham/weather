#!/usr/bin/env python3
"""Validation checks for the iem_asos_1min parquet output.

Implements levels 1-4 of the paranoid audit rigor ladder from
``.claude/skills/data-validation/SKILL.md`` as permanent regression guards.
Level 5 (fresh upstream re-fetch) and level 6 (invariant stress tests) run
interactively during deep audits; anything they catch must graduate into
this file to stay caught.

Checks performed, grouped by level:

**Level 1 — manifest & disk**

1. Raw manifest exists, status is ``complete``, and its ``target.contents``
   enumerates every CSV on disk (no orphans either direction).
2. Processed manifest exists, status is ``complete``, and its ``rows_written``
   equals the actual total rows across all parquets.

**Level 2 — row + column fidelity**

3. Every expected ``(station, YYYY-MM)`` pair has a parquet on disk.
4. Per-file row count is 1:1 between raw CSV and parquet (after dropping the
   CSV header row).
5. Every raw CSV column survives into the parquet (with the single rename
   ``valid(UTC)`` → ``valid``).
6. Month-file containment — every row's ``valid`` timestamp is within the
   file's (year, month).
7. No duplicate ``(station, valid)`` keys across the whole dataset.
8. No cross-file overlap — every ``(station, valid)`` key appears in exactly
   one parquet file.

**Level 3 — value-level fidelity**

9. Bit-for-bit value comparison using a polars lazy join: every raw CSV value
   is compared to the parquet value at the same key. Numeric ε = 1e-6 allowed
   for float repr round-tripping; strings must match exactly (empty / "M" →
   null).
10. Null-count parity per file — parquet null count ≥ raw null count for
    every column; no invented values, no cast-failure losses.
11. Timestamp parse coverage — every raw row produces a non-null ``valid``
    ``Datetime[us, UTC]``.

**Level 4 — schema invariants & cross-column consistency**

12. Required columns present with expected dtypes.
13. Value range sanity — temperature / wind / pressure / precipitation within
    physical bounds.
14. No NaN in any Float64 column (cast failures must be nulls, not NaN).
15. Station column has zero nulls.
16. Gap distribution — largest inter-observation gap per station warns if it
    exceeds ``MAX_OBS_GAP`` (data outages are real; this catches ones large
    enough to matter).
17. Second-offset check — ``valid`` timestamps always land on the ``:00``
    seconds mark (clean 1-minute grid).

Usage::

    uv run python scripts/iem_asos_1min/validate.py
    uv run python scripts/iem_asos_1min/validate.py --stations NYC LGA
    uv run python scripts/iem_asos_1min/validate.py --start 2025-06-01 --end 2026-04-11
    uv run python scripts/iem_asos_1min/validate.py --fidelity-sample 5
        # limit value-level comparison to 5 files for speed on large datasets

Exit 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_NAME = "iem_asos_1min"
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
PROCESSED_DIR = REPO_ROOT / "data" / "processed" / SOURCE_NAME
RAW_MANIFEST = RAW_DIR / "MANIFEST.json"
PROCESSED_MANIFEST = PROCESSED_DIR / "MANIFEST.json"

REQUIRED_COLS: dict[str, type[pl.DataType] | tuple[type[pl.DataType], ...]] = {
    "station": pl.Utf8,
    "station_name": pl.Utf8,
    "valid": pl.Datetime,
    "tmpf": pl.Float64,
    "dwpf": pl.Float64,
    "sknt": pl.Float64,
    "drct": pl.Float64,
    "gust_sknt": pl.Float64,
    "gust_drct": pl.Float64,
    "pres1": pl.Float64,
    "precip": pl.Float64,
    "ptype": pl.Utf8,
}

# The full numeric column set, used for bit-for-bit cross-checks. Must match
# (a subset of) what ``scripts/iem_asos_1min/download.py`` requests from IEM
# and what ``transform.py`` casts to Float64.
NUMERIC_COLS: tuple[str, ...] = (
    "tmpf",
    "dwpf",
    "sknt",
    "drct",
    "gust_sknt",
    "gust_drct",
    "pres1",
    "precip",
)

# String columns — kept as Utf8 after the transform. These are compared
# exactly (with empty / "M" normalised to None on both sides).
STRING_COLS: tuple[str, ...] = (
    "station",
    "station_name",
    "ptype",
)

# Physical plausibility bounds. Deliberately generous — real extremes should
# be *well* inside these. Anything outside is almost certainly a parse error
# or a sensor glitch worth investigating.
TMPF_MIN, TMPF_MAX = -80.0, 130.0
SKNT_MIN, SKNT_MAX = 0.0, 200.0
DRCT_MIN, DRCT_MAX = 0.0, 360.0
PRES1_MIN, PRES1_MAX = 20.0, 35.0  # inches Hg
PRECIP_MIN, PRECIP_MAX = 0.0, 10.0  # inches per minute — anything > 10 is glitch

# Maximum tolerated inter-observation gap per station. KNYC / KLGA are
# 1-minute ASOS sites so the typical gap is 1 minute, and healthy outages
# of a few hours are normal. Anything over 24 hours is big enough to flag.
MAX_OBS_GAP = timedelta(hours=24)

# Minimum mean observations-per-hour before a station's coverage is flagged.
# 1-minute feed should average ~60/hour; mid-50s is typical due to station
# maintenance. Anything below this is a warning.
MIN_MEAN_OBS_PER_HOUR = 40.0

# Null-count parity: checking every file is expensive (600k rows x 11 cols
# of Python CSV iteration). Default to a stratified sample. Use
# ``--fidelity-sample N`` / ``--null-parity-sample N`` to override.
DEFAULT_FIDELITY_SAMPLE = 6
DEFAULT_NULL_PARITY_SAMPLE = 3


# --- CLI ------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    today = datetime.now(UTC).date().isoformat()
    p = argparse.ArgumentParser(
        description="Validate iem_asos_1min parquet output.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--stations",
        nargs="+",
        default=None,
        metavar="ID",
        help="restrict checks to these station IDs; default: all present in manifest",
    )
    p.add_argument(
        "--start",
        default=None,
        metavar="YYYY-MM-DD",
        help="expected start date (UTC, inclusive). Defaults to manifest's download.start.",
    )
    p.add_argument(
        "--end",
        default=None,
        metavar="YYYY-MM-DD",
        help=f"expected end date (UTC, inclusive). Defaults to today ({today}).",
    )
    p.add_argument(
        "--fidelity-sample",
        type=int,
        default=DEFAULT_FIDELITY_SAMPLE,
        metavar="N",
        help=(
            "number of files to run the full bit-for-bit raw↔parquet comparison "
            "on. 0 = all files. Defaults to a stratified sample for speed on "
            "large datasets."
        ),
    )
    p.add_argument(
        "--null-parity-sample",
        type=int,
        default=DEFAULT_NULL_PARITY_SAMPLE,
        metavar="N",
        help=("number of files to run the expensive null-count parity check on. 0 = all files."),
    )
    return p.parse_args()


# --- helpers --------------------------------------------------------------- #


def month_starts(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield date(y, m, 1)
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def month_end(first: date) -> date:
    last_day = calendar.monthrange(first.year, first.month)[1]
    return date(first.year, first.month, last_day)


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"FAIL  {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"WARN  {msg}")

    def ok(self, msg: str) -> None:
        print(f"ok    {msg}")


def stratified_sample(files: list[Path], n: int) -> list[Path]:
    """Pick ``n`` files evenly spaced across the list. If ``n <= 0`` or
    ``n >= len(files)``, return all files.
    """
    if n <= 0 or n >= len(files):
        return files
    step = max(1, len(files) // n)
    picked = files[::step][:n]
    # Always include first and last to cover the edges.
    if files[0] not in picked:
        picked = [files[0], *picked]
    if files[-1] not in picked:
        picked = [*picked, files[-1]]
    return picked


# --- Level 1: manifests ---------------------------------------------------- #


def check_manifests(chk: Checker) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    raw_doc: dict[str, Any] | None = None
    proc_doc: dict[str, Any] | None = None

    if not RAW_MANIFEST.exists():
        chk.fail(f"missing raw manifest: {RAW_MANIFEST}")
    else:
        raw_doc = json.loads(RAW_MANIFEST.read_text())
        assert raw_doc is not None
        status = raw_doc.get("download", {}).get("status")
        if status != "complete":
            chk.fail(f"raw manifest status = {status!r} (expected 'complete')")
        else:
            chk.ok(f"raw manifest status = complete ({len(raw_doc['target']['contents'])} csvs)")

    if not PROCESSED_MANIFEST.exists():
        chk.fail(f"missing processed manifest: {PROCESSED_MANIFEST}")
    else:
        proc_doc = json.loads(PROCESSED_MANIFEST.read_text())
        assert proc_doc is not None
        status = proc_doc.get("transform", {}).get("status")
        if status != "complete":
            chk.fail(f"processed manifest status = {status!r} (expected 'complete')")
        else:
            stats = proc_doc["transform"]["stats"]
            chk.ok(
                f"processed manifest status = complete "
                f"(files={stats.get('files_written')} rows={stats.get('rows_written')})"
            )

    return raw_doc, proc_doc


def check_manifest_enumeration(chk: Checker) -> None:
    """Raw manifest.target.contents + processed manifest.target.contents must
    each enumerate every file on disk, with no orphans in either direction.
    """
    if RAW_MANIFEST.exists():
        raw_doc = json.loads(RAW_MANIFEST.read_text())
        declared = set(raw_doc.get("target", {}).get("contents") or [])
        actual = {f.relative_to(RAW_DIR).as_posix() for f in sorted(RAW_DIR.rglob("*.csv"))}
        if missing := (actual - declared):
            chk.fail(f"CSVs on disk not in raw manifest: {sorted(missing)}")
        if extras := (declared - actual):
            chk.fail(f"CSVs in raw manifest but missing on disk: {sorted(extras)}")
        if not (missing or extras):
            chk.ok(f"raw manifest enumerates all {len(actual)} CSVs on disk")

    if PROCESSED_MANIFEST.exists():
        proc_doc = json.loads(PROCESSED_MANIFEST.read_text())
        declared = set(proc_doc.get("target", {}).get("contents") or [])
        actual = {
            f.relative_to(PROCESSED_DIR).as_posix()
            for f in sorted(PROCESSED_DIR.rglob("*.parquet"))
        }
        if missing := (actual - declared):
            chk.fail(f"parquets on disk not in processed manifest: {sorted(missing)}")
        if extras := (declared - actual):
            chk.fail(f"parquets in processed manifest but missing on disk: {sorted(extras)}")
        if not (missing or extras):
            chk.ok(f"processed manifest enumerates all {len(actual)} parquets on disk")


def check_manifest_row_count(chk: Checker) -> None:
    """Processed manifest's rows_written must equal actual parquet total."""
    if not PROCESSED_MANIFEST.exists():
        return
    proc_doc = json.loads(PROCESSED_MANIFEST.read_text())
    stated = proc_doc.get("transform", {}).get("stats", {}).get("rows_written")
    if stated is None:
        return
    total = 0
    for p in PROCESSED_DIR.rglob("*.parquet"):
        total += pl.scan_parquet(p).select(pl.len()).collect().item()
    if stated == total:
        chk.ok(f"manifest rows_written ({stated}) matches parquet total")
    else:
        chk.fail(f"manifest rows_written ({stated}) != actual parquet total ({total})")


# --- Level 2: row + column fidelity ---------------------------------------- #


def check_file_completeness(
    chk: Checker, stations: list[str], start: date, end: date
) -> list[tuple[str, date, Path]]:
    """Every expected (station, month) pair has a non-empty parquet."""
    present: list[tuple[str, date, Path]] = []
    for station in stations:
        for first in month_starts(start, end):
            pq = PROCESSED_DIR / station / f"{first.year:04d}-{first.month:02d}.parquet"
            if not pq.exists():
                chk.fail(f"missing parquet: {pq.relative_to(REPO_ROOT)}")
                continue
            if pq.stat().st_size == 0:
                chk.fail(f"empty parquet: {pq.relative_to(REPO_ROOT)}")
                continue
            present.append((station, first, pq))
    if present:
        chk.ok(f"file completeness: {len(present)} parquets present")
    return present


def _count_raw_csv_data_rows(csv_path: Path) -> tuple[int, list[str]]:
    """Read an IEM ASOS 1-min CSV, return ``(data_row_count, header_columns)``.

    Unlike the METAR CGI, the 1-min CGI does NOT emit ``#DEBUG:`` comment
    lines — the header is the literal first line.
    """
    with csv_path.open() as f:
        lines = [ln.rstrip("\n") for ln in f]
    if not lines:
        return 0, []
    header = lines[0].split(",")
    data_rows = [ln for ln in lines[1:] if ln.strip()]
    return len(data_rows), header


def check_csv_to_parquet_fidelity(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """Row count + column set preservation per file.

    Catches two failure classes the schema check misses:

    * **row dropping** — polars silently skipping a malformed row or a
      comment-stripping rule eating a legitimate row
    * **column dropping** — a raw column not written into the parquet
      because our cast map missed it (only caught by comparing the literal
      column sets)
    """
    mismatches = 0
    for station, _first, pq_path in present:
        csv_path = RAW_DIR / station / f"{pq_path.stem}.csv"
        if not csv_path.exists():
            chk.fail(f"raw csv missing: {csv_path.relative_to(REPO_ROOT)}")
            mismatches += 1
            continue

        csv_rows, csv_cols = _count_raw_csv_data_rows(csv_path)
        pq_df = pl.read_parquet(pq_path)

        if pq_df.height != csv_rows:
            chk.fail(
                f"row mismatch {station}/{pq_path.stem}: csv={csv_rows}, parquet={pq_df.height}"
            )
            mismatches += 1

        pq_cols = set(pq_df.columns)
        dropped: set[str] = set()
        for c in csv_cols:
            target = "valid" if c == "valid(UTC)" else c
            if target not in pq_cols:
                dropped.add(c)
        if dropped:
            chk.fail(f"column dropped {station}/{pq_path.stem}: {sorted(dropped)}")
            mismatches += 1

    if mismatches == 0:
        chk.ok(
            f"raw CSV ↔ parquet fidelity: {len(present)} files, row counts 1:1, "
            f"every raw column preserved"
        )


def check_uniqueness(chk: Checker) -> None:
    """No duplicate (station, valid) keys across the whole dataset."""
    if not any(PROCESSED_DIR.rglob("*.parquet")):
        return
    full = pl.scan_parquet(str(PROCESSED_DIR / "**/*.parquet"))
    dups = (
        full.group_by(["station", "valid"])
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
        .collect()
    )
    if dups.height == 0:
        total = full.select(pl.len()).collect().item()
        chk.ok(f"no duplicate (station, valid) rows across {total} rows")
    else:
        chk.fail(f"{dups.height} duplicate (station, valid) keys (first 3):\n{dups.head(3)}")


def check_cross_file_overlap(chk: Checker) -> None:
    """Every (station, valid) appears in exactly one parquet file.

    Streams file-by-file so it stays cheap even on very large datasets.
    """
    seen: dict[tuple[str, datetime], str] = {}
    dups_logged = 0
    for p in sorted(PROCESSED_DIR.rglob("*.parquet")):
        df = pl.read_parquet(p, columns=["station", "valid"])
        for row in df.iter_rows(named=True):
            key = (row["station"], row["valid"])
            if key in seen:
                dups_logged += 1
                if dups_logged <= 3:
                    chk.fail(f"cross-file duplicate: {key} in {seen[key]} and {p.name}")
                continue
            seen[key] = p.name
    if dups_logged == 0:
        chk.ok(f"no cross-file duplicates: {len(seen)} unique keys across all files")


def check_month_containment(chk: Checker) -> None:
    """Every row in ``YYYY-MM.parquet`` has a ``valid`` in that month."""
    bad_files = 0
    for p in sorted(PROCESSED_DIR.rglob("*.parquet")):
        stem = p.stem
        if len(stem) != 7 or stem[4] != "-":
            continue
        y, m = int(stem[:4]), int(stem[5:7])
        df = pl.read_parquet(p, columns=["valid"])
        bad = df.filter((pl.col("valid").dt.year() != y) | (pl.col("valid").dt.month() != m)).height
        if bad > 0:
            chk.fail(f"{p.relative_to(PROCESSED_DIR)}: {bad} rows outside ({y}, {m})")
            bad_files += 1
    if bad_files == 0:
        ok_count = sum(1 for _ in PROCESSED_DIR.rglob("*.parquet"))
        chk.ok(
            f"month containment: all rows in all {ok_count} files fall within file's (year, month)"
        )


# --- Level 3: value-level fidelity ---------------------------------------- #


def check_schema_and_bounds(
    chk: Checker, present: list[tuple[str, date, Path]], today: date
) -> None:
    """Dtype + required-column presence + value-range plausibility.

    Per-file; aggregates 'ok' into a single line at the end.
    """
    if not present:
        return
    total_rows = 0
    failures = 0
    for station, first, pq in present:
        df = pl.read_parquet(pq)
        schema = df.schema

        for col, expected in REQUIRED_COLS.items():
            if col not in schema:
                chk.fail(f"{pq.name} ({station}): missing column {col!r}")
                failures += 1
                continue
            got = schema[col]
            if isinstance(expected, tuple):
                if not isinstance(got, expected):
                    chk.fail(f"{pq.name} ({station}): {col} is {got}, expected one of {expected}")
                    failures += 1
            else:
                if not isinstance(got, expected):
                    chk.fail(f"{pq.name} ({station}): {col} is {got}, expected {expected}")
                    failures += 1

        rows = df.height
        total_rows += rows
        last = month_end(first)
        if rows == 0:
            chk.fail(f"{pq.name} ({station}): zero rows")
            failures += 1
            continue

        if "station" in df.columns and df["station"].null_count() > 0:
            chk.fail(f"{pq.name} ({station}): station column has nulls")
            failures += 1

        if "valid" in df.columns:
            vmin = df["valid"].min()
            vmax = df["valid"].max()
            assert isinstance(vmin, datetime) and isinstance(vmax, datetime)
            if vmin.date() < first:
                chk.fail(f"{pq.name} ({station}): earliest valid {vmin} < month start {first}")
                failures += 1
            if vmax.date() > last:
                chk.fail(f"{pq.name} ({station}): latest valid {vmax} > month end {last}")
                failures += 1

        for col, lo_bound, hi_bound in (
            ("tmpf", TMPF_MIN, TMPF_MAX),
            ("dwpf", TMPF_MIN, TMPF_MAX),
            ("sknt", SKNT_MIN, SKNT_MAX),
            ("gust_sknt", SKNT_MIN, SKNT_MAX),
            ("drct", DRCT_MIN, DRCT_MAX),
            ("gust_drct", DRCT_MIN, DRCT_MAX),
            ("pres1", PRES1_MIN, PRES1_MAX),
            ("precip", PRECIP_MIN, PRECIP_MAX),
        ):
            if col in df.columns:
                vals = df[col].drop_nulls()
                if vals.len() > 0:
                    lo = vals.min()
                    hi = vals.max()
                    assert isinstance(lo, int | float) and isinstance(hi, int | float)
                    if lo < lo_bound or hi > hi_bound:
                        chk.fail(
                            f"{pq.name} ({station}): {col} range [{lo}, {hi}] "
                            f"escapes [{lo_bound}, {hi_bound}]"
                        )
                        failures += 1

    if failures == 0:
        chk.ok(
            f"schema + bound checks: {total_rows} rows across {len(present)} files; "
            f"all required columns present with expected dtypes and in-range values"
        )


def check_value_fidelity(
    chk: Checker, present: list[tuple[str, date, Path]], sample_n: int
) -> None:
    """Bit-for-bit raw↔parquet value comparison via polars lazy join.

    Reads the raw CSV with polars in Utf8 mode, normalises strings the same
    way the transform does, joins to the parquet by ``(station, valid)``,
    and asserts every column matches exactly. O(N) and vectorised — handles
    the full 600k-row dataset in a few seconds.
    """
    if not present:
        return
    files = [pq for _, _, pq in present]
    picked = stratified_sample(files, sample_n)
    mismatches = 0
    checked_rows = 0

    for pq_path in picked:
        station = pq_path.parent.name
        csv_path = RAW_DIR / station / f"{pq_path.stem}.csv"
        if not csv_path.exists():
            continue

        # Read raw CSV with polars as all-strings so we can normalise ''/'M' → null
        # and do lazy comparisons without polars re-casting under us.
        raw = pl.read_csv(csv_path, infer_schema_length=0, null_values=["M", ""])
        if "valid(UTC)" in raw.columns:
            raw = raw.rename({"valid(UTC)": "valid"})
        # Parse valid to the same dtype as the parquet for a clean join key.
        raw = raw.with_columns(
            pl.col("valid").str.strptime(
                pl.Datetime("us", time_zone="UTC"),
                format="%Y-%m-%d %H:%M",
                strict=False,
            )
        )
        # Cast numeric columns to match parquet dtype.
        for c in NUMERIC_COLS:
            if c in raw.columns:
                raw = raw.with_columns(pl.col(c).cast(pl.Float64, strict=False))

        pq = pl.read_parquet(pq_path)
        if raw.height != pq.height:
            chk.fail(f"{pq_path.name}: row count differs raw={raw.height} pq={pq.height}")
            mismatches += 1
            continue

        # Outer join on (station, valid) — a missing match on either side is
        # a hard fail because the row count already matches, so any key
        # asymmetry means the transform reshuffled keys.
        joined = raw.join(
            pq,
            on=["station", "valid"],
            how="full",
            suffix="_pq",
        )
        if joined.height != raw.height:
            chk.fail(
                f"{pq_path.name}: join produced {joined.height} rows "
                f"vs expected {raw.height} — key asymmetry"
            )
            mismatches += 1
            continue

        for c in NUMERIC_COLS:
            if c not in raw.columns or c not in pq.columns:
                continue
            c_pq = f"{c}_pq"
            diff = joined.filter(
                (pl.col(c).is_null() != pl.col(c_pq).is_null())
                | (
                    pl.col(c).is_not_null()
                    & pl.col(c_pq).is_not_null()
                    & ((pl.col(c) - pl.col(c_pq)).abs() > 1e-6)
                )
            )
            if diff.height > 0:
                chk.fail(
                    f"{pq_path.name} {c}: {diff.height} value mismatches "
                    f"(sample: {diff.head(1).to_dicts()})"
                )
                mismatches += 1

        for c in STRING_COLS:
            if c not in raw.columns or c not in pq.columns:
                continue
            if c == "station":
                # station is the join key — guaranteed equal — skip.
                continue
            c_pq = f"{c}_pq"
            diff = joined.filter(
                (pl.col(c).is_null() != pl.col(c_pq).is_null())
                | (
                    pl.col(c).is_not_null()
                    & pl.col(c_pq).is_not_null()
                    & (pl.col(c) != pl.col(c_pq))
                )
            )
            if diff.height > 0:
                chk.fail(
                    f"{pq_path.name} {c}: {diff.height} string mismatches "
                    f"(sample: {diff.head(1).to_dicts()})"
                )
                mismatches += 1

        checked_rows += raw.height

    if mismatches == 0:
        chk.ok(
            f"value fidelity: {checked_rows} rows x {len(NUMERIC_COLS) + len(STRING_COLS) - 1} "
            f"columns across {len(picked)} files, zero mismatches"
        )


def check_null_parity(chk: Checker, present: list[tuple[str, date, Path]], sample_n: int) -> None:
    """For each sampled file, verify ``parquet_nulls ≥ raw_nulls`` for every
    column, and flag any column where parquet nulls differ from raw nulls
    (either direction). iem_asos_1min has no trace-sentinel replacement, so
    parity should be exact — any drift means the transform is dropping or
    inventing values.
    """
    if not present:
        return
    files = [pq for _, _, pq in present]
    picked = stratified_sample(files, sample_n)

    lost = 0
    invented = 0
    for pq_path in picked:
        station = pq_path.parent.name
        csv_path = RAW_DIR / station / f"{pq_path.stem}.csv"
        if not csv_path.exists():
            continue
        with csv_path.open() as f:
            reader = csv.DictReader(f)
            raw_nulls: dict[str, int] = {c: 0 for c in reader.fieldnames or []}
            for row in reader:
                for c, v in row.items():
                    if v is None or v == "" or v == "M":
                        raw_nulls[c] += 1
        pq_df = pl.read_parquet(pq_path)
        for c, rn in raw_nulls.items():
            target = "valid" if c == "valid(UTC)" else c
            if target not in pq_df.columns:
                continue
            pn = pq_df[target].null_count()
            if pn < rn:
                invented += 1
                chk.fail(
                    f"{pq_path.name} {c}: pq nulls {pn} < raw nulls {rn} "
                    f"(transform invented {rn - pn} non-null values)"
                )
            elif pn > rn:
                lost += 1
                chk.fail(
                    f"{pq_path.name} {c}: pq nulls {pn} > raw nulls {rn} "
                    f"(transform lost {pn - rn} raw values to cast failure)"
                )
    if lost == 0 and invented == 0:
        chk.ok(
            f"null-count parity: exact match across {len(picked)} files x "
            f"{len(REQUIRED_COLS)} columns — no values lost or invented"
        )


def check_timestamp_coverage(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """Every raw CSV data row produces a non-null ``valid`` datetime."""
    losses = 0
    for _station, _first, pq_path in present:
        station = pq_path.parent.name
        csv_path = RAW_DIR / station / f"{pq_path.stem}.csv"
        if not csv_path.exists():
            continue
        csv_rows, _ = _count_raw_csv_data_rows(csv_path)
        pq_df = pl.read_parquet(pq_path, columns=["valid"])
        pq_valid = pq_df["valid"].drop_nulls().len()
        if pq_valid != csv_rows:
            chk.fail(
                f"{pq_path.name}: {csv_rows - pq_valid} valid timestamps failed to parse "
                f"(raw rows={csv_rows}, non-null valid={pq_valid})"
            )
            losses += 1
    if losses == 0:
        chk.ok("every raw CSV row parsed to a non-null valid datetime")


# --- Level 4: schema invariants -------------------------------------------- #


def check_nan(chk: Checker) -> None:
    """No NaN in any Float64 column — cast failures must be nulls."""
    if not any(PROCESSED_DIR.rglob("*.parquet")):
        return
    full = pl.read_parquet(str(PROCESSED_DIR / "**/*.parquet"))
    num_cols = [c for c, d in full.schema.items() if d == pl.Float64]
    nans = 0
    for c in num_cols:
        n = full.filter(pl.col(c).is_not_null() & pl.col(c).is_nan()).height
        if n > 0:
            chk.fail(f"{c}: {n} NaN values (cast failures should be null, not NaN)")
            nans += 1
    if nans == 0:
        chk.ok(f"no NaN across {len(num_cols)} Float64 columns")


def check_second_offset(chk: Checker) -> None:
    """All ``valid`` timestamps land on ``:00`` seconds."""
    if not any(PROCESSED_DIR.rglob("*.parquet")):
        return
    full = pl.scan_parquet(str(PROCESSED_DIR / "**/*.parquet"))
    distinct_seconds = (
        full.select(pl.col("valid").dt.second().alias("s")).unique().collect()["s"].to_list()
    )
    if distinct_seconds == [0]:
        chk.ok("all timestamps land on the :00 second mark (clean 1-minute grid)")
    else:
        chk.warn(f"non-zero second offsets found: {sorted(distinct_seconds)[:10]}")


def check_gap_distribution(chk: Checker, stations: list[str]) -> None:
    """Report mean + max inter-observation gap per station."""
    if not any(PROCESSED_DIR.rglob("*.parquet")):
        return
    full = pl.read_parquet(str(PROCESSED_DIR / "**/*.parquet"), columns=["station", "valid"])
    for st in stations:
        st_df = full.filter(pl.col("station") == st).sort("valid")
        if st_df.height < 2:
            continue
        gaps = st_df.select(pl.col("valid").diff().alias("gap")).drop_nulls()
        max_gap = gaps["gap"].max()
        mean_gap = gaps["gap"].mean()
        assert isinstance(max_gap, timedelta) and isinstance(mean_gap, timedelta)
        if max_gap > MAX_OBS_GAP:
            chk.warn(f"{st}: max observation gap {max_gap} > {MAX_OBS_GAP} — possible outage")
        else:
            chk.ok(f"{st}: mean gap {mean_gap}, max gap {max_gap}")


def check_obs_per_hour_density(chk: Checker, stations: list[str]) -> None:
    """Average observations per hour per station should be ~60 (1-min feed)."""
    if not any(PROCESSED_DIR.rglob("*.parquet")):
        return
    full = pl.read_parquet(str(PROCESSED_DIR / "**/*.parquet"), columns=["station", "valid"])
    for st in stations:
        st_df = full.filter(pl.col("station") == st).with_columns(
            h=pl.col("valid").dt.truncate("1h")
        )
        per_hour = st_df.group_by("h").len()
        if per_hour.height == 0:
            continue
        mean_rate = per_hour["len"].mean()
        assert isinstance(mean_rate, int | float)
        low = per_hour.filter(pl.col("len") < 10).height
        if mean_rate < MIN_MEAN_OBS_PER_HOUR:
            chk.warn(
                f"{st}: mean {mean_rate:.1f} obs/hour < {MIN_MEAN_OBS_PER_HOUR} — "
                f"unusually sparse for a 1-minute feed"
            )
        else:
            chk.ok(
                f"{st}: mean {mean_rate:.1f} obs/hour, "
                f"{low} hours with <10 obs (station-level outages)"
            )


def check_date_coverage(
    chk: Checker, stations: list[str], start: date, end: date, today: date
) -> None:
    """Flag days with zero observations. We don't fail on this — real
    station outages do happen — but we do surface them as a warning so a
    human can spot-check against upstream.
    """
    if not any(PROCESSED_DIR.rglob("*.parquet")):
        return
    end_clip = min(end, today)
    full = pl.read_parquet(str(PROCESSED_DIR / "**/*.parquet"), columns=["station", "valid"])
    for st in stations:
        st_df = full.filter(pl.col("station") == st).with_columns(d=pl.col("valid").dt.date())
        days = set(st_df["d"].unique().to_list())
        missing = []
        d = start
        while d <= end_clip:
            if d not in days:
                missing.append(d)
            d += timedelta(days=1)
        if not missing:
            chk.ok(f"{st}: every day in [{start}, {end_clip}] has observations")
        else:
            chk.warn(
                f"{st}: {len(missing)} days with zero observations — "
                f"(first 5): {missing[:5]}. Likely real station outages; "
                f"spot-check against IEM if suspicious."
            )


# --- main ------------------------------------------------------------------ #


def main() -> int:
    args = parse_args()
    today = datetime.now(UTC).date()
    chk = Checker()

    print(f"validating {SOURCE_NAME} under {PROCESSED_DIR}")
    raw_doc, _proc_doc = check_manifests(chk)
    check_manifest_enumeration(chk)
    check_manifest_row_count(chk)

    # Derive station + date range from args, falling back to the raw manifest.
    if args.stations:
        stations = sorted(
            {s.upper().lstrip("K") if len(s) == 4 else s.upper() for s in args.stations}
        )
    elif raw_doc is not None:
        stations = sorted(raw_doc["download"]["stations"])
    else:
        chk.fail("no --stations and no raw manifest to infer from; cannot proceed")
        return 1

    if args.start:
        start = date.fromisoformat(args.start)
    elif raw_doc is not None:
        start = date.fromisoformat(raw_doc["download"]["start"])
    else:
        chk.fail("no --start and no raw manifest; cannot proceed")
        return 1

    end = date.fromisoformat(args.end) if args.end else today
    print(f"stations: {stations}  range: {start} → {end}")

    present = check_file_completeness(chk, stations, start, end)
    if present:
        check_csv_to_parquet_fidelity(chk, present)
        check_uniqueness(chk)
        check_cross_file_overlap(chk)
        check_month_containment(chk)
        check_schema_and_bounds(chk, present, today)
        check_value_fidelity(chk, present, args.fidelity_sample)
        check_null_parity(chk, present, args.null_parity_sample)
        check_timestamp_coverage(chk, present)
        check_nan(chk)
        check_second_offset(chk)
        check_gap_distribution(chk, stations)
        check_obs_per_hour_density(chk, stations)
        check_date_coverage(chk, stations, start, end, today)

    print()
    print(f"summary: {len(chk.errors)} errors, {len(chk.warnings)} warnings")
    if chk.errors:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

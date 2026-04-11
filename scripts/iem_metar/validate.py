#!/usr/bin/env python3
"""Validation checks for the iem_metar parquet output.

Runs a battery of checks against ``data/processed/iem_metar/`` to prove that
the download + transform pipeline produced usable Parquet for the requested
stations and date range:

1. **File completeness** — every (station, YYYY-MM) pair in the requested
   range has a corresponding parquet file on disk.
2. **Manifest present + complete** — ``MANIFEST.json`` status is ``complete``
   for both the raw download and the processed transform.
3. **Schema sanity** — every parquet has the required columns with the
   expected types (valid: Datetime[us, UTC], station: Utf8, tmpf: Float64,
   metar: Utf8, ...).
4. **Row counts** — each (station, month) has a plausible row count
   (≥ ~500 rows/month for a normal METAR feed; warn on < 400, fail on 0).
5. **Timestamp range** — every row's ``valid`` falls inside the file's
   month window; values are strictly monotonic non-decreasing after sort.
6. **No null stations** — ``station`` column is never null.
7. **Raw METAR string non-null** — the ``metar`` column is non-null for a
   strong majority of rows (> 95%); it's the source-of-truth for remark
   decoding.
8. **Temperature sanity** — ``tmpf`` values (when non-null) fall within
   [-80, 130] degF.
9. **Present-weather sampling** — pick the first file and summarise
   ``wxcodes`` frequency so the human can eyeball the distribution.

Usage::

    uv run python scripts/iem_metar/validate.py
    uv run python scripts/iem_metar/validate.py --stations NYC LGA
    uv run python scripts/iem_metar/validate.py --start 2025-12-20 --end 2026-04-11

Exit 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_NAME = "iem_metar"
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
PROCESSED_DIR = REPO_ROOT / "data" / "processed" / SOURCE_NAME
RAW_MANIFEST = RAW_DIR / "MANIFEST.json"
PROCESSED_MANIFEST = PROCESSED_DIR / "MANIFEST.json"

REQUIRED_COLS: dict[str, type[pl.DataType] | tuple[type[pl.DataType], ...]] = {
    "station": pl.Utf8,
    "valid": pl.Datetime,
    "tmpf": pl.Float64,
    "dwpf": pl.Float64,
    "drct": pl.Float64,
    "sknt": pl.Float64,
    "alti": pl.Float64,
    "vsby": pl.Float64,
    "skyc1": pl.Utf8,
    "wxcodes": pl.Utf8,
    "metar": pl.Utf8,
}

# Plausibility bounds for temperature (°F) — anything outside is almost
# certainly a parse error or a genuine data glitch worth investigating.
TMPF_MIN = -80.0
TMPF_MAX = 130.0

# Minimum METAR rows per station-month for a healthy feed. KLGA/KNYC tend to
# run ~750-900 hourly+SPECI rows/month. Below 400 is suspicious, 0 is a fail.
ROWS_MIN_WARN = 400
ROWS_MIN_FAIL = 1


# --- CLI ------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    today = datetime.now(UTC).date().isoformat()
    p = argparse.ArgumentParser(
        description="Validate iem_metar parquet output.",
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


# --- checks ---------------------------------------------------------------- #


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


def check_file_completeness(
    chk: Checker, stations: list[str], start: date, end: date
) -> list[tuple[str, date, Path]]:
    """For each expected (station, month), require a parquet on disk.

    Returns the list of (station, month_first, parquet_path) tuples for the
    files that DO exist, so downstream checks can iterate them.
    """
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


def check_schema_and_rows(chk: Checker, present: list[tuple[str, date, Path]], today: date) -> int:
    total_rows = 0
    for station, first, pq in present:
        df = pl.read_parquet(pq)
        schema = df.schema

        for col, expected in REQUIRED_COLS.items():
            if col not in schema:
                chk.fail(f"{pq.name} ({station}): missing column {col!r}")
                continue
            got = schema[col]
            if isinstance(expected, tuple):
                if not isinstance(got, expected):
                    chk.fail(f"{pq.name} ({station}): {col} is {got}, expected one of {expected}")
            else:
                if not isinstance(got, expected):
                    chk.fail(f"{pq.name} ({station}): {col} is {got}, expected {expected}")

        rows = df.height
        total_rows += rows
        last = month_end(first)
        is_current_month = first >= today.replace(day=1)
        if rows < ROWS_MIN_FAIL:
            chk.fail(f"{pq.name} ({station}): {rows} rows (fail threshold {ROWS_MIN_FAIL})")
        elif rows < ROWS_MIN_WARN and not is_current_month:
            # Current month is partial — suppress the low-rows warning there.
            chk.warn(f"{pq.name} ({station}): {rows} rows (below warn threshold {ROWS_MIN_WARN})")

        if "station" in df.columns and df["station"].null_count() > 0:
            chk.fail(f"{pq.name} ({station}): station column has nulls")

        if "valid" in df.columns and rows > 0:
            vmin = df["valid"].min()
            vmax = df["valid"].max()
            assert isinstance(vmin, datetime) and isinstance(vmax, datetime)
            if vmin.date() < first:
                chk.fail(f"{pq.name} ({station}): earliest valid {vmin} < month start {first}")
            if vmax.date() > last:
                chk.fail(f"{pq.name} ({station}): latest valid {vmax} > month end {last}")

        if "tmpf" in df.columns:
            tmp = df["tmpf"].drop_nulls()
            if tmp.len() > 0:
                lo = tmp.min()
                hi = tmp.max()
                assert isinstance(lo, int | float) and isinstance(hi, int | float)
                if lo < TMPF_MIN or hi > TMPF_MAX:
                    chk.fail(
                        f"{pq.name} ({station}): tmpf range [{lo}, {hi}] "
                        f"escapes [{TMPF_MIN}, {TMPF_MAX}]"
                    )

        if "metar" in df.columns and rows > 0:
            null_frac = df["metar"].null_count() / rows
            if null_frac > 0.05:
                chk.fail(f"{pq.name} ({station}): metar null fraction {null_frac:.1%} > 5%")

    if total_rows > 0:
        chk.ok(f"schema + row checks: {total_rows} total rows across {len(present)} files")
    return total_rows


def sample_wxcodes(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    if not present:
        return
    _, _, pq = present[0]
    df = pl.read_parquet(pq, columns=["wxcodes"])
    freq = (
        df.filter(pl.col("wxcodes").is_not_null())
        .group_by("wxcodes")
        .len()
        .sort("len", descending=True)
        .head(10)
    )
    if freq.height == 0:
        chk.ok(f"wxcodes sample ({pq.name}): all null (dry/clear month?)")
        return
    lines = [f"  {row['wxcodes']!r}: {row['len']}" for row in freq.iter_rows(named=True)]
    chk.ok(f"wxcodes top-10 in {pq.name}:\n" + "\n".join(lines))


# --- main ------------------------------------------------------------------ #


def main() -> int:
    args = parse_args()
    today = datetime.now(UTC).date()
    chk = Checker()

    print(f"validating {SOURCE_NAME} under {PROCESSED_DIR}")
    raw_doc, _proc_doc = check_manifests(chk)

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
        check_schema_and_rows(chk, present, today)
        sample_wxcodes(chk, present)

    print()
    print(f"summary: {len(chk.errors)} errors, {len(chk.warnings)} warnings")
    if chk.errors:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

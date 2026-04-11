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
import csv
import json
import sys
from datetime import UTC, date, datetime, timedelta
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
    # RMK-decoded v2 columns.
    "temp_c": pl.Float64,
    "dewpt_c": pl.Float64,
    "slp_mb_rmk": pl.Float64,
    "max_temp_6hr_c": pl.Float64,
    "min_temp_6hr_c": pl.Float64,
    "max_temp_24hr_c": pl.Float64,
    "min_temp_24hr_c": pl.Float64,
    "precip_6hr_in": pl.Float64,
    "precip_24hr_in": pl.Float64,
    "press_tendency_3hr_mb": pl.Float64,
    "press_tendency_3hr_code": pl.Int64,
    "presrr": pl.Boolean,
    "presfr": pl.Boolean,
    "tsb_minute": pl.Int64,
    "tse_minute": pl.Int64,
}

# Plausibility bounds for temperature (°F) — anything outside is almost
# certainly a parse error or a genuine data glitch worth investigating.
TMPF_MIN = -80.0
TMPF_MAX = 130.0

# Plausibility bound for celsius-from-RMK columns. Bounds are deliberately
# generous — even extreme CONUS events don't approach these.
TEMPC_MIN = -60.0
TEMPC_MAX = 55.0

# Plausibility bound for SLP. Real-world extremes for CONUS: ~925 mb in
# strong hurricanes, ~1060 mb in arctic highs.
SLP_MB_MIN = 900.0
SLP_MB_MAX = 1080.0

# Minimum METAR rows per station-month for a healthy feed. KLGA/KNYC tend to
# run ~750-900 hourly+SPECI rows/month. Below 400 is suspicious, 0 is a fail.
ROWS_MIN_WARN = 400
ROWS_MIN_FAIL = 1

# Maximum tolerated gap between consecutive observations per station. KLGA
# and KNYC should report at least hourly; anything over this is a genuine
# data outage worth flagging.
MAX_OBS_GAP = timedelta(hours=6)


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

        # Raw METAR strings must be 100% non-null; they're the source of
        # truth for every RMK-decoded column and for any downstream remark
        # re-parse. A null here means we dropped data upstream.
        if "metar" in df.columns and rows > 0:
            metar_nulls = df["metar"].null_count()
            if metar_nulls > 0:
                chk.fail(
                    f"{pq.name} ({station}): {metar_nulls}/{rows} rows have null metar string "
                    f"— raw text must be 100% non-null"
                )

        # temp_c / dewpt_c plausibility.
        for col, lo_bound, hi_bound in (
            ("temp_c", TEMPC_MIN, TEMPC_MAX),
            ("dewpt_c", TEMPC_MIN, TEMPC_MAX),
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

        # slp_mb_rmk plausibility.
        if "slp_mb_rmk" in df.columns:
            vals = df["slp_mb_rmk"].drop_nulls()
            if vals.len() > 0:
                lo = vals.min()
                hi = vals.max()
                assert isinstance(lo, int | float) and isinstance(hi, int | float)
                if lo < SLP_MB_MIN or hi > SLP_MB_MAX:
                    chk.fail(
                        f"{pq.name} ({station}): slp_mb_rmk range [{lo}, {hi}] "
                        f"escapes [{SLP_MB_MIN}, {SLP_MB_MAX}]"
                    )

    if total_rows > 0:
        chk.ok(f"schema + row checks: {total_rows} total rows across {len(present)} files")
    return total_rows


# --- fidelity audit -------------------------------------------------------- #


def _count_raw_csv_data_rows(csv_path: Path) -> tuple[int, list[str]]:
    """Read an IEM METAR CSV, skip the ``#DEBUG:`` preamble, return
    ``(data_row_count, header_columns)``.
    """
    with csv_path.open() as f:
        lines = [ln.rstrip("\n") for ln in f]
    header_idx: int | None = None
    for i, ln in enumerate(lines):
        if not ln.startswith("#"):
            header_idx = i
            break
    if header_idx is None:
        return 0, []
    header = lines[header_idx].split(",")
    data_rows = [ln for ln in lines[header_idx + 1 :] if ln.strip()]
    return len(data_rows), header


def check_csv_to_parquet_fidelity(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """Verify the raw CSV → Parquet transform preserves every row and every
    column. Catches two failure classes the shape-only checks miss:

    * **row dropping** — polars silently skipping a malformed row at read
      time, or our ``#``-comment stripping eating a legitimate row
    * **column dropping** — a raw column present in IEM's output not being
      written into the parquet because our rename / cast map missed it
    """
    mismatches = 0
    for station, _first, pq_path in present:
        csv_path = RAW_DIR / station / f"{pq_path.stem}.csv"
        if not csv_path.exists():
            chk.fail(f"raw csv missing for fidelity check: {csv_path.relative_to(REPO_ROOT)}")
            mismatches += 1
            continue

        csv_rows, csv_cols = _count_raw_csv_data_rows(csv_path)
        pq_df = pl.read_parquet(pq_path)

        if pq_df.height != csv_rows:
            chk.fail(
                f"row mismatch {station}/{pq_path.stem}: csv={csv_rows}, parquet={pq_df.height}"
            )
            mismatches += 1

        # Column-set fidelity: every raw CSV column must appear in the
        # parquet, with the single rename ``valid(UTC)`` → ``valid`` when
        # present in older CSVs.
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
            f"raw CSV ↔ parquet fidelity: {len(present)} files, "
            f"row counts 1:1, every raw column preserved"
        )


def check_csv_null_parity(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """For every (station, month) file, verify that null counts in the
    parquet are ≥ raw-CSV null counts (cast failures can increase nulls,
    but the transform can never invent a non-null value).

    Also fails if any raw-non-null numeric value was silently cast to null
    — with the one allowed exception of the ``p01i`` trace-precip sentinel
    (``T`` → ``0.0001``), which legitimately REDUCES the null count.
    """
    invented = 0
    lost = 0
    for station, _first, pq_path in present:
        csv_path = RAW_DIR / station / f"{pq_path.stem}.csv"
        if not csv_path.exists():
            continue
        pq_df = pl.read_parquet(pq_path)

        with csv_path.open() as f:
            rows = [ln for ln in f if not ln.startswith("#")]
        reader = csv.DictReader(rows)
        raw_null_counts: dict[str, int] = {c: 0 for c in reader.fieldnames or []}
        for row in reader:
            for c, v in row.items():
                if v is None or v == "" or v == "M":
                    raw_null_counts[c] += 1

        for c, raw_nulls in raw_null_counts.items():
            target = "valid" if c == "valid(UTC)" else c
            if target not in pq_df.columns:
                continue
            pq_nulls = pq_df[target].null_count()
            if pq_nulls < raw_nulls:
                # Transform reduced nulls — only legal for p01i trace sentinel.
                if c != "p01i":
                    chk.fail(
                        f"{station}/{pq_path.stem} {c}: pq nulls {pq_nulls} < raw nulls "
                        f"{raw_nulls} — transform invented values"
                    )
                    invented += 1
            elif pq_nulls > raw_nulls:
                chk.fail(
                    f"{station}/{pq_path.stem} {c}: pq nulls {pq_nulls} > raw nulls "
                    f"{raw_nulls} — {pq_nulls - raw_nulls} raw values lost to cast failure"
                )
                lost += 1
    if invented == 0 and lost == 0:
        chk.ok(
            "null-count parity: no non-null raw values lost to cast failure, "
            "no nulls invented (trace-precip 'T' conversion allowed)"
        )


def check_timestamp_coverage(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """Verify that every raw CSV data row produced a non-null ``valid``
    timestamp in the parquet — i.e., no row was silently dropped at parse
    time or given a null datetime by strptime.
    """
    losses = 0
    for station, _first, pq_path in present:
        csv_path = RAW_DIR / station / f"{pq_path.stem}.csv"
        if not csv_path.exists():
            continue
        csv_rows, _ = _count_raw_csv_data_rows(csv_path)
        pq_df = pl.read_parquet(pq_path, columns=["valid"])
        pq_valid = pq_df["valid"].drop_nulls().len()
        if pq_valid != csv_rows:
            chk.fail(
                f"{station}/{pq_path.stem}: {csv_rows - pq_valid} valid timestamps "
                f"failed to parse (raw rows={csv_rows}, non-null valid={pq_valid})"
            )
            losses += 1
    if losses == 0:
        chk.ok("every raw CSV row parsed to a non-null valid timestamp")


def check_gap_distribution(
    chk: Checker, stations: list[str], present: list[tuple[str, date, Path]]
) -> None:
    """Largest inter-observation gap per station. IEM reports hourly +
    SPECI so typical gaps are ≤ 1 hour; anything > MAX_OBS_GAP is a real
    outage worth surfacing.
    """
    if not present:
        return
    full = pl.read_parquet([str(p) for _, _, p in present])
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


def check_report_type_mix(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """Confirm we have both routine METARs and SPECI specials. IEM strips
    the ``METAR``/``SPECI`` type marker from the raw string, so we detect
    SPECI by non-standard observation minute — routine reports land at
    ``:51`` past the hour, SPECIs fire off-cycle on rapid change.
    """
    if not present:
        return
    full = pl.read_parquet([str(p) for _, _, p in present]).with_columns(
        minute=pl.col("valid").dt.minute()
    )
    routine = full.filter(pl.col("minute") == 51).height
    speci = full.height - routine
    chk.ok(
        f"report types: {routine} routine ({routine / full.height:.1%}) + "
        f"{speci} SPECI ({speci / full.height:.1%})"
    )
    if speci == 0 and full.height > 100:
        chk.warn(
            "zero SPECI reports — unusual over a multi-month window. "
            "Verify --report_type=4 is set on download."
        )


def check_rmk_temp_consistency(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """Cross-check ``temp_c`` against IEM's ``tmpf``: every row with both
    non-null should satisfy ``round(temp_c * 1.8 + 32) == tmpf``. A
    mismatch means either the RMK parser or IEM is wrong.
    """
    if not present:
        return
    full = pl.read_parquet([str(p) for _, _, p in present])
    both = full.filter(pl.col("tmpf").is_not_null() & pl.col("temp_c").is_not_null())
    if both.height == 0:
        return
    disagree = both.with_columns(predicted=(pl.col("temp_c") * 9 / 5 + 32).round(0)).filter(
        (pl.col("tmpf") - pl.col("predicted")).abs() > 1
    )
    if disagree.height > 0:
        chk.fail(
            f"temp_c ↔ tmpf consistency: {disagree.height}/{both.height} rows "
            f"disagree by > 1°F after rounding"
        )
    else:
        chk.ok(f"temp_c ↔ tmpf consistency: {both.height} rows match within 1°F rounding")


def check_slp_redundancy(chk: Checker, present: list[tuple[str, date, Path]]) -> None:
    """``slp_mb_rmk`` is re-decoded from the raw METAR RMK SLP-group; IEM
    already decodes the same group into ``mslp``. They should agree
    exactly on every row where either is non-null. If they diverge,
    upstream parsing has changed and we need to look.
    """
    if not present:
        return
    full = pl.read_parquet([str(p) for _, _, p in present])
    both = full.filter(pl.col("mslp").is_not_null() & pl.col("slp_mb_rmk").is_not_null())
    only_mslp = full.filter(pl.col("mslp").is_not_null() & pl.col("slp_mb_rmk").is_null()).height
    only_rmk = full.filter(pl.col("mslp").is_null() & pl.col("slp_mb_rmk").is_not_null()).height
    if both.height > 0:
        disagree = both.filter(pl.col("mslp") != pl.col("slp_mb_rmk")).height
        if disagree > 0:
            chk.fail(
                f"slp_mb_rmk ↔ mslp: {disagree}/{both.height} rows disagree "
                f"— IEM and python-metar parsed the same RMK SLP group differently"
            )
        elif only_mslp > 0 or only_rmk > 0:
            chk.warn(
                f"slp_mb_rmk ↔ mslp coverage skew: only_mslp={only_mslp}, "
                f"only_rmk={only_rmk} (expected: identical null patterns)"
            )
        else:
            chk.ok(
                f"slp_mb_rmk ↔ mslp: exact agreement on {both.height} rows, identical null coverage"
            )


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
        check_csv_to_parquet_fidelity(chk, present)
        check_csv_null_parity(chk, present)
        check_timestamp_coverage(chk, present)
        check_report_type_mix(chk, present)
        check_gap_distribution(chk, stations, present)
        check_rmk_temp_consistency(chk, present)
        check_slp_redundancy(chk, present)
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

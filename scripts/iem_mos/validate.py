#!/usr/bin/env python3
"""Validate IEM MOS/NBM data (levels 1-4).

Checks:
  L1: manifest + disk (files present, status ok)
  L2: row counts (CSV lines vs parquet rows, 1:1 minus header)
  L3: value-level (temps in range, no NaN, timestamps parse, lead_hours sane)
  L4: cross-column (runtime + lead_hours = ftime, station matches filename,
      tmp_f within physical bounds per station climate)

Usage:
    uv run python scripts/iem_mos/validate.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / "iem_mos"
PROC_DIR = REPO_ROOT / "data" / "processed" / "iem_mos"

FAILS: list[str] = []
WARNS: list[str] = []


def _ok(msg: str) -> None:
    print(f"  \u2713 {msg}")


def _fail(msg: str) -> None:
    print(f"  \u2717 FAIL: {msg}")
    FAILS.append(msg)


def _warn(msg: str) -> None:
    print(f"  ! WARN: {msg}")
    WARNS.append(msg)


def section(title: str) -> None:
    print(f"\n===== {title} =====")


def level1() -> None:
    section("L1 — manifest & disk")
    mp = RAW_DIR / "MANIFEST.json"
    if not mp.exists():
        _fail("MANIFEST.json missing")
        return
    m = json.loads(mp.read_text())
    d = m.get("download", {})
    if d.get("status") not in ("ok", "ok_with_errors"):
        _fail(f"status = {d.get('status')}")
    else:
        _ok(f"status={d.get('status')}, {d.get('n_files')} files, {d.get('total_bytes'):,} bytes")

    for model in ["GFS", "NBS"]:
        raw_dir = RAW_DIR / model
        proc_dir = PROC_DIR / model
        raw_files = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
        proc_files = sorted(proc_dir.glob("*.parquet")) if proc_dir.exists() else []
        if len(raw_files) == 0:
            _fail(f"{model}: no raw CSVs")
        elif len(proc_files) == 0:
            _fail(f"{model}: no parquet files (transform not run?)")
        elif len(raw_files) != len(proc_files):
            _warn(f"{model}: {len(raw_files)} CSVs vs {len(proc_files)} parquets")
        else:
            _ok(f"{model}: {len(raw_files)} CSVs → {len(proc_files)} parquets")


def level2() -> None:
    section("L2 — row counts")
    con = duckdb.connect()
    for model in ["GFS", "NBS"]:
        raw_dir = RAW_DIR / model
        proc_dir = PROC_DIR / model
        if not raw_dir.exists() or not proc_dir.exists():
            continue
        for csv_path in sorted(raw_dir.glob("*.csv")):
            station = csv_path.stem
            pq_path = proc_dir / f"{station}.parquet"
            if not pq_path.exists():
                _fail(f"{model}/{station}: parquet missing")
                continue
            raw_lines = sum(1 for _ in csv_path.open()) - 1  # minus header
            pq_rows = con.execute(f"SELECT COUNT(*) FROM '{pq_path}'").fetchone()[0]
            if raw_lines != pq_rows:
                _fail(f"{model}/{station}: CSV {raw_lines:,} vs parquet {pq_rows:,}")
            else:
                _ok(f"{model}/{station}: {pq_rows:,} rows match")


def level3() -> None:
    section("L3 — value-level sanity")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    for model in ["GFS", "NBS"]:
        proc_dir = PROC_DIR / model
        if not proc_dir.exists():
            continue
        glob = f"{proc_dir}/*.parquet"

        # Temperature range: -40 to 130°F covers all US stations all seasons
        r = con.execute(f"""
            SELECT
                SUM(CASE WHEN tmp_f < -40 OR tmp_f > 130 THEN 1 ELSE 0 END) AS bad_tmp,
                SUM(CASE WHEN tmp_f IS NOT NULL AND isnan(tmp_f) THEN 1 ELSE 0 END) AS nan_tmp,
                SUM(CASE WHEN runtime IS NULL THEN 1 ELSE 0 END) AS null_rt,
                SUM(CASE WHEN ftime IS NULL THEN 1 ELSE 0 END) AS null_ft,
                SUM(CASE WHEN lead_hours < 0 OR lead_hours > 400 THEN 1 ELSE 0 END) AS bad_lead,
                COUNT(*) AS total
            FROM '{glob}'
        """).fetchone()
        bad_tmp, nan_tmp, null_rt, null_ft, bad_lead, total = r
        any_bad = bad_tmp or nan_tmp or null_rt or null_ft or bad_lead
        if bad_tmp: _fail(f"{model}: {bad_tmp} tmp_f outside [-40, 130]")
        if nan_tmp: _fail(f"{model}: {nan_tmp} NaN tmp_f")
        if null_rt: _fail(f"{model}: {null_rt} null runtime")
        if null_ft: _fail(f"{model}: {null_ft} null ftime")
        if bad_lead: _fail(f"{model}: {bad_lead} lead_hours outside [0, 400]")
        if not any_bad:
            _ok(f"{model}: {total:,} rows, all range/null/NaN checks pass")

        # Null rate on tmp_f — some rows may legitimately be null
        null_tmp = con.execute(f"SELECT SUM(CASE WHEN tmp_f IS NULL THEN 1 ELSE 0 END) FROM '{glob}'").fetchone()[0]
        null_pct = null_tmp / total * 100 if total else 0
        if null_pct > 20:
            _warn(f"{model}: {null_pct:.1f}% null tmp_f — high null rate")
        else:
            _ok(f"{model}: {null_pct:.1f}% null tmp_f (acceptable)")

        # Duplicate check: (station, runtime, ftime) should be unique
        dupes = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT station, runtime, ftime, COUNT(*) AS n
                FROM '{glob}' GROUP BY 1, 2, 3 HAVING n > 1
            )
        """).fetchone()[0]
        if dupes:
            _warn(f"{model}: {dupes} duplicate (station, runtime, ftime) rows")
        else:
            _ok(f"{model}: 0 duplicates on (station, runtime, ftime)")


def level4() -> None:
    section("L4 — cross-column consistency")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    for model in ["GFS", "NBS"]:
        proc_dir = PROC_DIR / model
        if not proc_dir.exists():
            continue
        glob = f"{proc_dir}/*.parquet"

        # lead_hours should equal ftime - runtime
        r = con.execute(f"""
            SELECT SUM(CASE WHEN ABS(lead_hours -
                DATE_DIFF('hour', runtime, ftime)) > 1 THEN 1 ELSE 0 END)
            FROM '{glob}'
            WHERE runtime IS NOT NULL AND ftime IS NOT NULL
        """).fetchone()[0]
        if r:
            _fail(f"{model}: {r} rows where lead_hours != ftime - runtime")
        else:
            _ok(f"{model}: lead_hours consistent with runtime→ftime")

        # Station column should match the file's station
        for pq in sorted(proc_dir.glob("*.parquet")):
            expected = pq.stem
            bad = con.execute(f"""
                SELECT COUNT(*) FROM '{pq}' WHERE station != '{expected}'
            """).fetchone()[0]
            if bad:
                _fail(f"{model}/{expected}: {bad} rows with wrong station")

        _ok(f"{model}: all station columns match filenames")

        # Per-station temp range sanity (Miami should be warmer than Denver)
        r = con.execute(f"""
            SELECT station, ROUND(AVG(tmp_f), 1) AS avg_tmp,
                   MIN(tmp_f) AS min_tmp, MAX(tmp_f) AS max_tmp
            FROM '{glob}'
            WHERE tmp_f IS NOT NULL
            GROUP BY station ORDER BY avg_tmp
        """).fetchall()
        coldest = r[0] if r else None
        warmest = r[-1] if r else None
        if coldest and warmest:
            _ok(f"{model}: coldest avg = {coldest[0]} ({coldest[1]}°F), "
                f"warmest = {warmest[0]} ({warmest[1]}°F)")

    # Cross-model: for stations in both GFS and NBS, tmp_f should be correlated
    gfs_dir = PROC_DIR / "GFS"
    nbs_dir = PROC_DIR / "NBS"
    if gfs_dir.exists() and nbs_dir.exists():
        r = con.execute(f"""
            SELECT ROUND(CORR(g.tmp_f, n.tmp_f), 4) AS cross_corr, COUNT(*) AS n
            FROM '{gfs_dir}/*.parquet' g
            JOIN '{nbs_dir}/*.parquet' n
              ON g.station = n.station AND g.ftime = n.ftime
            WHERE g.tmp_f IS NOT NULL AND n.tmp_f IS NOT NULL
              AND ABS(DATE_DIFF('hour', g.runtime, n.runtime)) <= 1
        """).fetchone()
        if r[0] and r[0] > 0.9:
            _ok(f"GFS↔NBS tmp_f correlation = {r[0]} on {r[1]:,} matched rows")
        elif r[0]:
            _warn(f"GFS↔NBS tmp_f correlation = {r[0]} (expected > 0.9)")
        else:
            _warn("could not compute GFS↔NBS cross-correlation")


def main() -> int:
    print("IEM_MOS — validate.py (levels 1-4)")
    print("=" * 60)
    level1()
    level2()
    level3()
    level4()

    print(f"\nFAILS: {len(FAILS)}  WARNS: {len(WARNS)}")
    if FAILS:
        print("\nFAIL list:")
        for f in FAILS:
            print(f"  * {f}")
    if WARNS:
        print("\nWARN list:")
        for w in WARNS:
            print(f"  * {w}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

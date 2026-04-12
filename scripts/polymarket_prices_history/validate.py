"""Permanent validator for polymarket_prices_history.

Runs levels 1-4 of the data-validation contract against the raw JSONs
and the partitioned parquet output (hourly + min1). Level 5 (fresh
upstream re-fetch) is documented but not run automatically.

Exit code 0 on success, 1 on any FAIL.

Usage:
    uv run python scripts/polymarket_prices_history/validate.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_HIST = REPO_ROOT / "data" / "raw" / "polymarket_prices_history"
PROC_HIST = REPO_ROOT / "data" / "processed" / "polymarket_prices_history"
HOURLY = PROC_HIST / "hourly"
MIN1 = PROC_HIST / "min1"
MARKETS = REPO_ROOT / "data" / "processed" / "polymarket_weather" / "markets.parquet"

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


def level1_manifest() -> None:
    section("L1 — manifest & disk")
    mp = RAW_HIST / "MANIFEST.json"
    if not mp.exists():
        _fail("MANIFEST.json missing")
        return
    try:
        m = json.loads(mp.read_text())
    except Exception as e:
        _fail(f"MANIFEST.json parse error: {e}")
        return
    d = m.get("download", {})
    if d.get("status") not in ("ok", "ok_with_errors"):
        _fail(f"download status = {d.get('status')}")
    else:
        _ok(f"status={d.get('status')}, n_done={d.get('n_slugs_done')}/{d.get('n_slugs_planned')}, "
            f"empty={d.get('n_slugs_empty')}, failed={d.get('n_slugs_failed')}")

    # Count slug JSONs on disk (excluding MANIFEST.json)
    json_files = [p for p in RAW_HIST.glob("*.json") if p.name != "MANIFEST.json"]
    planned = d.get("n_slugs_planned", 0)
    if len(json_files) < planned * 0.95:
        _warn(f"only {len(json_files)} slug JSONs on disk, expected ~{planned}")
    else:
        _ok(f"{len(json_files)} slug JSONs on disk")


def level2_row_count() -> None:
    section("L2 — raw JSON row count vs parquet")
    json_files = [p for p in RAW_HIST.glob("*.json") if p.name != "MANIFEST.json"]
    n_h60_raw = 0
    n_min1_raw = 0
    bad = 0
    for jf in json_files:
        try:
            d = json.loads(jf.read_text())
        except Exception:
            bad += 1
            continue
        n_h60_raw += len(d.get("history_max_h60", []) or [])
        n_min1_raw += len(d.get("history_1d_min1", []) or [])
    if bad:
        _fail(f"{bad} JSONs failed to parse")
    else:
        _ok(f"{len(json_files)} JSONs parsed; raw h60 rows = {n_h60_raw:,}, min1 = {n_min1_raw:,}")

    con = duckdb.connect()

    if not HOURLY.exists():
        _warn("hourly parquet missing (transform.py not run?)")
        return
    n_h = con.execute(f"SELECT COUNT(*) FROM '{HOURLY}/**/*.parquet'").fetchone()[0]
    if n_h != n_h60_raw:
        _fail(f"hourly parquet {n_h:,} != raw h60 {n_h60_raw:,} (delta {n_h - n_h60_raw:+})")
    else:
        _ok(f"hourly: {n_h:,} rows match raw")

    if not MIN1.exists():
        _warn("min1 parquet missing")
        return
    n_m = con.execute(f"SELECT COUNT(*) FROM '{MIN1}/**/*.parquet'").fetchone()[0]
    if n_m != n_min1_raw:
        _fail(f"min1 parquet {n_m:,} != raw min1 {n_min1_raw:,} (delta {n_m - n_min1_raw:+})")
    else:
        _ok(f"min1: {n_m:,} rows match raw")


def level3_value_sanity() -> None:
    section("L3 — value-level sanity")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    for variant, path in [("hourly", HOURLY), ("min1", MIN1)]:
        if not path.exists():
            continue
        r = con.execute(f"""
            SELECT
                SUM(CASE WHEN p_yes < 0 OR p_yes > 1 THEN 1 ELSE 0 END) AS bad_p,
                SUM(CASE WHEN isnan(p_yes) THEN 1 ELSE 0 END) AS nan_p,
                SUM(CASE WHEN p_yes IS NULL THEN 1 ELSE 0 END) AS null_p,
                SUM(CASE WHEN slug IS NULL THEN 1 ELSE 0 END) AS null_slug,
                SUM(CASE WHEN timestamp IS NULL THEN 1 ELSE 0 END) AS null_ts,
                COUNT(*) AS total
            FROM '{path}/**/*.parquet'
        """).fetchone()
        bad_p, nan_p, null_p, null_slug, null_ts, total = r
        any_bad = bad_p or nan_p or null_p or null_slug or null_ts
        if bad_p: _fail(f"{variant}: {bad_p} p_yes outside [0, 1]")
        if nan_p: _fail(f"{variant}: {nan_p} NaN p_yes")
        if null_p: _fail(f"{variant}: {null_p} null p_yes")
        if null_slug: _fail(f"{variant}: {null_slug} null slug")
        if null_ts: _fail(f"{variant}: {null_ts} null timestamp")
        if not any_bad:
            _ok(f"{variant}: {total:,} rows, all value checks pass")

        # Duplicate (slug, timestamp) — known upstream issue, warn not fail
        n_dupes = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT slug, timestamp, COUNT(*) AS n
                FROM '{path}/**/*.parquet'
                GROUP BY 1, 2 HAVING n > 1
            )
        """).fetchone()[0]
        if n_dupes:
            _warn(f"{variant}: {n_dupes} duplicate (slug, timestamp) pairs — known upstream emitter issue, dedup at query time with DISTINCT ON")
        else:
            _ok(f"{variant}: 0 duplicate (slug, timestamp) pairs")


def level4_cross_column() -> None:
    section("L4 — cross-column / schema invariants")
    if not MARKETS.exists():
        _warn("markets.parquet missing, skipping L4")
        return
    if not HOURLY.exists():
        _warn("hourly parquet missing, skipping L4")
        return
    con = duckdb.connect()

    r = con.execute(f"""
        WITH h AS (SELECT DISTINCT slug, condition_id, yes_token_id
                   FROM '{HOURLY}/**/*.parquet')
        SELECT COUNT(*) AS n_bad
        FROM h
        LEFT JOIN '{MARKETS}' m USING (slug)
        WHERE m.condition_id != h.condition_id OR m.yes_token_id != h.yes_token_id
    """).fetchone()[0]
    if r:
        _fail(f"{r} slugs in hourly with condition_id or yes_token_id mismatch vs markets.parquet")
    else:
        _ok("condition_id + yes_token_id consistent with markets.parquet")


def main() -> int:
    print("POLYMARKET_PRICES_HISTORY — validate.py (levels 1-4)")
    print("=" * 60)
    level1_manifest()
    level2_row_count()
    level3_value_sanity()
    level4_cross_column()

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

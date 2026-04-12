"""Permanent validator for polymarket_book.

Runs levels 1-4 of the data-validation contract (see
`.claude/skills/data-validation/SKILL.md`) against the raw JSONL
stream and the top-of-book parquet output. Level 5 (fresh upstream
re-fetch) is documented but not run automatically — invoke it
during deep audits.

Exit code 0 on success, 1 on any FAIL (WARN does not fail).

Usage:
    uv run python scripts/polymarket_book/validate.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_BOOK = REPO_ROOT / "data" / "raw" / "polymarket_book"
TOB = REPO_ROOT / "data" / "processed" / "polymarket_book" / "tob"
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


def level1_manifest_and_disk() -> None:
    section("L1 — manifest & disk")
    mp = RAW_BOOK / "MANIFEST.json"
    if not mp.exists():
        _fail("MANIFEST.json missing")
        return
    try:
        m = json.loads(mp.read_text())
    except Exception as e:
        _fail(f"MANIFEST.json parse error: {e}")
        return
    if m.get("source_name") != "polymarket_book":
        _fail(f"wrong source_name: {m.get('source_name')}")
        return
    _ok(f"manifest parses, n_slugs_subscribed={m.get('n_slugs_subscribed')}")

    slug_dirs = [p for p in RAW_BOOK.iterdir() if p.is_dir() and p.name != "_unknown"]
    subscribed = set(m.get("slugs") or [])
    on_disk = {p.name for p in slug_dirs}
    missing = subscribed - on_disk
    if missing:
        _warn(f"{len(missing)} subscribed slugs have no directory (usually resolved markets "
              f"with no messages): e.g. {sorted(missing)[:3]}")
    else:
        _ok(f"all {len(subscribed)} subscribed slugs present on disk")

    unk = RAW_BOOK / "_unknown"
    if unk.exists():
        n = sum(1 for _ in unk.iterdir())
        _fail(f"_unknown/ directory exists with {n} entries — routing bug")
    else:
        _ok("no _unknown/ directory (routing clean)")


def level2_row_fidelity() -> None:
    section("L2 — raw JSONL vs tob parquet row count")
    jsonl_files = list(RAW_BOOK.rglob("*.jsonl"))
    raw_lines = 0
    parse_failures = 0
    type_counts: dict[str, int] = {}
    for jf in jsonl_files:
        with jf.open() as fh:
            for line in fh:
                raw_lines += 1
                try:
                    msg = json.loads(line)
                    et = msg.get("event_type", "?")
                    type_counts[et] = type_counts.get(et, 0) + 1
                except Exception:
                    parse_failures += 1
    if parse_failures:
        _fail(f"{parse_failures} of {raw_lines:,} JSONL lines failed to parse")
    else:
        _ok(f"raw JSONL: {raw_lines:,} lines across {len(jsonl_files)} files, 100% parseable")

    if not TOB.exists():
        _warn("tob parquet missing (transform.py has not run yet)")
        return

    con = duckdb.connect()
    tob_rows = con.execute(f"SELECT COUNT(*) FROM '{TOB}/**/*.parquet'").fetchone()[0]
    # Book rows contribute 1 row per msg; price_change contributes up to 2 (both sides)
    expected_max = type_counts.get("book", 0) + type_counts.get("price_change", 0) * 2
    if tob_rows > expected_max:
        _fail(f"tob rows {tob_rows:,} > max expected {expected_max:,}")
    elif tob_rows < expected_max * 0.95:
        _warn(f"tob rows {tob_rows:,} < 95% of expected max {expected_max:,}")
    else:
        _ok(f"tob rows {tob_rows:,} within [0.95, 1.0] of expected max {expected_max:,}")


def level3_value_sanity() -> None:
    section("L3 — value-level sanity on tob parquet")
    if not TOB.exists():
        _warn("tob parquet missing, skipping L3")
        return
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    r = con.execute(f"""
        SELECT
            SUM(CASE WHEN best_bid < 0 OR best_bid > 1 THEN 1 ELSE 0 END) AS bad_bid,
            SUM(CASE WHEN best_ask < 0 OR best_ask > 1 THEN 1 ELSE 0 END) AS bad_ask,
            SUM(CASE WHEN mid < 0 OR mid > 1 THEN 1 ELSE 0 END) AS bad_mid,
            SUM(CASE WHEN spread < 0 THEN 1 ELSE 0 END) AS neg_spread,
            SUM(CASE WHEN best_bid > best_ask THEN 1 ELSE 0 END) AS crossed,
            COUNT(*) AS total
        FROM '{TOB}/**/*.parquet'
    """).fetchone()
    bad_bid, bad_ask, bad_mid, neg_spread, crossed, total = r

    if bad_bid: _fail(f"{bad_bid} rows with best_bid outside [0, 1]")
    else: _ok(f"100% best_bid in [0, 1] ({total:,} rows)")
    if bad_ask: _fail(f"{bad_ask} rows with best_ask outside [0, 1]")
    else: _ok(f"100% best_ask in [0, 1]")
    if bad_mid: _fail(f"{bad_mid} rows with mid outside [0, 1]")
    else: _ok(f"100% mid in [0, 1]")
    if crossed: _fail(f"{crossed} rows with best_bid > best_ask (crossed market)")
    else: _ok("0 crossed markets")
    if neg_spread: _fail(f"{neg_spread} rows with negative spread")
    else: _ok("all spreads >= 0")

    # mid = (bid + ask) / 2
    r = con.execute(f"""
        SELECT SUM(CASE WHEN ABS(mid - (best_bid + best_ask) / 2) > 1e-9 THEN 1 ELSE 0 END)
        FROM '{TOB}/**/*.parquet'
        WHERE best_bid IS NOT NULL AND best_ask IS NOT NULL
    """).fetchone()[0]
    if r:
        _fail(f"{r} rows with mid != (bid + ask) / 2")
    else:
        _ok("mid == (bid + ask) / 2 for all rows")

    # NaN check
    r = con.execute(f"""
        SELECT
            SUM(CASE WHEN isnan(best_bid) THEN 1 ELSE 0 END) AS nan_bid,
            SUM(CASE WHEN isnan(best_ask) THEN 1 ELSE 0 END) AS nan_ask,
            SUM(CASE WHEN isnan(mid) THEN 1 ELSE 0 END) AS nan_mid
        FROM '{TOB}/**/*.parquet'
    """).fetchone()
    nan_total = sum(x for x in r if x is not None)
    if nan_total:
        _fail(f"NaN values in float columns: {r}")
    else:
        _ok("no NaN in bid/ask/mid")


def level4_cross_column() -> None:
    section("L4 — cross-column / schema invariants")
    if not TOB.exists() or not MARKETS.exists():
        _warn("tob or markets.parquet missing, skipping L4")
        return
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # Every asset_id maps to a known YES or NO token
    r = con.execute(f"""
        WITH tok_map AS (
            SELECT slug, yes_token_id AS tok FROM '{MARKETS}' WHERE yes_token_id IS NOT NULL
            UNION ALL
            SELECT slug, no_token_id FROM '{MARKETS}' WHERE no_token_id IS NOT NULL
        )
        SELECT t.asset_id, COUNT(*) AS n
        FROM '{TOB}/**/*.parquet' t
        LEFT JOIN tok_map tm ON tm.tok = t.asset_id
        WHERE tm.tok IS NULL
        GROUP BY t.asset_id
        LIMIT 10
    """).fetchall()
    if r:
        _fail(f"{len(r)}+ asset_ids in tob not in markets.parquet token set: {r[:3]}")
    else:
        _ok("every tob asset_id maps to a known YES or NO token")

    # tob.slug matches the slug of the asset_id's token in markets.parquet
    r = con.execute(f"""
        WITH tok_map AS (
            SELECT slug, yes_token_id AS tok FROM '{MARKETS}' WHERE yes_token_id IS NOT NULL
            UNION ALL
            SELECT slug, no_token_id FROM '{MARKETS}' WHERE no_token_id IS NOT NULL
        )
        SELECT COUNT(*)
        FROM '{TOB}/**/*.parquet' t
        LEFT JOIN tok_map tm ON tm.tok = t.asset_id
        WHERE tm.slug IS NOT NULL AND tm.slug != t.slug
    """).fetchone()[0]
    if r:
        _fail(f"{r} rows where tob.slug != markets.slug for asset_id (routing bug)")
    else:
        _ok("tob.slug matches markets.slug for every asset_id")

    # YES+NO complement via point-in-time asof join.
    # Critical: do NOT aggregate with MAX per second — that picks non-simultaneous
    # values when the book reprices fast. ASOF last-NO-before-YES is the right join.
    r = con.execute(f"""
        WITH tm AS (SELECT slug, yes_token_id, no_token_id FROM '{MARKETS}'),
        yes_stream AS (
            SELECT t.slug, t.received_at, t.mid AS y_mid
            FROM '{TOB}/**/*.parquet' t
            INNER JOIN tm ON tm.slug = t.slug AND tm.yes_token_id = t.asset_id
        ),
        no_stream AS (
            SELECT t.slug, t.received_at, t.mid AS n_mid
            FROM '{TOB}/**/*.parquet' t
            INNER JOIN tm ON tm.slug = t.slug AND tm.no_token_id = t.asset_id
        ),
        joined AS (
            SELECT y.slug, y.received_at, y.y_mid, n.n_mid
            FROM yes_stream y
            ASOF LEFT JOIN no_stream n
              ON n.slug = y.slug AND n.received_at <= y.received_at
            WHERE n.n_mid IS NOT NULL
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(y_mid + n_mid), 6) AS avg_sum,
            ROUND(MAX(ABS(y_mid + n_mid - 1.0)), 6) AS max_abs_dev,
            SUM(CASE WHEN ABS(y_mid + n_mid - 1.0) > 0.01 THEN 1 ELSE 0 END) AS bad_10c
        FROM joined
    """).fetchone()
    n, avg_sum, max_dev, bad_10c = r
    if n == 0:
        _warn("no YES/NO pairs available — skipping complement check")
    elif abs(avg_sum - 1.0) > 1e-4:
        _fail(f"YES+NO avg sum = {avg_sum}, deviation from 1.0 > 1e-4 (n={n})")
    elif max_dev > 0.01:
        _fail(f"YES+NO max abs deviation = {max_dev}, exceeds 1c threshold (n={n}, bad_10c={bad_10c})")
    else:
        _ok(f"YES+NO complement holds: n={n:,}, avg_sum={avg_sum}, max_dev={max_dev}")


def main() -> int:
    print("POLYMARKET_BOOK — validate.py (levels 1-4)")
    print("=" * 60)
    level1_manifest_and_disk()
    level2_row_fidelity()
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

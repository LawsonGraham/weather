"""Exploratory Q — paranoid multi-level audit of all Polymarket data.

Runs levels 1-4 of the data-validation skill against:
  - polymarket_book (raw JSONL + tob parquet)
  - polymarket_prices_history (raw JSON + hourly/min1 parquet)
  - polymarket_weather (referenced for token routing)

Level 5 (fresh upstream refetch) is run as a separate inline spot-check.

Emits FAIL / WARN / OK per check. Non-zero exit on FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path.cwd()
RAW_BOOK = REPO_ROOT / "data" / "raw" / "polymarket_book"
RAW_HIST = REPO_ROOT / "data" / "raw" / "polymarket_prices_history"
TOB = REPO_ROOT / "data" / "processed" / "polymarket_book" / "tob"
HOURLY = REPO_ROOT / "data" / "processed" / "polymarket_prices_history" / "hourly"
MIN1 = REPO_ROOT / "data" / "processed" / "polymarket_prices_history" / "min1"
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


# ============================================================================
# polymarket_book audit
# ============================================================================


def audit_book_l1() -> None:
    section("[BOOK] L1 — manifest & disk")
    mp = RAW_BOOK / "MANIFEST.json"
    if not mp.exists():
        _fail("MANIFEST.json missing")
        return
    m = json.loads(mp.read_text())
    if m.get("source_name") != "polymarket_book":
        _fail(f"wrong source_name: {m.get('source_name')}")
    _ok(f"manifest parses, n_slugs_subscribed={m.get('n_slugs_subscribed')}")

    slug_dirs = [p for p in RAW_BOOK.iterdir() if p.is_dir()]
    if len(slug_dirs) != m.get("n_slugs_subscribed"):
        _warn(f"dir count {len(slug_dirs)} != manifest n_slugs {m.get('n_slugs_subscribed')}")
    else:
        _ok(f"{len(slug_dirs)} slug dirs match manifest")

    # No unknown routing dir
    unk = RAW_BOOK / "_unknown"
    if unk.exists():
        _fail(f"_unknown/ directory exists ({sum(1 for _ in unk.iterdir())} files) — routing bug")
    else:
        _ok("no _unknown/ directory")


def audit_book_l2() -> None:
    section("[BOOK] L2 — raw JSONL row count vs tob row count")
    # count raw JSONL lines
    jsonl_files = list(RAW_BOOK.rglob("*.jsonl"))
    raw_lines = 0
    parse_failures = 0
    type_counts: dict[str, int] = {}
    for jf in jsonl_files:
        with jf.open() as fh:
            for line in fh:
                raw_lines += 1
                try:
                    m = json.loads(line)
                    et = m.get("event_type", "?")
                    type_counts[et] = type_counts.get(et, 0) + 1
                except Exception:
                    parse_failures += 1
    _ok(f"raw JSONL: {raw_lines:,} lines across {len(jsonl_files)} files")
    print(f"    msg types: {type_counts}")
    if parse_failures:
        _fail(f"{parse_failures} JSONL lines failed to parse")
    else:
        _ok("100% JSONL parse success")

    # tob parquet row count
    con = duckdb.connect()
    tob_rows = con.execute(f"SELECT COUNT(*) FROM '{TOB}/**/*.parquet'").fetchone()[0]
    # book rows should contribute 1 row per msg; price_change contributes 2 (both sides)
    expected = type_counts.get("book", 0) + type_counts.get("price_change", 0) * 2
    delta = abs(tob_rows - expected)
    if delta > expected * 0.005:  # > 0.5% mismatch
        _fail(f"tob rows {tob_rows:,} vs expected {expected:,} (delta {delta:,})")
    else:
        _ok(f"tob rows {tob_rows:,} ≈ expected {expected:,} (delta {delta:,})")

    # last_trade_price + tick_size_change NOT in tob (by design)
    _ok(f"ignored (not in tob): last_trade_price={type_counts.get('last_trade_price', 0)}, "
        f"tick_size_change={type_counts.get('tick_size_change', 0)}")


def audit_book_l3() -> None:
    section("[BOOK] L3 — value-level sanity on tob parquet")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # best_bid, best_ask, mid in [0, 1]
    r = con.execute(f"""
        SELECT
            SUM(CASE WHEN best_bid < 0 OR best_bid > 1 THEN 1 ELSE 0 END) AS bad_bid,
            SUM(CASE WHEN best_ask < 0 OR best_ask > 1 THEN 1 ELSE 0 END) AS bad_ask,
            SUM(CASE WHEN mid < 0 OR mid > 1 THEN 1 ELSE 0 END) AS bad_mid,
            SUM(CASE WHEN spread < 0 THEN 1 ELSE 0 END) AS neg_spread,
            SUM(CASE WHEN spread IS NULL THEN 1 ELSE 0 END) AS null_spread,
            SUM(CASE WHEN best_bid > best_ask THEN 1 ELSE 0 END) AS crossed,
            COUNT(*) AS total
        FROM '{TOB}/**/*.parquet'
    """).fetchone()
    bad_bid, bad_ask, bad_mid, neg_spread, null_spread, crossed, total = r
    if bad_bid: _fail(f"{bad_bid} rows with best_bid outside [0,1]")
    else: _ok("100% best_bid in [0,1]")
    if bad_ask: _fail(f"{bad_ask} rows with best_ask outside [0,1]")
    else: _ok("100% best_ask in [0,1]")
    if bad_mid: _fail(f"{bad_mid} rows with mid outside [0,1]")
    else: _ok("100% mid in [0,1]")
    if crossed: _fail(f"{crossed} rows with best_bid > best_ask (crossed market)")
    else: _ok(f"0 crossed markets in {total:,} rows")
    if neg_spread: _fail(f"{neg_spread} rows with negative spread")
    else: _ok("all spreads >= 0")

    # mid = (bid + ask) / 2
    r = con.execute(f"""
        SELECT SUM(CASE WHEN ABS(mid - (best_bid + best_ask) / 2) > 1e-9 THEN 1 ELSE 0 END)
        FROM '{TOB}/**/*.parquet'
        WHERE best_bid IS NOT NULL AND best_ask IS NOT NULL
    """).fetchone()[0]
    if r: _fail(f"{r} rows with mid != (bid+ask)/2")
    else: _ok("mid == (bid+ask)/2 for all rows")

    # event_type in expected set
    r = con.execute(f"""
        SELECT event_type, COUNT(*) FROM '{TOB}/**/*.parquet' GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    expected = {"book", "price_change"}
    actual = {row[0] for row in r}
    if actual != expected:
        _fail(f"unexpected event_types: {actual - expected} missing: {expected - actual}")
    else:
        _ok(f"event_types = {{book, price_change}}: {dict(r)}")

    # NaN check on float columns
    r = con.execute(f"""
        SELECT
            SUM(CASE WHEN isnan(best_bid) THEN 1 ELSE 0 END) AS nan_bid,
            SUM(CASE WHEN isnan(best_ask) THEN 1 ELSE 0 END) AS nan_ask,
            SUM(CASE WHEN isnan(mid) THEN 1 ELSE 0 END) AS nan_mid
        FROM '{TOB}/**/*.parquet'
    """).fetchone()
    if sum(x for x in r if x is not None):
        _fail(f"NaN values in float columns: {r}")
    else:
        _ok("no NaN in bid/ask/mid")

    # Timestamp sanity: received_at within expected window
    r = con.execute(f"""
        SELECT MIN(received_at) AS t0, MAX(received_at) AS t1,
               SUM(CASE WHEN received_at < '2026-04-11 00:00:00+00:00' THEN 1 ELSE 0 END) AS too_old,
               SUM(CASE WHEN received_at > NOW() + INTERVAL '1 minute' THEN 1 ELSE 0 END) AS future
        FROM '{TOB}/**/*.parquet'
    """).fetchone()
    if r[2]: _fail(f"{r[2]} rows dated before 2026-04-11 (shouldn't happen)")
    if r[3]: _fail(f"{r[3]} rows in the future")
    else: _ok(f"timestamps in [{r[0]}, {r[1]}]")


def audit_book_l4() -> None:
    section("[BOOK] L4 — cross-column / schema invariants")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # Every asset_id in tob should map to a known (slug, yes/no) in markets.parquet
    r = con.execute(f"""
        WITH tok_map AS (
            SELECT slug, yes_token_id AS tok, 'YES' AS side FROM '{MARKETS}'
            WHERE yes_token_id IS NOT NULL
            UNION ALL
            SELECT slug, no_token_id, 'NO' FROM '{MARKETS}'
            WHERE no_token_id IS NOT NULL
        )
        SELECT t.asset_id, COUNT(*) AS n
        FROM '{TOB}/**/*.parquet' t
        LEFT JOIN tok_map tm ON tm.tok = t.asset_id
        WHERE tm.tok IS NULL
        GROUP BY t.asset_id
        LIMIT 10
    """).fetchall()
    if r:
        _fail(f"{len(r)} asset_ids in tob not in markets.parquet token set: {r[:3]}")
    else:
        _ok("every tob asset_id maps to a known YES or NO token")

    # Every tob slug matches the asset_id's slug in markets.parquet
    r = con.execute(f"""
        WITH tok_map AS (
            SELECT slug, yes_token_id AS tok FROM '{MARKETS}' WHERE yes_token_id IS NOT NULL
            UNION ALL
            SELECT slug, no_token_id, FROM '{MARKETS}' WHERE no_token_id IS NOT NULL
        )
        SELECT COUNT(*)
        FROM '{TOB}/**/*.parquet' t
        LEFT JOIN tok_map tm ON tm.tok = t.asset_id
        WHERE tm.slug != t.slug
    """).fetchone()[0]
    if r:
        _fail(f"{r} rows where tob.slug != markets.slug for the same asset_id (routing bug)")
    else:
        _ok("tob.slug matches markets.slug for every asset_id (routing correct)")

    # book events should have n_bid_levels / n_ask_levels populated; price_change should have NULL
    r = con.execute(f"""
        SELECT event_type,
               SUM(CASE WHEN n_bid_levels IS NULL THEN 1 ELSE 0 END) AS nl_null,
               SUM(CASE WHEN n_bid_levels IS NOT NULL THEN 1 ELSE 0 END) AS nl_filled,
               COUNT(*) AS total
        FROM '{TOB}/**/*.parquet' GROUP BY 1 ORDER BY 1
    """).fetchall()
    for et, nl_null, nl_filled, total in r:
        if et == "book":
            if nl_null > 0:
                _warn(f"book events with null n_bid_levels: {nl_null}/{total}")
            else:
                _ok(f"book events all have n_bid_levels filled ({total:,})")
        elif et == "price_change":
            if nl_filled > 0:
                _warn(f"price_change with non-null n_bid_levels: {nl_filled}/{total}")
            else:
                _ok(f"price_change events all have null n_bid_levels (as designed)")

    # YES + NO complement: for a given slug, same second, YES_mid + NO_mid ≈ 1.0
    # Sample a few slugs and check.
    r = con.execute(f"""
        WITH tm AS (SELECT slug, yes_token_id, no_token_id FROM '{MARKETS}'),
        tagged AS (
            SELECT t.slug, date_trunc('second', t.received_at) AS sec,
                   CASE WHEN t.asset_id = tm.yes_token_id THEN 'YES' ELSE 'NO' END AS side,
                   t.mid
            FROM '{TOB}/**/*.parquet' t
            INNER JOIN tm ON tm.slug = t.slug AND (tm.yes_token_id = t.asset_id OR tm.no_token_id = t.asset_id)
        ),
        per_sec AS (
            SELECT slug, sec,
                   MAX(CASE WHEN side='YES' THEN mid END) AS y,
                   MAX(CASE WHEN side='NO' THEN mid END) AS n
            FROM tagged GROUP BY 1, 2
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(y + n), 4) AS avg_sum,
            ROUND(MIN(y + n), 4) AS min_sum,
            ROUND(MAX(y + n), 4) AS max_sum,
            SUM(CASE WHEN ABS((y + n) - 1.0) > 0.1 THEN 1 ELSE 0 END) AS offenders
        FROM per_sec
        WHERE y IS NOT NULL AND n IS NOT NULL
    """).fetchone()
    n, avg_sum, min_sum, max_sum, offenders = r
    if avg_sum is None:
        _warn("no YES/NO per-second joins — unable to check complement")
    elif abs(avg_sum - 1.0) > 0.02:
        _fail(f"YES+NO mid sum avg {avg_sum} is far from 1.0 (n={n})")
    elif offenders > n * 0.01:
        _warn(f"{offenders}/{n} per-sec YES+NO sums deviate by >0.1 from 1.0")
    else:
        _ok(f"YES+NO mid sum avg={avg_sum} (n={n}, min={min_sum}, max={max_sum}, offenders={offenders})")


# ============================================================================
# polymarket_prices_history audit
# ============================================================================


def audit_hist_l1() -> None:
    section("[PRICES_HISTORY] L1 — manifest & disk")
    mp = RAW_HIST / "MANIFEST.json"
    if not mp.exists():
        _fail("MANIFEST.json missing")
        return
    m = json.loads(mp.read_text())
    d = m.get("download", {})
    if d.get("status") != "ok":
        _fail(f"download status = {d.get('status')}")
    else:
        _ok(f"status=ok, n_done={d['n_slugs_done']}/{d['n_slugs_planned']}, "
            f"empty={d['n_slugs_empty']}, failed={d['n_slugs_failed']}")

    json_files = list(RAW_HIST.glob("*.json"))
    if len(json_files) != d["n_slugs_done"] + d["n_slugs_failed"]:
        _warn(f"json file count {len(json_files)} vs n_done+failed {d['n_slugs_done'] + d['n_slugs_failed']}")
    else:
        _ok(f"{len(json_files)} json files present")


def audit_hist_l2() -> None:
    section("[PRICES_HISTORY] L2 — raw JSON row count vs parquet row count")
    # Sum history_max_h60 and history_1d_min1 across all raw JSONs
    json_files = list(RAW_HIST.glob("*.json"))
    n_h60_total = 0
    n_min1_total = 0
    bad_json = 0
    for jf in json_files:
        try:
            d = json.loads(jf.read_text())
        except Exception:
            bad_json += 1
            continue
        n_h60_total += len(d.get("history_max_h60", []) or [])
        n_min1_total += len(d.get("history_1d_min1", []) or [])
    if bad_json:
        _fail(f"{bad_json} JSONs failed to parse")
    else:
        _ok(f"{len(json_files)} JSONs parsed; raw h60 rows = {n_h60_total:,}, min1 = {n_min1_total:,}")

    con = duckdb.connect()
    h = con.execute(f"SELECT COUNT(*) FROM '{HOURLY}/**/*.parquet'").fetchone()[0]
    m = con.execute(f"SELECT COUNT(*) FROM '{MIN1}/**/*.parquet'").fetchone()[0]
    if h != n_h60_total:
        _fail(f"hourly parquet {h:,} != raw h60 total {n_h60_total:,} (delta {h - n_h60_total:+})")
    else:
        _ok(f"hourly: {h:,} rows match raw")
    if m != n_min1_total:
        _fail(f"min1 parquet {m:,} != raw min1 total {n_min1_total:,} (delta {m - n_min1_total:+})")
    else:
        _ok(f"min1: {m:,} rows match raw")


def audit_hist_l3() -> None:
    section("[PRICES_HISTORY] L3 — value-level sanity on parquet")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    for variant, path in [("hourly", HOURLY), ("min1", MIN1)]:
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
        if bad_p: _fail(f"{variant}: {bad_p} p_yes outside [0,1]")
        if nan_p: _fail(f"{variant}: {nan_p} NaN p_yes")
        if null_p: _fail(f"{variant}: {null_p} null p_yes")
        if null_slug: _fail(f"{variant}: {null_slug} null slug")
        if null_ts: _fail(f"{variant}: {null_ts} null timestamp")
        if not any([bad_p, nan_p, null_p, null_slug, null_ts]):
            _ok(f"{variant}: {total:,} rows, all value checks pass")

        # Duplicate (slug, timestamp) check
        r = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT slug, timestamp, COUNT(*) AS n
                FROM '{path}/**/*.parquet'
                GROUP BY 1, 2 HAVING n > 1
            )
        """).fetchone()[0]
        if r:
            _warn(f"{variant}: {r} duplicate (slug, timestamp) pairs — known upstream issue, dedup at query time")
        else:
            _ok(f"{variant}: 0 duplicate (slug, timestamp)")


def audit_hist_l4() -> None:
    section("[PRICES_HISTORY] L4 — cross-column / schema invariants")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # condition_id + yes_token_id should match markets.parquet
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


# ============================================================================
# Cross-source consistency
# ============================================================================


def audit_cross() -> None:
    section("[CROSS] tob mid vs prices_history min1 p_yes during overlap window")
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # For the overlap window (tob starts 19:24 UTC, min1 has data for active slugs up to ~19:16 UTC)
    # there's a ~8min gap. Fresher data was pulled at transform time. Let's do a weaker check:
    # For each slug's LATEST min1 timestamp, find the EARLIEST tob timestamp for that slug's YES token,
    # and check the prices aren't wildly different.
    r = con.execute(f"""
        WITH min1_latest AS (
            SELECT DISTINCT ON (slug) slug, timestamp AS min1_ts, p_yes AS min1_p
            FROM '{MIN1}/**/*.parquet'
            ORDER BY slug, timestamp DESC
        ),
        tm AS (SELECT slug, yes_token_id FROM '{MARKETS}'),
        tob_earliest AS (
            SELECT DISTINCT ON (t.slug) t.slug, t.received_at AS tob_ts, t.mid AS tob_mid
            FROM '{TOB}/**/*.parquet' t
            INNER JOIN tm ON tm.slug = t.slug AND tm.yes_token_id = t.asset_id
            ORDER BY t.slug, t.received_at ASC
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(ABS(ml.min1_p - te.tob_mid)), 4) AS avg_delta,
            ROUND(MAX(ABS(ml.min1_p - te.tob_mid)), 4) AS max_delta,
            SUM(CASE WHEN ABS(ml.min1_p - te.tob_mid) > 0.2 THEN 1 ELSE 0 END) AS wild
        FROM min1_latest ml
        INNER JOIN tob_earliest te ON te.slug = ml.slug
    """).fetchone()
    n, avg_delta, max_delta, wild = r
    if avg_delta is None:
        _warn("no overlap between min1 and tob")
    elif avg_delta > 0.05:
        _warn(f"avg tob_mid - min1_p delta = {avg_delta} (n={n}, max={max_delta}, wild={wild}); possible drift or time gap")
    else:
        _ok(f"tob-vs-min1 price agreement: avg_delta={avg_delta}, max={max_delta}, n={n}, wild_count={wild}")


# ============================================================================
# main
# ============================================================================


def main() -> int:
    print("POLYMARKET DATA AUDIT — levels 1-4")
    print("=" * 70)

    audit_book_l1()
    audit_book_l2()
    audit_book_l3()
    audit_book_l4()

    audit_hist_l1()
    audit_hist_l2()
    audit_hist_l3()
    audit_hist_l4()

    audit_cross()

    section("SUMMARY")
    print(f"  FAILS: {len(FAILS)}")
    print(f"  WARNS: {len(WARNS)}")
    if FAILS:
        print("\n  FAIL list:")
        for f in FAILS:
            print(f"    * {f}")
    if WARNS:
        print("\n  WARN list:")
        for w in WARNS:
            print(f"    * {w}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

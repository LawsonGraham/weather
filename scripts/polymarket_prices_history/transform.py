"""Transform polymarket_prices_history raw JSONs into partitioned parquet.

Reads every ``data/raw/polymarket_prices_history/<slug>.json`` and emits
two parquet datasets:

    data/processed/polymarket_prices_history/hourly/year=YYYY/month=MM/part-0.parquet
        — hourly fidelity (interval=max), full lifetime per slug, all rows
    data/processed/polymarket_prices_history/min1/year=YYYY/month=MM/part-0.parquet
        — 1-minute fidelity (interval=1d), only available for OPEN markets
          at fetch time

Schema (both):
    timestamp        TIMESTAMP WITH TIME ZONE
    slug             VARCHAR
    condition_id     VARCHAR
    yes_token_id     VARCHAR
    p_yes            DOUBLE
    closed_at_fetch  BOOLEAN
    fetched_at       TIMESTAMP WITH TIME ZONE

Idempotent: re-running overwrites the parquet partitions.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

SOURCE_NAME = "polymarket_prices_history"
SCRIPT_VERSION = 1

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
PROC_DIR = REPO_ROOT / "data" / "processed" / SOURCE_NAME

log = logging.getLogger(SOURCE_NAME)


def _setup_logging() -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PROC_DIR / "transform.log"
    fmt = "%(asctime)sZ [%(levelname)s] %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")
    file_h = logging.FileHandler(log_path, mode="a")
    file_h.setFormatter(formatter)
    stream_h = logging.StreamHandler(sys.stdout)
    stream_h.setFormatter(formatter)
    log.handlers.clear()
    log.addHandler(file_h)
    log.addHandler(stream_h)
    log.setLevel(logging.INFO)


def collect_rows(history_key: str) -> list[dict]:
    """Walk every JSON in RAW_DIR and accumulate rows for one history series."""
    rows: list[dict] = []
    json_files = sorted(RAW_DIR.glob("*.json"))
    log.info(f"  scanning {len(json_files)} raw JSONs for `{history_key}`")
    for fp in json_files:
        try:
            d = json.loads(fp.read_text())
        except Exception as e:
            log.warning(f"    {fp.name}: parse error {e}")
            continue
        slug = d.get("slug")
        cond = d.get("condition_id")
        token = d.get("yes_token_id")
        closed = bool(d.get("closed", False))
        fetched_at = d.get("fetched_at")
        for pt in d.get(history_key, []) or []:
            t = pt.get("t")
            p = pt.get("p")
            if t is None or p is None:
                continue
            rows.append({
                "timestamp_unix": int(t),
                "slug": slug,
                "condition_id": cond,
                "yes_token_id": token,
                "p_yes": float(p),
                "closed_at_fetch": closed,
                "fetched_at": fetched_at,
            })
    return rows


def write_partitioned(rows: list[dict], variant: str) -> None:
    """Write rows to data/processed/polymarket_prices_history/<variant>/year=/month=/part-0.parquet."""
    if not rows:
        log.info(f"  {variant}: 0 rows, nothing to write")
        return
    out_dir = PROC_DIR / variant
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    # Stage rows in a temp duckdb table then write partitioned parquet
    con.register("rows_df", _to_arrow(rows))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE staged AS
        SELECT
            to_timestamp(timestamp_unix) AS timestamp,
            slug, condition_id, yes_token_id, p_yes,
            closed_at_fetch, CAST(fetched_at AS TIMESTAMP WITH TIME ZONE) AS fetched_at,
            EXTRACT(year FROM to_timestamp(timestamp_unix))::INT AS year,
            EXTRACT(month FROM to_timestamp(timestamp_unix))::INT AS month
        FROM rows_df
    """)
    n = con.execute("SELECT COUNT(*) FROM staged").fetchone()[0]
    log.info(f"  {variant}: staged {n:,} rows")
    con.execute(f"""
        COPY (SELECT * FROM staged ORDER BY slug, timestamp)
        TO '{out_dir.as_posix()}'
        (FORMAT PARQUET, PARTITION_BY (year, month), OVERWRITE_OR_IGNORE)
    """)
    log.info(f"  {variant}: wrote partitioned parquet to {out_dir.relative_to(REPO_ROOT)}")


def _to_arrow(rows: list[dict]):
    import pyarrow as pa
    return pa.Table.from_pylist(rows)


def main() -> int:
    _setup_logging()
    log.info(f"transform {SOURCE_NAME} starting")
    log.info(f"  raw dir : {RAW_DIR}")
    log.info(f"  proc dir: {PROC_DIR}")

    # Hourly variant — present on every slug
    log.info("--- hourly (interval=max fidelity=60) ---")
    h_rows = collect_rows("history_max_h60")
    write_partitioned(h_rows, "hourly")

    # 1-min variant — only for open markets at fetch time
    log.info("--- min1 (interval=1d fidelity=1) ---")
    m_rows = collect_rows("history_1d_min1")
    write_partitioned(m_rows, "min1")

    log.info(f"transform done: hourly={len(h_rows):,}  min1={len(m_rows):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

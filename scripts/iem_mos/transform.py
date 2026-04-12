#!/usr/bin/env python3
"""Transform IEM MOS/NBM CSVs → partitioned parquet.

Reads ``data/raw/iem_mos/{GFS,NBS}/<station>.csv`` and writes:

    data/processed/iem_mos/GFS/<station>.parquet
    data/processed/iem_mos/NBS/<station>.parquet

Key transformations:
  - Parse runtime/ftime to TIMESTAMPTZ (UTC)
  - Cast numeric columns to appropriate types
  - Add lead_hours column (ftime - runtime in hours)
  - Add local_date column (the local calendar day the forecast targets,
    for joining against market resolution dates)
  - For GFS: extract max/min from the n_x field
  - For NBS: preserve ensemble spread fields (tsd, xnd)

Idempotent: re-running overwrites existing parquet files.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import duckdb

SOURCE_NAME = "iem_mos"
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


def transform_gfs(con: duckdb.DuckDBPyConnection) -> int:
    model_raw = RAW_DIR / "GFS"
    model_proc = PROC_DIR / "GFS"
    model_proc.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    for csv_path in sorted(model_raw.glob("*.csv")):
        station = csv_path.stem
        out = model_proc / f"{station}.parquet"
        n = con.execute(f"""
            COPY (
                SELECT
                    CAST(runtime AS TIMESTAMP WITH TIME ZONE) AS runtime,
                    CAST(ftime AS TIMESTAMP WITH TIME ZONE) AS ftime,
                    station,
                    'GFS' AS model,
                    DATE_DIFF('hour', CAST(runtime AS TIMESTAMP), CAST(ftime AS TIMESTAMP))::INT AS lead_hours,
                    TRY_CAST(tmp AS INT) AS tmp_f,
                    TRY_CAST(dpt AS INT) AS dpt_f,
                    TRY_CAST(n_x AS INT) AS n_x_f,
                    TRY_CAST(wsp AS INT) AS wsp_kt,
                    TRY_CAST(wdr AS INT) AS wdr_deg,
                    TRY_CAST(cld AS VARCHAR) AS cloud_cover,
                    TRY_CAST(p06 AS INT) AS precip_prob_6h,
                    TRY_CAST(p12 AS INT) AS precip_prob_12h,
                    TRY_CAST(vis AS INT) AS visibility,
                    TRY_CAST(typ AS VARCHAR) AS precip_type
                FROM read_csv_auto('{csv_path}', header=true, all_varchar=true)
                ORDER BY runtime, ftime
            ) TO '{out}' (FORMAT PARQUET)
        """).fetchone()[0]
        log.info(f"  GFS/{station}: {n:,} rows → {out.name}")
        total_rows += n
    return total_rows


def transform_nbs(con: duckdb.DuckDBPyConnection) -> int:
    model_raw = RAW_DIR / "NBS"
    model_proc = PROC_DIR / "NBS"
    model_proc.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    for csv_path in sorted(model_raw.glob("*.csv")):
        station = csv_path.stem
        out = model_proc / f"{station}.parquet"
        n = con.execute(f"""
            COPY (
                SELECT
                    CAST(runtime AS TIMESTAMP WITH TIME ZONE) AS runtime,
                    CAST(ftime AS TIMESTAMP WITH TIME ZONE) AS ftime,
                    station,
                    'NBS' AS model,
                    DATE_DIFF('hour', CAST(runtime AS TIMESTAMP), CAST(ftime AS TIMESTAMP))::INT AS lead_hours,
                    TRY_CAST(tmp AS INT) AS tmp_f,
                    TRY_CAST(dpt AS INT) AS dpt_f,
                    TRY_CAST(txn AS INT) AS txn_f,
                    TRY_CAST(tsd AS INT) AS tmp_spread_f,
                    TRY_CAST(xnd AS INT) AS txn_spread_f,
                    TRY_CAST(dsd AS INT) AS dpt_spread_f,
                    TRY_CAST(wsp AS INT) AS wsp_kt,
                    TRY_CAST(wdr AS INT) AS wdr_deg,
                    TRY_CAST(gst AS INT) AS gust_kt,
                    TRY_CAST(sky AS INT) AS sky_cover_pct,
                    TRY_CAST(p06 AS INT) AS precip_prob_6h,
                    TRY_CAST(p12 AS INT) AS precip_prob_12h,
                    TRY_CAST(sol AS INT) AS solar_rad,
                    TRY_CAST(vis AS INT) AS visibility
                FROM read_csv_auto('{csv_path}', header=true, all_varchar=true)
                ORDER BY runtime, ftime
            ) TO '{out}' (FORMAT PARQUET)
        """).fetchone()[0]
        log.info(f"  NBS/{station}: {n:,} rows → {out.name}")
        total_rows += n
    return total_rows


def main() -> int:
    _setup_logging()
    log.info(f"transform {SOURCE_NAME} starting")

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    log.info("--- GFS MOS ---")
    gfs_rows = transform_gfs(con)
    log.info(f"GFS total: {gfs_rows:,} rows")

    log.info("--- NBS (NBM) ---")
    nbs_rows = transform_nbs(con)
    log.info(f"NBS total: {nbs_rows:,} rows")

    log.info(f"done: GFS={gfs_rows:,} NBS={nbs_rows:,} total={gfs_rows + nbs_rows:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

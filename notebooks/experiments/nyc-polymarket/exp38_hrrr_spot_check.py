"""Experiment 38 — Single-day HRRR vs METAR spot check.

A 24-row HRRR verification parquet was found at /private/tmp/ from an
earlier verification run. It contains one HRRR cycle for KLGA on
2026-01-15. Use it to demonstrate the comparison shape and check
whether HRRR has the same universal upward bias the market does.

This is a SPOT CHECK on n=1, not a real analysis. The full HRRR
backfill is still running (currently ~96%); when it completes, exp30
will run the rigorous version across all 55 days.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

HRRR_VERIFY = "/private/tmp/hrrr_verify_L6_run1_KLGA_h.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    print("=== HRRR vs METAR spot check (single 24-hr window) ===")
    print(con.execute(f"""
        WITH hrrr AS (
            SELECT
                CAST((valid_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                ROUND(t2m_heightAboveGround_2 * 9.0/5.0 - 459.67, 1) AS t_f
            FROM read_parquet('{HRRR_VERIFY}')
        ),
        hrrr_day AS (
            SELECT local_date, ROUND(MAX(t_f), 1) AS hrrr_max_f, COUNT(*) AS n_hrrr
            FROM hrrr GROUP BY local_date
        ),
        metar_day AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   ROUND(MAX(GREATEST(COALESCE(tmpf, -999),
                                COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999))), 1) AS metar_max_f
            FROM read_parquet('{METAR}') WHERE station='LGA'
            GROUP BY 1
        )
        SELECT
            h.local_date,
            h.hrrr_max_f,
            m.metar_max_f,
            ROUND(m.metar_max_f - h.hrrr_max_f, 1) AS bias_f,
            h.n_hrrr AS n_hrrr_hours
        FROM hrrr_day h
        LEFT JOIN metar_day m USING (local_date)
        ORDER BY h.local_date
    """).df())

    print("\n   note: 2026-01-15 has only 13 of 24 hours of HRRR coverage,")
    print("         and the next day's HRRR window is overnight cooling — not")
    print("         covering the actual peak. This is a tiny anecdote, not a")
    print("         rigorous result. The full backfill will enable exp30.")


if __name__ == "__main__":
    main()

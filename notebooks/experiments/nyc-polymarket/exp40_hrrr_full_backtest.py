"""Experiment 40 — HRRR vs METAR realized: full backtest at last.

After 28 iterations of waiting on the HRRR backfill, the full data is
available. 2701 hourly cycles × 113 days at KLGA. Each cycle is a
+6h forecast.

Question: does HRRR have the same +4°F upward bias the Polymarket
market has? If yes, HRRR is the bias source — Polymarket consumes a
HRRR-derived forecast (via weather apps / human pattern-matching) and
the bias propagates downstream.

Method: for each local NY day in the HRRR-covered window:
    1. Take MAX(t2m) across all HRRR cycles whose valid_time is in
       that day's local 24-hour window. Convert Kelvin to Fahrenheit.
    2. Compare to METAR realized day_max.
    3. Compute the bias = (METAR - HRRR_max).
    4. Report distribution and check correlation with the Polymarket
       favorite gap from exp12.

Critical claim if HRRR_bias matches market_bias: the price source IS
HRRR, the consumption pattern is human (per exp28). Strategy D
becomes "fade HRRR's day-max under-prediction" with a clean
mechanistic explanation.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

HRRR_HOURLY = "data/raw/hrrr/KLGA/hourly.parquet"
HRRR_SUBHOURLY = "data/raw/hrrr/KLGA/subhourly.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"
MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # METAR truth
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_daily AS
        WITH m AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   GREATEST(COALESCE(tmpf, -999),
                            COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999)) AS te
            FROM '{METAR}' WHERE station='LGA'
        )
        SELECT local_date, ROUND(MAX(te))::INT AS metar_day_max
        FROM m WHERE te > -900 GROUP BY 1
    """)

    # HRRR daily max — take MAX(t2m) across all cycles whose valid_time is on local day D
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW hrrr_daily AS
        SELECT
            CAST((valid_time AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
            ROUND(MAX(t2m_heightAboveGround_2 * 9.0/5.0 - 459.67), 1) AS hrrr_day_max_f,
            COUNT(*) AS n_cycles
        FROM '{HRRR_HOURLY}'
        GROUP BY local_date
    """)

    print("\n=== HRRR coverage ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_days,
            MIN(local_date) AS first_day,
            MAX(local_date) AS last_day,
            ROUND(AVG(n_cycles), 1) AS avg_cycles_per_day
        FROM hrrr_daily
    """).df())

    print("\n=== HRRR vs METAR DAILY MAX BIAS (METAR - HRRR) ===")
    print("    Positive = HRRR under-predicted the actual day max.")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(metar_day_max - hrrr_day_max_f), 2) AS mean_bias,
            ROUND(STDDEV(metar_day_max - hrrr_day_max_f), 2) AS std_bias,
            ROUND(QUANTILE_CONT(metar_day_max - hrrr_day_max_f, 0.5), 2) AS median_bias,
            COUNT(*) FILTER (WHERE metar_day_max > hrrr_day_max_f) AS n_under,
            COUNT(*) FILTER (WHERE metar_day_max < hrrr_day_max_f) AS n_over,
            COUNT(*) FILTER (WHERE metar_day_max = hrrr_day_max_f) AS n_equal
        FROM hrrr_daily h JOIN metar_daily m USING (local_date)
        WHERE m.metar_day_max IS NOT NULL AND h.hrrr_day_max_f IS NOT NULL
    """).df())

    # Now correlate with Polymarket bias from exp12
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE range_12 AS
        WITH r AS (
            SELECT slug, group_item_title AS strike,
                   CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
                   CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
                   CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
            FROM '{MARKETS}'
            WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
              AND group_item_title NOT ILIKE '%or %'
        )
        SELECT r.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=r.slug
               AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p12_mid
        FROM r
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW poly_daily AS
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p12_mid) AS poly_fav_lo
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT f.local_day AS local_date, f.poly_fav_lo
        FROM fav f
    """)

    print("\n=== TRIPLE COMPARISON: HRRR vs METAR vs Polymarket favorite ===")
    print(con.execute("""
        SELECT
            h.local_date,
            ROUND(h.hrrr_day_max_f, 0) AS hrrr_max,
            m.metar_day_max AS metar_max,
            p.poly_fav_lo AS poly_fav_lo,
            (m.metar_day_max - h.hrrr_day_max_f) AS hrrr_bias,
            (m.metar_day_max - p.poly_fav_lo) AS poly_bias,
            (h.hrrr_day_max_f - p.poly_fav_lo) AS hrrr_minus_poly
        FROM hrrr_daily h
        JOIN metar_daily m USING (local_date)
        JOIN poly_daily p USING (local_date)
        ORDER BY h.local_date
        LIMIT 30
    """).df())

    print("\n=== AGGREGATE BIAS COMPARISON (days where all 3 sources are present) ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_days,
            ROUND(AVG(m.metar_day_max - h.hrrr_day_max_f), 2) AS mean_hrrr_bias,
            ROUND(AVG(m.metar_day_max - p.poly_fav_lo), 2) AS mean_poly_bias,
            ROUND(CORR(m.metar_day_max - h.hrrr_day_max_f, m.metar_day_max - p.poly_fav_lo), 3) AS corr_hrrr_poly_bias
        FROM hrrr_daily h
        JOIN metar_daily m USING (local_date)
        JOIN poly_daily p USING (local_date)
    """).df())


if __name__ == "__main__":
    main()

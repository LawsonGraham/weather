"""Experiment 34/35 — Saturday deep-dive + morning re-open price pattern.

Two questions:

A. Saturday: exp31 found Strategy D has 75% hit rate on Saturdays
   (n=4) and 100% upward direction on all 7 Saturdays in the sample.
   Is this real or small-sample noise? Per-Saturday detail.

B. Morning re-open: exp18 found 08-10 EDT (12-14 UTC) is the WORST
   entry hour for Strategy D. Hit rates: 17%, 19% vs 31% at 12 EDT.
   Why? Hypothesis: the morning sees the favorite UPDATE between
   06 EDT (overnight close) and 10 EDT (US morning open), and the
   update is in the wrong direction. Can we identify a fadeable
   morning-reopen price drop?
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_daily AS
        WITH m AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   GREATEST(COALESCE(tmpf, -999),
                            COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999)) AS te
            FROM '{METAR}' WHERE station='LGA'
        )
        SELECT local_date, ROUND(MAX(te))::INT AS day_max_whole
        FROM m WHERE te > -900 GROUP BY 1
    """)
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

    print("\n=== A. EVERY SATURDAY IN THE SAMPLE ===")
    print(con.execute("""
        WITH fav AS (
            SELECT local_day,
                   arg_max(strike, p12_mid) AS fav_strike,
                   arg_max(lo_f, p12_mid) AS fav_lo,
                   max(p12_mid) AS fav_p
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT
            f.local_day,
            f.fav_strike,
            ROUND(f.fav_p, 3) AS fav_p,
            md.day_max_whole AS dmax,
            (md.day_max_whole - f.fav_lo) AS gap,
            CASE WHEN md.day_max_whole BETWEEN f.fav_lo AND f.fav_lo+1 THEN 'fav HIT'
                 WHEN md.day_max_whole BETWEEN f.fav_lo+2 AND f.fav_lo+3 THEN '+2 HIT (Strategy D win)'
                 ELSE CONCAT('miss by +', md.day_max_whole - f.fav_lo, 'F')
            END AS outcome
        FROM fav f JOIN metar_daily md ON md.local_date = f.local_day
        WHERE EXTRACT(DOW FROM f.local_day) = 6
        ORDER BY f.local_day
    """).df())

    # Re-check: original DoW 6 = Saturday in DuckDB (same as Postgres convention)
    print("\n=== B. STRATEGY D AT 08 EDT (WORST HOUR) ===")
    # 08 EDT = 12 UTC
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE range_08 AS
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
               AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '12 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_at_08
        FROM r
    """)
    print(con.execute(f"""
        WITH fav_08 AS (
            SELECT local_day, arg_max(lo_f, p_at_08) AS fav_lo
            FROM range_08 WHERE p_at_08 IS NOT NULL GROUP BY 1
        ),
        d AS (
            SELECT f.local_day, r.p_at_08, r.lo_f, r.hi_f, md.day_max_whole,
                   CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y
            FROM fav_08 f
            JOIN range_08 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE r.p_at_08 IS NOT NULL AND r.p_at_08 >= 0.02
        )
        SELECT COUNT(*) AS n, ROUND(AVG(p_at_08), 3) AS avg_p,
               ROUND(AVG(y), 3) AS hit_rate,
               ROUND(SUM(y/(p_at_08*(1+{FEE})) - 1), 2) AS cum_pnl
        FROM d
    """).df())

    print("\n=== C. FAVORITE PRICE 06 EDT vs 10 EDT vs 12 EDT ===")
    print("    Does the favorite re-rate during the morning open?")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE p_six AS
        SELECT nr.slug, nr.lo_f, nr.local_day,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '10 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_at_06,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '14 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_at_10,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_at_12
        FROM (SELECT slug, lo_f, local_day FROM range_12) nr
    """)
    print(con.execute("""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p_at_06) AS fav_lo
            FROM p_six WHERE p_at_06 IS NOT NULL GROUP BY 1
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(ps.p_at_06), 3) AS mean_fav_p_06,
            ROUND(AVG(ps.p_at_10), 3) AS mean_fav_p_10,
            ROUND(AVG(ps.p_at_12), 3) AS mean_fav_p_12,
            ROUND(AVG(ps.p_at_10 - ps.p_at_06), 3) AS mean_drift_06_to_10,
            ROUND(AVG(ps.p_at_12 - ps.p_at_10), 3) AS mean_drift_10_to_12
        FROM fav f
        JOIN p_six ps ON ps.local_day = f.local_day AND ps.lo_f = f.fav_lo
        WHERE ps.p_at_06 IS NOT NULL AND ps.p_at_10 IS NOT NULL AND ps.p_at_12 IS NOT NULL
    """).df())


if __name__ == "__main__":
    main()

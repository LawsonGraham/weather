"""Experiment 32 — Favorite price drift through the day.

For each day, compute the favorite range strike's yes_price at four
snapshots: 12 / 14 / 16 / 18 EDT. How much does the favorite's price
drift through the afternoon?

If the price stays high (favorite is "locked in" early), the market is
not actively repricing — Strategy D's edge is from market staleness.
If the price drops dramatically (favorite gets faded), the market is
actively repricing — Strategy D may be capturing real new info.

Bonus: does drift magnitude correlate with hit rate? Days where the
favorite drops 30¢+ from 12 EDT to 18 EDT may be days where Strategy
D wins big (the +2 bucket gets the new probability mass).
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

    # For each fav, get prices at 4 snapshots
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE fav_drift AS
        WITH fav AS (
            SELECT local_day,
                   arg_max(slug, p12_mid) AS fav_slug,
                   arg_max(lo_f, p12_mid) AS fav_lo,
                   arg_max(hi_f, p12_mid) AS fav_hi,
                   max(p12_mid) AS p_12
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT
            f.*, md.day_max_whole,
            CASE WHEN md.day_max_whole BETWEEN f.fav_lo AND f.fav_hi THEN 1 ELSE 0 END AS fav_y,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug = f.fav_slug
               AND p.timestamp <= (CAST(f.local_day AS TIMESTAMPTZ) + INTERVAL '18 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_14edt,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug = f.fav_slug
               AND p.timestamp <= (CAST(f.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_16edt,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug = f.fav_slug
               AND p.timestamp <= (CAST(f.local_day AS TIMESTAMPTZ) + INTERVAL '22 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_18edt
        FROM fav f
        JOIN metar_daily md ON md.local_date = f.local_day
    """)

    print("\n=== FAVORITE PRICE DRIFT THROUGH THE DAY ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(p_12), 3)              AS mean_p_12,
            ROUND(AVG(p_14edt), 3)           AS mean_p_14,
            ROUND(AVG(p_16edt), 3)           AS mean_p_16,
            ROUND(AVG(p_18edt), 3)           AS mean_p_18,
            ROUND(AVG(p_18edt - p_12), 3)    AS mean_drift,
            ROUND(STDDEV(p_18edt - p_12), 3) AS std_drift
        FROM fav_drift
        WHERE p_18edt IS NOT NULL
    """).df())

    print("\n=== DRIFT vs FAV HIT RATE — does big drop predict miss? ===")
    print(con.execute("""
        SELECT
            CASE
                WHEN p_18edt - p_12 > 0.30 THEN '1: drift > +30c (rallied)'
                WHEN p_18edt - p_12 > 0.05 THEN '2: drift +5 to +30c'
                WHEN p_18edt - p_12 > -0.05 THEN '3: drift -5 to +5c (flat)'
                WHEN p_18edt - p_12 > -0.30 THEN '4: drift -5 to -30c'
                ELSE '5: drift < -30c (collapsed)'
            END AS band,
            COUNT(*) AS n,
            ROUND(AVG(fav_y), 3) AS fav_hit_rate,
            ROUND(AVG(p_12), 3) AS avg_start_price,
            ROUND(AVG(p_18edt), 3) AS avg_end_price
        FROM fav_drift WHERE p_18edt IS NOT NULL
        GROUP BY band ORDER BY band
    """).df())

    print("\n=== TOP 15 BIGGEST FAVORITE DROPS ===")
    print(con.execute("""
        SELECT fav_drift.local_day,
               fav_drift.fav_lo,
               fav_drift.day_max_whole,
               ROUND(fav_drift.p_12, 3)    AS p_12,
               ROUND(fav_drift.p_18edt, 3) AS p_18,
               ROUND(fav_drift.p_18edt - fav_drift.p_12, 3) AS drift,
               fav_drift.fav_y AS hit
        FROM fav_drift WHERE p_18edt IS NOT NULL
        ORDER BY drift ASC
        LIMIT 15
    """).df())


if __name__ == "__main__":
    main()

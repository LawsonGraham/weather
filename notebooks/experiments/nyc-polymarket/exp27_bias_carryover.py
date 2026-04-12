"""Experiment 27 — Multi-day carryover of the upward bias.

Universal upward bias (+4°F mean) is a daily property. Question: is it
PERSISTENT across days? If yesterday had a big upward miss, is today
more likely to also have one?

If carryover is strong, a simple filter ("only trade Strategy D when
yesterday was also upward-biased") could tighten the signal without
needing METAR regime filtering.

Method:
    1. Compute signed_gap = day_max - fav_lo for every day.
    2. Look at the correlation between day_n gap and day_n-1 gap.
    3. Test Strategy D conditioned on yesterday's gap sign.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02


def build(con: duckdb.DuckDBPyConnection) -> None:
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
    con.execute("""
        CREATE OR REPLACE TEMP TABLE daily_bias AS
        WITH favs AS (
            SELECT local_day,
                   arg_max(lo_f, p12_mid) AS fav_lo,
                   arg_max(hi_f, p12_mid) AS fav_hi,
                   max(p12_mid) AS fav_p
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT f.local_day, f.fav_lo, f.fav_hi, f.fav_p, md.day_max_whole,
               (md.day_max_whole - f.fav_lo) AS signed_gap,
               CASE WHEN md.day_max_whole BETWEEN f.fav_lo AND f.fav_hi THEN 1 ELSE 0 END AS fav_hit
        FROM favs f
        JOIN metar_daily md ON md.local_date = f.local_day
        ORDER BY f.local_day
    """)


def lag_correlation(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== LAG 1 BIAS AUTOCORRELATION ===")
    print(con.execute("""
        WITH w AS (
            SELECT local_day, signed_gap,
                   LAG(signed_gap, 1) OVER (ORDER BY local_day) AS prev_gap
            FROM daily_bias
        )
        SELECT
            COUNT(*) FILTER (WHERE prev_gap IS NOT NULL) AS n,
            ROUND(CORR(signed_gap, prev_gap), 3) AS corr_lag1,
            ROUND(AVG(signed_gap - prev_gap), 3) AS mean_day_over_day_change
        FROM w WHERE prev_gap IS NOT NULL
    """).df())


def conditional_hit_rate(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY D HIT RATE BY YESTERDAY'S GAP SIGN ===")
    print(con.execute(f"""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p12_mid) AS fav_lo
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        ),
        d_target AS (
            SELECT f.local_day, f.fav_lo,
                   r.lo_f AS d_lo, r.hi_f AS d_hi, r.p12_mid AS d_p
            FROM fav f
            JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
            WHERE r.p12_mid IS NOT NULL AND r.p12_mid >= 0.02
        ),
        lagged AS (
            SELECT local_day, LAG(signed_gap) OVER (ORDER BY local_day) AS prev_gap
            FROM daily_bias
        )
        SELECT
            CASE
                WHEN l.prev_gap IS NULL THEN '0: no prior'
                WHEN l.prev_gap > 3 THEN '1: prev > +3F'
                WHEN l.prev_gap > 0 THEN '2: prev +0 to +3F'
                WHEN l.prev_gap = 0 THEN '3: prev = 0'
                ELSE '4: prev < 0'
            END AS band,
            COUNT(*) AS n,
            ROUND(AVG(CASE WHEN db.day_max_whole BETWEEN dt.d_lo AND dt.d_hi THEN 1.0 ELSE 0.0 END), 3) AS hit_rate,
            ROUND(AVG(CASE WHEN dt.d_p > 0 THEN
                (CASE WHEN db.day_max_whole BETWEEN dt.d_lo AND dt.d_hi THEN 1 ELSE 0 END) / (dt.d_p * (1 + {FEE})) - 1
            END), 3) AS net_avg,
            ROUND(SUM(CASE WHEN dt.d_p > 0 THEN
                (CASE WHEN db.day_max_whole BETWEEN dt.d_lo AND dt.d_hi THEN 1 ELSE 0 END) / (dt.d_p * (1 + {FEE})) - 1
            END), 2) AS cum_pnl
        FROM daily_bias db
        JOIN d_target dt ON dt.local_day = db.local_day
        LEFT JOIN lagged l ON l.local_day = db.local_day
        GROUP BY band ORDER BY band
    """).df())


def per_day_table(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== PER-DAY GAP SEQUENCE ===")
    print(con.execute("""
        SELECT local_day, fav_lo, day_max_whole AS dmax, signed_gap AS gap,
               fav_hit AS hit
        FROM daily_bias
        ORDER BY local_day
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    lag_correlation(con)
    conditional_hit_rate(con)
    per_day_table(con)


if __name__ == "__main__":
    main()

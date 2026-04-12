"""Experiment 31 — Day-of-week effect on Strategy D.

Polymarket retail flow likely differs by day of week (weekends have
more home traders, weekdays have work-hour traders, etc.). Does the
upward bias differ by DoW? Does Strategy D perform better on certain
days?

Method: tag each Strategy D trade with day-of-week, compute hit rate
and cum_pnl per DoW.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

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

    print("\n=== STRATEGY D BY DAY OF WEEK ===")
    print(con.execute(f"""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p12_mid) AS fav_lo
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        ),
        d AS (
            SELECT f.local_day, r.p12_mid, md.day_max_whole, r.lo_f, r.hi_f,
                   CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                   r.p12_mid * (1 + {FEE}) AS entry_cost,
                   EXTRACT(DOW FROM f.local_day)::INT AS dow,
                   strftime(f.local_day, '%A') AS day_name
            FROM fav f
            JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE r.p12_mid IS NOT NULL AND r.p12_mid >= 0.02
        )
        SELECT
            dow,
            day_name,
            COUNT(*) AS n,
            ROUND(AVG(p12_mid), 3) AS avg_p,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
            ROUND(SUM(y / entry_cost - 1), 2) AS cum_pnl
        FROM d
        GROUP BY dow, day_name ORDER BY dow
    """).df())

    print("\n=== UNIVERSAL UPWARD BIAS BY DOW (all 55 days, not just D-eligible) ===")
    print(con.execute("""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p12_mid) AS fav_lo
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT
            EXTRACT(DOW FROM f.local_day)::INT AS dow,
            strftime(f.local_day, '%A') AS day_name,
            COUNT(*) AS n,
            ROUND(AVG(md.day_max_whole - f.fav_lo), 2) AS mean_signed_gap,
            COUNT(*) FILTER (WHERE md.day_max_whole > f.fav_lo) AS n_upward
        FROM fav f
        JOIN metar_daily md ON md.local_date = f.local_day
        GROUP BY dow, day_name ORDER BY dow
    """).df())


if __name__ == "__main__":
    main()

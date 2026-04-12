"""Experiment 29 — Edge decay over the 55-day window.

Exp12 showed a universal +4.07°F upward bias. But is the edge DECAYING?
If the market is gradually learning to correct its bias, we'd see the
signed_gap shrinking over time (later days have smaller gaps).

If decay is fast, the Strategy D headline numbers are upward-biased by
early-window observations. Live deployment would underperform backtest.

Test: rolling 14-day and 21-day average of signed_gap across the 55
days. Look for a downward trend.
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

    print("\n=== SIGNED GAP PER DAY + ROLLING MEANS ===")
    print(con.execute("""
        WITH fav AS (
            SELECT local_day,
                   arg_max(lo_f, p12_mid) AS fav_lo
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        ),
        daily AS (
            SELECT f.local_day, f.fav_lo, md.day_max_whole,
                   (md.day_max_whole - f.fav_lo) AS signed_gap
            FROM fav f JOIN metar_daily md ON md.local_date = f.local_day
        )
        SELECT local_day, fav_lo, day_max_whole AS dmax, signed_gap,
               ROUND(AVG(signed_gap) OVER (ORDER BY local_day
                   ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)::FLOAT, 2) AS rolling_14d,
               ROUND(AVG(signed_gap) OVER (ORDER BY local_day
                   ROWS BETWEEN 20 PRECEDING AND CURRENT ROW)::FLOAT, 2) AS rolling_21d
        FROM daily ORDER BY local_day
    """).df())

    print("\n=== FIRST vs LAST HALF MEAN GAP ===")
    print(con.execute("""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p12_mid) AS fav_lo
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        ),
        daily AS (
            SELECT f.local_day, (md.day_max_whole - f.fav_lo) AS signed_gap,
                   ROW_NUMBER() OVER (ORDER BY f.local_day) AS rk,
                   COUNT(*) OVER () AS total
            FROM fav f JOIN metar_daily md ON md.local_date = f.local_day
        )
        SELECT
            CASE WHEN rk <= total/2 THEN 'first_half' ELSE 'second_half' END AS half,
            COUNT(*) AS n,
            ROUND(AVG(signed_gap), 2) AS mean_gap,
            ROUND(STDDEV(signed_gap), 2) AS std,
            COUNT(*) FILTER (WHERE signed_gap > 0) AS n_upward,
            COUNT(*) FILTER (WHERE signed_gap <= 0) AS n_not_upward
        FROM daily
        GROUP BY half ORDER BY half
    """).df())


if __name__ == "__main__":
    main()

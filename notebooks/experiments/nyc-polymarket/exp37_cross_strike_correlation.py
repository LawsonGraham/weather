"""Experiment 37 — Cross-strike price correlation through the day.

Hypothesis: when one strike's price moves up, its neighbors move down
(probability mass redistributes). Adjacent strikes should be negatively
correlated; far strikes should be uncorrelated.

Method: for each day's range strike ladder, sample yes_price at the 7
hours 06/08/10/12/14/16/18 EDT. Build a wide table of one row per day,
columns for each strike's price trajectory. Then compute correlations
between adjacent strikes (offset +1, +2) and farther strikes (+3, +4).
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

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
        CREATE OR REPLACE TEMP TABLE nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)

    # Build a tall table: one row per (slug, hour_utc) with the price
    # snapshot at that hour. Use 12 EDT (16 UTC) and 18 EDT (22 UTC).
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE wide AS
        SELECT
            nr.local_day, nr.lo_f, nr.hi_f, nr.strike,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_12,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '22 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_18
        FROM nyc_range nr
    """)

    # Compute drift = p_18 - p_12 per strike per day. Then look at
    # neighbor correlations.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE drift AS
        SELECT local_day, lo_f, hi_f, p_12, p_18, (p_18 - p_12) AS d
        FROM wide WHERE p_12 IS NOT NULL AND p_18 IS NOT NULL
    """)

    print("\n=== AVERAGE DRIFT BY STRIKE POSITION RELATIVE TO 12 EDT FAVORITE ===")
    print(con.execute("""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p_12) AS fav_lo
            FROM drift GROUP BY 1
        ),
        rel AS (
            SELECT d.local_day, (d.lo_f - f.fav_lo) AS pos_offset, d.d, d.p_12, d.p_18
            FROM drift d JOIN fav f USING (local_day)
        )
        SELECT
            pos_offset,
            COUNT(*) AS n,
            ROUND(AVG(p_12), 3) AS mean_p_12,
            ROUND(AVG(p_18), 3) AS mean_p_18,
            ROUND(AVG(d), 3) AS mean_drift_12_to_18
        FROM rel
        WHERE pos_offset BETWEEN -4 AND 6
        GROUP BY pos_offset ORDER BY pos_offset
    """).df())

    print("\n=== CORRELATION OF FAVORITE DRIFT vs +k OFFSET DRIFT ===")
    print("    For each day, take fav's drift and the +k strike's drift.")
    print("    If they're anticorrelated, the book is rebalancing as we'd expect.")
    print(con.execute("""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p_12) AS fav_lo
            FROM drift GROUP BY 1
        ),
        f_drift AS (
            SELECT d.local_day, d.d AS fav_drift
            FROM drift d JOIN fav f ON f.local_day=d.local_day AND f.fav_lo=d.lo_f
        ),
        offsets AS (
            SELECT d.local_day, (d.lo_f - f.fav_lo) AS off, d.d AS off_drift
            FROM drift d JOIN fav f ON f.local_day=d.local_day
            WHERE d.lo_f != f.fav_lo
        ),
        merged AS (
            SELECT o.local_day, o.off, o.off_drift, fd.fav_drift
            FROM offsets o JOIN f_drift fd USING (local_day)
        )
        SELECT
            off,
            COUNT(*) AS n,
            ROUND(CORR(fav_drift, off_drift), 3) AS corr_with_fav_drift
        FROM merged
        WHERE off BETWEEN -4 AND 6
        GROUP BY off ORDER BY off
    """).df())


if __name__ == "__main__":
    main()

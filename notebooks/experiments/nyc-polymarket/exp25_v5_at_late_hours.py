"""Experiment 25 — Strategy D v5 (skip rules) at 16 EDT and 18 EDT.

Exp24 found the skip rule (skip dry OR rise_needed>=6) lifts Strategy D
at 12 EDT from 31% to 42% hit rate. Exp18/20 found later entry hours
(16/18 EDT) have higher hit rates even without the skip rule. This
stacks the two: does V5 at 16 EDT or 18 EDT push hit rates past 50% or
into the 60s?

Applies the v5 skip rule (using METAR at 12 EDT — the entry-time METAR,
not the 16/18 EDT one, because the skip decision is made pre-afternoon)
and enters the +2 bucket at the given hour using real-ask price.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
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
    # METAR at 12 EDT for the skip decision
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_12edt AS
        WITH ranked AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   valid, tmpf, relh,
                   ROW_NUMBER() OVER (
                       PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                       ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                   ) AS rk
            FROM '{METAR}' WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT local_date, tmpf, relh FROM ranked WHERE rk=1
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


def build_snapshot(con: duckdb.DuckDBPyConnection, hour_utc: int) -> None:
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE snap_h{hour_utc} AS
        SELECT nr.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug
               AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_mid,
            CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' AS target_ts
        FROM nyc_range nr
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE fav_h{hour_utc} AS
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p_mid DESC NULLS LAST) AS rk
            FROM snap_h{hour_utc} WHERE p_mid IS NOT NULL
        )
        SELECT local_day, lo_f AS fav_lo, target_ts
        FROM ranked WHERE rk = 1
    """)


def run_v5(con: duckdb.DuckDBPyConnection, hour_utc: int, label: str) -> None:
    edt = hour_utc - 4
    # V1 baseline (no skip)
    print(f"\n=== @ {edt} EDT — V1 baseline (no skip) ===")
    print(con.execute(f"""
        WITH trade AS (
            SELECT f.local_day, s.strike, s.p_mid, s.lo_f, s.hi_f, md.day_max_whole,
                   CASE WHEN md.day_max_whole BETWEEN s.lo_f AND s.hi_f THEN 1 ELSE 0 END AS y,
                   (s.p_mid) * (1 + {FEE}) AS entry_cost
            FROM fav_h{hour_utc} f
            JOIN snap_h{hour_utc} s ON s.local_day = f.local_day AND s.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE s.p_mid IS NOT NULL AND s.p_mid >= 0.02 AND (s.p_mid * 1.02) < 0.97
        )
        SELECT COUNT(*) AS n, ROUND(AVG(y), 3) AS hit,
               ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
               ROUND(QUANTILE_CONT(y / entry_cost - 1, 0.5), 3) AS net_med,
               ROUND(SUM(y / entry_cost - 1), 2) AS cum
        FROM trade
    """).df())

    # V5 at this hour
    print(f"\n=== @ {edt} EDT — V5 (skip dry OR rise_needed >= 6) ===")
    print(con.execute(f"""
        WITH trade AS (
            SELECT f.local_day, s.strike, s.p_mid, s.lo_f, s.hi_f, md.day_max_whole,
                   m12.tmpf, m12.relh,
                   (f.fav_lo - m12.tmpf) AS rise_needed,
                   CASE WHEN md.day_max_whole BETWEEN s.lo_f AND s.hi_f THEN 1 ELSE 0 END AS y,
                   (s.p_mid) * (1 + {FEE}) AS entry_cost
            FROM fav_h{hour_utc} f
            JOIN snap_h{hour_utc} s ON s.local_day = f.local_day AND s.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            JOIN metar_12edt m12 ON m12.local_date = f.local_day
            WHERE s.p_mid IS NOT NULL AND s.p_mid >= 0.02 AND (s.p_mid * 1.02) < 0.97
              AND m12.relh >= 40
              AND (f.fav_lo - m12.tmpf) < 6
        )
        SELECT COUNT(*) AS n, ROUND(AVG(y), 3) AS hit,
               ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
               ROUND(QUANTILE_CONT(y / entry_cost - 1, 0.5), 3) AS net_med,
               ROUND(SUM(y / entry_cost - 1), 2) AS cum
        FROM trade
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    for h, label in [(16, "12 EDT"), (20, "16 EDT"), (22, "18 EDT")]:
        build_snapshot(con, h)
        run_v5(con, h, label)


if __name__ == "__main__":
    main()

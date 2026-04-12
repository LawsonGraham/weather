"""Experiment 22 — Condition Strategy D wins on weather regime.

Strategy D has ~30% hit rate. Can we filter to high-hit-rate days ex-ante
using METAR features at 12 EDT? If yes, the refined strategy would fire
fewer days but with much higher per-trade edge.

Method: for each Strategy D trade (35 conservative bets), tag as win or
loss. Then split the population by METAR features and compute per-bucket
hit rates.

Features at 12 EDT:
    - sky cover (skyc1)
    - tmpf / tmpf tercile
    - dwpf / relh
    - wind direction / speed
    - rise_needed (fav_lo - tmpf_12)  ← strongest signal from exp12

Hypothesis: D wins cluster on days where the AFTERNOON is going to
heat up unexpectedly. METAR at 12 EDT should predict this via
sky cover (clear = big heating) and rise_needed (stable morning =
bias under-estimates afternoon rise).
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
        CREATE OR REPLACE TEMP VIEW metar_12edt AS
        WITH ranked AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   valid, tmpf, dwpf, relh, sknt, drct, skyc1,
                   ROW_NUMBER() OVER (
                       PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                       ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                   ) AS rk
            FROM '{METAR}' WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT local_date, tmpf, dwpf, relh, sknt, drct, skyc1
        FROM ranked WHERE rk=1
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
             ORDER BY p.timestamp DESC LIMIT 1) AS p12
        FROM r
    """)
    # Strategy D trades with win/loss flag and METAR context
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE d_trades AS
        WITH fav AS (
            SELECT local_day,
                   arg_max(strike, p12) AS fav_strike,
                   max(p12)             AS fav_p,
                   arg_max(lo_f, p12)   AS fav_lo,
                   arg_max(hi_f, p12)   AS fav_hi
            FROM range_12 WHERE p12 IS NOT NULL GROUP BY 1
        )
        SELECT
            f.local_day, f.fav_strike, f.fav_lo, f.fav_p,
            r.strike AS d_strike, r.p12 AS d_p, r.lo_f AS d_lo, r.hi_f AS d_hi,
            md.day_max_whole,
            CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
            m12.tmpf, m12.dwpf, m12.relh, m12.sknt, m12.drct, m12.skyc1,
            (f.fav_lo - m12.tmpf) AS rise_needed,
            CASE
                WHEN m12.skyc1 IN ('CLR','FEW','SCT') THEN 'clear'
                WHEN m12.skyc1 = 'BKN' THEN 'broken'
                WHEN m12.skyc1 = 'OVC' THEN 'overcast'
                ELSE 'unknown'
            END AS sky_bucket
        FROM fav f
        JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
        JOIN metar_daily md ON md.local_date = f.local_day
        LEFT JOIN metar_12edt m12 ON m12.local_date = f.local_day
        WHERE r.p12 IS NOT NULL AND r.p12 >= 0.02
    """)


def overall(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY D BASE RATE ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            SUM(y) AS n_wins,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(d_p), 3) AS avg_entry
        FROM d_trades
    """).df())


def by_sky(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== HIT RATE BY SKY COVER ===")
    print(con.execute(f"""
        SELECT
            sky_bucket,
            COUNT(*) AS n,
            SUM(y) AS wins,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(d_p), 3) AS avg_entry,
            ROUND(AVG(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 3) AS net_avg
        FROM d_trades
        GROUP BY sky_bucket ORDER BY sky_bucket
    """).df())


def by_rise_needed(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== HIT RATE BY rise_needed BAND (fav_lo - tmpf_12) ===")
    print(con.execute(f"""
        SELECT
            CASE
                WHEN rise_needed < 0 THEN '1: <0 (fav below now)'
                WHEN rise_needed < 3 THEN '2: 0-3F'
                WHEN rise_needed < 6 THEN '3: 3-6F'
                WHEN rise_needed < 10 THEN '4: 6-10F'
                ELSE '5: 10+F'
            END AS band,
            COUNT(*) AS n,
            SUM(y) AS wins,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(d_p), 3) AS avg_entry,
            ROUND(AVG(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 3) AS net_avg
        FROM d_trades
        WHERE rise_needed IS NOT NULL
        GROUP BY band ORDER BY band
    """).df())


def by_relh(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== HIT RATE BY RELH TERCILE ===")
    print(con.execute(f"""
        WITH q AS (
            SELECT *, NTILE(3) OVER (ORDER BY relh) AS tercile
            FROM d_trades WHERE relh IS NOT NULL
        )
        SELECT
            tercile, COUNT(*) AS n, SUM(y) AS wins,
            ROUND(AVG(relh), 1) AS avg_relh,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 3) AS net_avg
        FROM q GROUP BY tercile ORDER BY tercile
    """).df())


def combined_filter(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== COMBINED FILTER: clear sky AND rise_needed < 6 ===")
    print(con.execute(f"""
        SELECT
            COUNT(*) AS n,
            SUM(y) AS wins,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(d_p), 3) AS avg_entry,
            ROUND(AVG(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 3) AS net_avg,
            ROUND(SUM(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 2) AS cum_pnl
        FROM d_trades
        WHERE sky_bucket = 'clear' AND rise_needed < 6
    """).df())

    print("\n=== REFINED vs ORIGINAL (no filter) ===")
    print(con.execute(f"""
        SELECT
            'all'          AS filter_name,
            COUNT(*) AS n,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(SUM(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 2) AS cum_pnl
        FROM d_trades
        UNION ALL
        SELECT
            'clear+rise<6' AS filter_name,
            COUNT(*), ROUND(AVG(y), 3),
            ROUND(SUM(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 2)
        FROM d_trades WHERE sky_bucket='clear' AND rise_needed < 6
        UNION ALL
        SELECT
            'clear only'   AS filter_name,
            COUNT(*), ROUND(AVG(y), 3),
            ROUND(SUM(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 2)
        FROM d_trades WHERE sky_bucket='clear'
        UNION ALL
        SELECT
            'rise<6 only'  AS filter_name,
            COUNT(*), ROUND(AVG(y), 3),
            ROUND(SUM(CASE WHEN d_p > 0 THEN y/(d_p*(1+{FEE})) - 1 END), 2)
        FROM d_trades WHERE rise_needed < 6
    """).df())


def wins_vs_losses_table(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== MEAN FEATURE VALUES: WINS vs LOSSES ===")
    print(con.execute("""
        SELECT
            y AS outcome,
            COUNT(*) AS n,
            ROUND(AVG(tmpf), 1) AS mean_tmpf,
            ROUND(AVG(dwpf), 1) AS mean_dwpf,
            ROUND(AVG(relh), 1) AS mean_relh,
            ROUND(AVG(sknt), 1) AS mean_wind,
            ROUND(AVG(rise_needed), 2) AS mean_rise_needed,
            ROUND(AVG(fav_p), 3) AS mean_fav_p,
            ROUND(AVG(d_p), 3) AS mean_d_p
        FROM d_trades
        GROUP BY y ORDER BY y
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    overall(con)
    wins_vs_losses_table(con)
    by_sky(con)
    by_rise_needed(con)
    by_relh(con)
    combined_filter(con)


if __name__ == "__main__":
    main()

"""Exploratory M — Strategy D V1 vs V3 (16 EDT vs 17 EDT) multi-day backtest.

Exp L rejected V2 (15:30 EDT entry) and floated V3 (16:30 EDT) as a
candidate. Full minute data only covers 1-2 days, which isn't enough
for a statistical test. Instead, use the HOURLY prices_history data
(full lifetime, 571 slugs) and compare:

  - V1: entry at 20:00 UTC (16 EDT), buy favorite+2 bucket at that price
  - V3: entry at 21:00 UTC (17 EDT), buy favorite+2 bucket at that price

Hit rate is outcome-based: METAR LGA max for the day determines winner.

Caveats:
  - Hourly fidelity is the best we have for a multi-day test.
  - The hourly midpoint doesn't carry the real-ask premium we measured
    in exp K. We apply a flat 2% fee consistent with exp14/exp40.
  - The backtest uses `arg_max(mid, p)` favorite definition per snapshot.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 100)

HOURLY = "data/processed/polymarket_prices_history/hourly/**/*.parquet"
MARKETS = "data/processed/polymarket_weather/markets.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # METAR daily max for LGA
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_daily AS
        WITH m AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   GREATEST(COALESCE(tmpf, -999),
                            COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999)) AS te
            FROM '{METAR}' WHERE station='LGA'
        )
        SELECT local_date, ROUND(MAX(te))::INT AS metar_max
        FROM m WHERE te > -900
        GROUP BY 1
    """)

    # Bucket metadata from markets.parquet — closed NYC ladders only
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE buckets AS
        SELECT slug,
               group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%'
          AND closed AND group_item_title NOT ILIKE '%or %'
    """)

    # For each slug, extract the hourly midpoint at 16 EDT and 17 EDT of
    # the day of resolution. 16 EDT = 20 UTC, 17 EDT = 21 UTC.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE entries AS
        WITH h AS (
            SELECT slug, timestamp, p_yes,
                   CAST((timestamp AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   EXTRACT(hour FROM (timestamp AT TIME ZONE 'America/New_York'))::INT AS local_hour
            FROM '{HOURLY}'
        ),
        joined AS (
            SELECT b.slug, b.local_day, b.lo_f, b.hi_f, h.p_yes, h.local_hour
            FROM buckets b
            JOIN h ON h.slug = b.slug AND h.local_date = b.local_day
            WHERE h.local_hour IN (16, 17, 18)
        )
        SELECT slug, local_day, lo_f, hi_f,
               MAX(CASE WHEN local_hour = 16 THEN p_yes END) AS p16,
               MAX(CASE WHEN local_hour = 17 THEN p_yes END) AS p17,
               MAX(CASE WHEN local_hour = 18 THEN p_yes END) AS p18
        FROM joined
        GROUP BY slug, local_day, lo_f, hi_f
    """)

    # Compute per-day favorite at each hour and find the +2 bucket
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW trades AS
        WITH fav_16 AS (
            SELECT local_day, arg_max(lo_f, p16) AS fav_lo
            FROM entries WHERE p16 IS NOT NULL AND p16 >= 0.02
            GROUP BY local_day
        ),
        fav_17 AS (
            SELECT local_day, arg_max(lo_f, p17) AS fav_lo
            FROM entries WHERE p17 IS NOT NULL AND p17 >= 0.02
            GROUP BY local_day
        ),
        v1 AS (
            SELECT f.local_day, e.lo_f, e.hi_f, e.p16 AS entry_p,
                   md.metar_max,
                   CASE WHEN md.metar_max BETWEEN e.lo_f AND e.hi_f THEN 1 ELSE 0 END AS y,
                   'V1-16' AS strat
            FROM fav_16 f
            JOIN entries e ON e.local_day = f.local_day AND e.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE e.p16 IS NOT NULL AND e.p16 >= 0.005 AND e.p16 < 0.97
        ),
        v3 AS (
            SELECT f.local_day, e.lo_f, e.hi_f, e.p17 AS entry_p,
                   md.metar_max,
                   CASE WHEN md.metar_max BETWEEN e.lo_f AND e.hi_f THEN 1 ELSE 0 END AS y,
                   'V3-17' AS strat
            FROM fav_17 f
            JOIN entries e ON e.local_day = f.local_day AND e.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE e.p17 IS NOT NULL AND e.p17 >= 0.005 AND e.p17 < 0.97
        ),
        v4 AS (
            SELECT f.local_day, e.lo_f, e.hi_f, e.p18 AS entry_p,
                   md.metar_max,
                   CASE WHEN md.metar_max BETWEEN e.lo_f AND e.hi_f THEN 1 ELSE 0 END AS y,
                   'V4-18' AS strat
            FROM fav_17 f
            JOIN entries e ON e.local_day = f.local_day AND e.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE e.p18 IS NOT NULL AND e.p18 >= 0.005 AND e.p18 < 0.97
        )
        SELECT * FROM v1
        UNION ALL SELECT * FROM v3
        UNION ALL SELECT * FROM v4
    """)

    print("=== Strategy D V1 (16 EDT) vs V3 (17 EDT) vs V4 (18 EDT) on hourly data ===")
    print(con.execute(f"""
        SELECT strat,
               COUNT(*) AS n,
               ROUND(AVG(entry_p), 3) AS avg_entry,
               ROUND(AVG(y), 3) AS hit_rate,
               ROUND(AVG(y / (entry_p * (1 + {FEE})) - 1), 3) AS net_avg,
               ROUND(SUM(y / (entry_p * (1 + {FEE})) - 1), 2) AS cum_pnl,
               ROUND(AVG(y) / AVG(entry_p * (1 + {FEE})), 3) AS ev_ratio
        FROM trades
        GROUP BY strat
        ORDER BY strat
    """).df())

    # Side-by-side per day comparison on days that have BOTH entry_p
    print("\n=== per-day V1 vs V3 delta ===")
    print(con.execute(f"""
        WITH v1 AS (
            SELECT local_day, entry_p AS p_v1, y FROM trades WHERE strat = 'V1-16'
        ),
        v3 AS (
            SELECT local_day, entry_p AS p_v3 FROM trades WHERE strat = 'V3-17'
        )
        SELECT v1.local_day,
               ROUND(v1.p_v1, 3) AS p16,
               ROUND(v3.p_v3, 3) AS p17,
               ROUND(v3.p_v3 - v1.p_v1, 3) AS delta,
               v1.y
        FROM v1 JOIN v3 USING (local_day)
        ORDER BY v1.local_day
        LIMIT 30
    """).df())

    # How many days have +1 and +2 buckets still tradeable?
    print("\n=== trade counts by entry hour ===")
    print(con.execute("""
        SELECT strat, COUNT(*) AS n_trades FROM trades GROUP BY 1 ORDER BY 1
    """).df())


if __name__ == "__main__":
    main()

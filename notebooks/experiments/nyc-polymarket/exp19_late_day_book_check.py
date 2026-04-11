"""Experiment 19 — Verify 16/18 EDT book activity for V1/V3 deployment.

Exp18 found Strategy D works dramatically better at 16-18 EDT than at 12 EDT.
But those late-day snapshots may be hitting thin or stale books — by 18 EDT
(22 UTC) some NYC daily-temp markets have already resolved or have minimal
active trading. We need to check that the `+2 bucket` we're buying at 16/18
EDT was actually ACTIVELY TRADING at that time, not sitting on a frozen
resting order.

Method: for each "+2 bucket" trade at 16 EDT and 18 EDT, count fills in a
±30 min window around the snapshot. Flag slugs with:
  - n_fills < 3   (stale)
  - window_total_usd < $20  (no real size)
  - no bid-ask action  (only one side of book trading)

Then re-score the strategy after dropping the stale-book trades.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

SPREAD = 0.03
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
        CREATE OR REPLACE TEMP TABLE nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)


def build_trades_for_hour(con: duckdb.DuckDBPyConnection, hour_utc: int) -> None:
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE prices_h{hour_utc} AS
        SELECT nr.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug
               AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_h
        FROM nyc_range nr
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE fav_h{hour_utc} AS
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p_h DESC NULLS LAST) AS rk
            FROM prices_h{hour_utc} WHERE p_h IS NOT NULL
        )
        SELECT local_day, lo_f AS fav_lo, hi_f AS fav_hi, strike AS fav_strike, p_h AS fav_p
        FROM ranked WHERE rk = 1
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE strat_h{hour_utc} AS
        SELECT
            f.local_day,
            ph.slug AS bucket_slug,
            ph.strike AS bucket,
            ph.lo_f AS lo_bought,
            ph.p_h AS p_entry,
            md.day_max_whole,
            CASE WHEN md.day_max_whole BETWEEN ph.lo_f AND ph.hi_f THEN 1 ELSE 0 END AS y,
            (ph.p_h + {SPREAD}) * (1 + {FEE}) AS entry_cost
        FROM fav_h{hour_utc} f
        JOIN prices_h{hour_utc} ph ON ph.local_day = f.local_day AND ph.lo_f = f.fav_lo + 2
        JOIN metar_daily md ON md.local_date = f.local_day
        WHERE ph.p_h IS NOT NULL AND (ph.p_h + {SPREAD}) < 0.97
          AND ph.p_h >= 0.02
    """)


def verify_book_activity(con: duckdb.DuckDBPyConnection, hour_utc: int) -> None:
    edt = hour_utc - 4
    print(f"\n=== BOOK ACTIVITY AT {edt} EDT (±30min fills window) ===")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE book_h{hour_utc} AS
        SELECT
            s.local_day, s.bucket, s.p_entry, s.y, s.entry_cost,
            COALESCE((
                SELECT COUNT(*) FROM '{FILLS}' f
                WHERE f.slug = s.bucket_slug
                  AND f.timestamp BETWEEN
                      (CAST(s.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' - INTERVAL '30 minute')
                      AND (CAST(s.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' + INTERVAL '30 minute')
            ), 0) AS n_fills,
            COALESCE((
                SELECT SUM(usd) FROM '{FILLS}' f
                WHERE f.slug = s.bucket_slug
                  AND f.timestamp BETWEEN
                      (CAST(s.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' - INTERVAL '30 minute')
                      AND (CAST(s.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' + INTERVAL '30 minute')
            ), 0) AS vol_usd,
            COALESCE((
                SELECT COUNT(DISTINCT taker) FROM '{FILLS}' f
                WHERE f.slug = s.bucket_slug
                  AND f.timestamp BETWEEN
                      (CAST(s.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' - INTERVAL '30 minute')
                      AND (CAST(s.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' + INTERVAL '30 minute')
            ), 0) AS takers
        FROM strat_h{hour_utc} s
    """)

    # Summary
    print(con.execute(f"""
        SELECT
            COUNT(*) AS n_trades,
            COUNT(*) FILTER (WHERE n_fills = 0) AS n_zero_fills,
            COUNT(*) FILTER (WHERE n_fills < 3) AS n_lt_3_fills,
            COUNT(*) FILTER (WHERE vol_usd < 20) AS n_lt_20usd,
            ROUND(AVG(n_fills), 1) AS mean_fills,
            ROUND(QUANTILE_CONT(n_fills, 0.5), 0) AS median_fills,
            ROUND(AVG(vol_usd), 0) AS mean_vol,
            ROUND(QUANTILE_CONT(vol_usd, 0.5), 0) AS median_vol
        FROM book_h{hour_utc}
    """).df())

    # Per-trade detail
    print("\nPer-trade detail:")
    print(con.execute(f"""
        SELECT local_day, bucket,
               ROUND(p_entry, 3) AS p, y,
               n_fills, ROUND(vol_usd, 0) AS usd, takers,
               CASE WHEN n_fills < 3 OR vol_usd < 20 THEN 'STALE' ELSE 'active' END AS status
        FROM book_h{hour_utc}
        ORDER BY local_day
    """).df())

    # Re-score dropping stale
    print(f"\n=== STRATEGY D @ {edt} EDT — after dropping stale books (n_fills ≥ 3 AND vol_usd ≥ 20) ===")
    print(con.execute(f"""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(p_entry), 3) AS avg_p,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y/entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(y/entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(y/entry_cost - 1), 2) AS cum_pnl
        FROM book_h{hour_utc}
        WHERE n_fills >= 3 AND vol_usd >= 20
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    for h in [20, 22]:  # 16 EDT, 18 EDT
        build_trades_for_hour(con, h)
        verify_book_activity(con, h)


if __name__ == "__main__":
    main()

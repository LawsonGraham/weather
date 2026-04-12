"""Experiment 30 — Taker direction bias on favorite vs +2 bucket.

What are OTHER traders betting on? For each Strategy D day, look at
fills in the favorite and the +2 bucket during the main trading window
(14-16 UTC = 10-12 EDT and 16-20 UTC = 12-16 EDT). Measure:

    • Net YES taker flow on the favorite (buy minus sell)
    • Net YES taker flow on the +2 bucket
    • Net NO taker flow on each

If takers systematically BUY YES on the favorite and BUY NO on the +2
bucket, that confirms the market over-commits to the low bucket and
gives us direct evidence of the "smart money is wrong" pattern.

This tells us: are the humans confidently wrong, or uncertainly wrong?
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
    con.execute("""
        CREATE OR REPLACE TEMP TABLE pairs AS
        WITH fav AS (
            SELECT local_day,
                   arg_max(slug, p12_mid) AS fav_slug,
                   arg_max(lo_f, p12_mid) AS fav_lo
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT f.local_day, f.fav_slug, f.fav_lo,
               d.slug AS d_slug
        FROM fav f
        JOIN range_12 d ON d.local_day = f.local_day AND d.lo_f = f.fav_lo + 2
        WHERE d.p12_mid IS NOT NULL AND d.p12_mid >= 0.02
    """)

    print("\n=== TAKER FLOW ON FAV vs +2 BUCKET (14-20 UTC window, main trading day) ===")
    print("    For each slug, count YES-buy (taker paying ask) vs YES-sell (taker at bid)")
    print("    Net bullish flow = yes_buy_count - yes_sell_count")
    print(con.execute(f"""
        WITH flow_fav AS (
            SELECT p.local_day,
                   COUNT(*) FILTER (WHERE UPPER(f.outcome)='YES' AND UPPER(f.side)='BUY') AS fav_yes_buy,
                   COUNT(*) FILTER (WHERE UPPER(f.outcome)='YES' AND UPPER(f.side)='SELL') AS fav_yes_sell,
                   COUNT(*) FILTER (WHERE UPPER(f.outcome)='NO'  AND UPPER(f.side)='BUY') AS fav_no_buy,
                   SUM(f.usd) AS fav_total_usd
            FROM pairs p
            LEFT JOIN '{FILLS}' f ON f.slug = p.fav_slug
              AND f.timestamp BETWEEN
                  (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '14 hour')
                  AND (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour')
            GROUP BY p.local_day
        ),
        flow_d AS (
            SELECT p.local_day,
                   COUNT(*) FILTER (WHERE UPPER(f.outcome)='YES' AND UPPER(f.side)='BUY') AS d_yes_buy,
                   COUNT(*) FILTER (WHERE UPPER(f.outcome)='YES' AND UPPER(f.side)='SELL') AS d_yes_sell,
                   COUNT(*) FILTER (WHERE UPPER(f.outcome)='NO'  AND UPPER(f.side)='BUY') AS d_no_buy,
                   SUM(f.usd) AS d_total_usd
            FROM pairs p
            LEFT JOIN '{FILLS}' f ON f.slug = p.d_slug
              AND f.timestamp BETWEEN
                  (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '14 hour')
                  AND (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour')
            GROUP BY p.local_day
        )
        SELECT
            COUNT(*) AS n_days,
            ROUND(SUM(fav_yes_buy) - SUM(fav_yes_sell))::BIGINT AS fav_net_yes_flow,
            ROUND(SUM(fav_no_buy))::BIGINT AS fav_no_buy_total,
            ROUND(SUM(d_yes_buy) - SUM(d_yes_sell))::BIGINT AS d_net_yes_flow,
            ROUND(SUM(d_no_buy))::BIGINT AS d_no_buy_total,
            ROUND(AVG(fav_total_usd), 0) AS avg_fav_usd,
            ROUND(AVG(d_total_usd), 0) AS avg_d_usd
        FROM flow_fav
        JOIN flow_d USING (local_day)
    """).df())

    print("\n=== PER-DAY FLOW SAMPLE ===")
    print(con.execute(f"""
        WITH per_day AS (
            SELECT p.local_day,
                   SUM(CASE WHEN UPPER(f.outcome)='YES' AND UPPER(f.side)='BUY' THEN 1 ELSE 0 END) -
                   SUM(CASE WHEN UPPER(f.outcome)='YES' AND UPPER(f.side)='SELL' THEN 1 ELSE 0 END)
                   AS fav_net_yes_flow,
                   ROUND(SUM(f.usd), 0) AS fav_usd
            FROM pairs p
            LEFT JOIN '{FILLS}' f ON f.slug = p.fav_slug
              AND f.timestamp BETWEEN
                  (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '14 hour')
                  AND (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour')
            GROUP BY p.local_day
        ),
        d_flow AS (
            SELECT p.local_day,
                   SUM(CASE WHEN UPPER(f.outcome)='YES' AND UPPER(f.side)='BUY' THEN 1 ELSE 0 END) -
                   SUM(CASE WHEN UPPER(f.outcome)='YES' AND UPPER(f.side)='SELL' THEN 1 ELSE 0 END)
                   AS d_net_yes_flow,
                   ROUND(SUM(f.usd), 0) AS d_usd
            FROM pairs p
            LEFT JOIN '{FILLS}' f ON f.slug = p.d_slug
              AND f.timestamp BETWEEN
                  (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '14 hour')
                  AND (CAST(p.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour')
            GROUP BY p.local_day
        )
        SELECT pd.local_day, pd.fav_net_yes_flow, pd.fav_usd,
               d.d_net_yes_flow, d.d_usd
        FROM per_day pd
        JOIN d_flow d USING (local_day)
        ORDER BY pd.local_day
        LIMIT 30
    """).df())


if __name__ == "__main__":
    main()

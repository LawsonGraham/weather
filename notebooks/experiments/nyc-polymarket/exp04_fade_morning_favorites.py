"""Experiment 04 — Fade the 12 EDT market favorite.

Exp02 baseline showed: `follow the market argmax range strike at 12 EDT` earns
-0.63 per $1 (55 days, hit rate 18%). That's a strong negative — the favorite
is systematically wrong at 12 EDT.

Direct test: sell the favorite (buy NO) at 12 EDT and hold to resolution.
Expected return per $1 invested, if p is the favorite's YES price at 12 EDT:
    - buy NO at price (1 - p)
    - win (1 - p) back if market resolves NO, lose (1 - p) if YES
    - return per $1 = (1 - y) / (1 - p) - 1

Also try 10 EDT and 11 EDT and see if fading the morning favorite is even
more lucrative, and see how the win rate decays with snapshot time.

Also sweep an entry filter: only fade when favorite p ≥ 0.30 (more room to
fade, avoids "favorite already obvious" cases).

And add a ladder-level variant: instead of fading just the argmax, short
the single strike whose p is farthest from the ladder's mean (the most
over-committed bucket).
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


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone = 'UTC'")
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
        CREATE OR REPLACE TEMP VIEW nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or higher%'
          AND group_item_title NOT ILIKE '%or below%'
    """)

    # Range-strike prices at several morning snapshots
    con.execute("""
        CREATE OR REPLACE TEMP TABLE morning_prices AS
        WITH s AS (
            SELECT nr.slug, nr.strike, nr.lo_f, nr.hi_f, nr.local_day,
                   (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '14 hour') AS t10,
                   (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '15 hour') AS t11,
                   (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour') AS t12,
                   (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '17 hour') AS t13
            FROM nyc_range nr
        )
        SELECT s.*,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug=s.slug AND p.timestamp <= s.t10 ORDER BY p.timestamp DESC LIMIT 1) AS p10,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug=s.slug AND p.timestamp <= s.t11 ORDER BY p.timestamp DESC LIMIT 1) AS p11,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug=s.slug AND p.timestamp <= s.t12 ORDER BY p.timestamp DESC LIMIT 1) AS p12,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug=s.slug AND p.timestamp <= s.t13 ORDER BY p.timestamp DESC LIMIT 1) AS p13
        FROM s
    """)


def fade_argmax(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FADE THE MARKET FAVORITE AT EACH MORNING SNAPSHOT ===")
    print("    Strategy: sell YES (buy NO at 1-p) on the argmax range strike at t.")
    print("    Return per $1 invested in NO = (1-y)/(1-p_entry) - 1")
    for label, col in [("10 EDT", "p10"), ("11 EDT", "p11"), ("12 EDT", "p12"), ("13 EDT", "p13")]:
        q = f"""
            WITH favs AS (
                SELECT
                    mp.local_day,
                    arg_max(mp.strike, mp.{col}) AS fav_strike,
                    max(mp.{col})                AS fav_p,
                    arg_max(mp.lo_f, mp.{col})   AS fav_lo,
                    arg_max(mp.hi_f, mp.{col})   AS fav_hi
                FROM morning_prices mp WHERE mp.{col} IS NOT NULL
                GROUP BY mp.local_day
            ),
            scored AS (
                SELECT f.*, md.day_max_whole,
                       CASE WHEN md.day_max_whole BETWEEN f.fav_lo AND f.fav_hi THEN 1 ELSE 0 END AS y
                FROM favs f
                JOIN metar_daily md ON md.local_date = f.local_day
                WHERE md.day_max_whole IS NOT NULL
            )
            SELECT
                '{label}' AS snap,
                COUNT(*) AS n,
                ROUND(AVG(fav_p), 3) AS avg_fav_p,
                ROUND(AVG(1 - y), 3) AS fav_miss_rate,
                -- Long YES return per $1 (the exp02 baseline)
                ROUND(AVG(CASE WHEN fav_p > 0 THEN y/fav_p - 1 END), 3) AS long_yes_avg,
                -- Short fav / long NO: pay (1-p) per share, collect 1 if y=0
                ROUND(AVG(CASE WHEN fav_p < 1 THEN (1 - y)/(1 - fav_p) - 1 END), 3) AS fade_avg,
                ROUND(QUANTILE_CONT(CASE WHEN fav_p < 1 THEN (1 - y)/(1 - fav_p) - 1 END, 0.5), 3) AS fade_med,
                ROUND(SUM(CASE WHEN fav_p < 1 THEN (1 - y)/(1 - fav_p) - 1 END), 2) AS fade_cum_pnl
            FROM scored
        """
        print(con.execute(q).df())


def fade_argmax_p_filter(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FADE ARGMAX — filtered by favorite confidence at 12 EDT ===")
    print("    Only fades when favorite already has p >= threshold (more room to fade).")
    for thr in [0.30, 0.40, 0.50, 0.60]:
        q = f"""
            WITH favs AS (
                SELECT
                    mp.local_day,
                    arg_max(mp.strike, mp.p12) AS fav,
                    max(mp.p12)                AS fav_p,
                    arg_max(mp.lo_f, mp.p12)   AS fav_lo,
                    arg_max(mp.hi_f, mp.p12)   AS fav_hi
                FROM morning_prices mp WHERE mp.p12 IS NOT NULL
                GROUP BY mp.local_day
            ),
            scored AS (
                SELECT f.*, md.day_max_whole,
                       CASE WHEN md.day_max_whole BETWEEN f.fav_lo AND f.fav_hi THEN 1 ELSE 0 END AS y
                FROM favs f
                JOIN metar_daily md ON md.local_date = f.local_day
                WHERE md.day_max_whole IS NOT NULL AND f.fav_p >= {thr}
            )
            SELECT
                {thr} AS fav_p_filter,
                COUNT(*) AS n,
                ROUND(AVG(fav_p), 3) AS avg_fav_p,
                ROUND(AVG(1 - y), 3) AS fav_miss_rate,
                ROUND(AVG((1 - y)/(1 - fav_p) - 1), 3) AS fade_avg,
                ROUND(SUM((1 - y)/(1 - fav_p) - 1), 2) AS fade_cum
            FROM scored
        """
        print(con.execute(q).df())


def fade_all_high_priced(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FADE EVERY RANGE STRIKE WITH p12 >= 0.30 (not just argmax) ===")
    print("    Tests whether the over-committed-bucket pathology is broader than the argmax.")
    print(con.execute("""
        WITH scored AS (
            SELECT mp.local_day, mp.strike, mp.lo_f, mp.hi_f, mp.p12, md.day_max_whole,
                   CASE WHEN md.day_max_whole BETWEEN mp.lo_f AND mp.hi_f THEN 1 ELSE 0 END AS y
            FROM morning_prices mp JOIN metar_daily md ON md.local_date = mp.local_day
            WHERE mp.p12 IS NOT NULL AND md.day_max_whole IS NOT NULL AND mp.p12 >= 0.30
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(p12), 3) AS avg_entry_p,
            ROUND(AVG(1-y), 3) AS miss_rate,
            ROUND(AVG((1-y)/(1-p12) - 1), 3) AS fade_avg_ret,
            ROUND(SUM((1-y)/(1-p12) - 1), 2) AS fade_cum
        FROM scored
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    fade_argmax(con)
    fade_argmax_p_filter(con)
    fade_all_high_priced(con)


if __name__ == "__main__":
    main()

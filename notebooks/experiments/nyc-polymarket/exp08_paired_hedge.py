"""Experiment 08 — Paired short-favorite + long-underdog hedge.

Thesis: On peaked-ladder days (where exp07 showed the fade-the-favorite
strategy has its edge), the probability mass often redistributes to the
NEIGHBOR bucket(s) after 12 EDT when the afternoon actually unfolds. The
market is over-confident on the peak bucket but the underdogs priced at
20-40¢ are actually more likely than their sticker.

Test: pair the short (buy NO on argmax) with a long on the 2nd or 3rd
favorite. Cost is higher (both legs cost money) but the EV should be
higher too because we collect on both legs if the underdog wins.

Three variants:
    V1: short argmax + long 2nd favorite
    V2: short argmax + long 2nd and 3rd favorites
    V3: short argmax + long every strike with 10¢ ≤ p ≤ 25¢ (the "rising hopefuls")

And compare to the solo short baseline on the same trade set.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 50)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02
SPREAD = 0.03  # half-spread paid on each leg


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
        CREATE OR REPLACE TEMP VIEW nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE range_12 AS
        SELECT nr.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug
               AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p12
        FROM nyc_range nr
    """)

    # Rank strikes per day, with outcome bool
    con.execute("""
        CREATE OR REPLACE TEMP TABLE ranked AS
        SELECT r12.local_day, r12.strike, r12.lo_f, r12.hi_f, r12.p12, md.day_max_whole,
               CASE WHEN md.day_max_whole BETWEEN r12.lo_f AND r12.hi_f THEN 1 ELSE 0 END AS y,
               ROW_NUMBER() OVER (PARTITION BY r12.local_day ORDER BY r12.p12 DESC NULLS LAST) AS rk
        FROM range_12 r12 JOIN metar_daily md ON md.local_date = r12.local_day
        WHERE r12.p12 IS NOT NULL AND md.day_max_whole IS NOT NULL
    """)

    # For each day, get top strikes as one row
    con.execute("""
        CREATE OR REPLACE TEMP TABLE pivot_day AS
        SELECT
            local_day,
            MAX(strike) FILTER (WHERE rk=1) AS fav_strike,
            MAX(p12)    FILTER (WHERE rk=1) AS fav_p,
            MAX(y)      FILTER (WHERE rk=1) AS fav_y,
            MAX(strike) FILTER (WHERE rk=2) AS s2,
            MAX(p12)    FILTER (WHERE rk=2) AS p2,
            MAX(y)      FILTER (WHERE rk=2) AS y2,
            MAX(strike) FILTER (WHERE rk=3) AS s3,
            MAX(p12)    FILTER (WHERE rk=3) AS p3,
            MAX(y)      FILTER (WHERE rk=3) AS y3,
            MAX(strike) FILTER (WHERE rk=4) AS s4,
            MAX(p12)    FILTER (WHERE rk=4) AS p4,
            MAX(y)      FILTER (WHERE rk=4) AS y4,
            COUNT(*) FILTER (WHERE p12 >= 0.10) AS n_over_10c
        FROM ranked
        GROUP BY local_day
    """)

    # Only trade peaked ladders: p_fav >= 0.60 AND n_over_10c <= 2
    con.execute("""
        CREATE OR REPLACE TEMP VIEW peaked AS
        SELECT * FROM pivot_day
        WHERE fav_p >= 0.60 AND n_over_10c <= 2
    """)


def baseline_solo_short(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== BASELINE — SOLO SHORT on peaked-ladder days (no hedge) ===")
    print(con.execute(f"""
        WITH s AS (
            SELECT local_day, fav_strike, fav_p, fav_y,
                   (1 - fav_p + {SPREAD}) * (1 + {FEE}) AS entry_cost
            FROM peaked
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(fav_p), 3) AS avg_fav_p,
            ROUND(AVG(1 - fav_y), 3) AS miss_rate,
            ROUND(AVG((1 - fav_y) / entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT((1 - fav_y) / entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM((1 - fav_y) / entry_cost - 1), 2) AS cum
        FROM s
    """).df())


def variant_1(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== V1 — SHORT argmax + LONG 2nd favorite (peaked-ladder days) ===")
    # Per $1 of TOTAL CAPITAL staked: split between buying (1-fav_p) NO and p2 YES
    # cost = (1-fav_p) + p2, both net of spread/fee
    # payoff: NO pays 1 if fav_y=0. YES on 2nd pays 1 if y2=1.
    # net = ((1 - fav_y) + y2 - cost*1) / cost   ... per $1 invested
    print(con.execute(f"""
        WITH t AS (
            SELECT local_day, fav_p, fav_y, p2, y2,
                   (1 - fav_p + {SPREAD}) * (1 + {FEE}) AS no_cost,
                   (p2 + {SPREAD}) * (1 + {FEE}) AS yes2_cost
            FROM peaked WHERE p2 IS NOT NULL
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(fav_p), 3) AS avg_fav_p,
            ROUND(AVG(p2), 3)    AS avg_p2,
            ROUND(AVG(1 - fav_y), 3) AS fav_miss,
            ROUND(AVG(y2), 3) AS p2_hit,
            ROUND(AVG(((1 - fav_y) + y2) / (no_cost + yes2_cost) - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(((1 - fav_y) + y2) / (no_cost + yes2_cost) - 1, 0.5), 3) AS net_med,
            ROUND(SUM(((1 - fav_y) + y2) / (no_cost + yes2_cost) - 1), 2) AS cum
        FROM t
    """).df())


def variant_2(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== V2 — SHORT argmax + LONG 2nd AND 3rd favorites (peaked-ladder days) ===")
    print(con.execute(f"""
        WITH t AS (
            SELECT local_day, fav_p, fav_y, p2, y2, p3, y3,
                   (1 - fav_p + {SPREAD}) * (1 + {FEE}) AS no_cost,
                   (p2 + {SPREAD}) * (1 + {FEE}) AS yes2_cost,
                   (p3 + {SPREAD}) * (1 + {FEE}) AS yes3_cost
            FROM peaked WHERE p2 IS NOT NULL AND p3 IS NOT NULL
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(((1-fav_y) + y2 + y3) / (no_cost + yes2_cost + yes3_cost) - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(((1-fav_y) + y2 + y3) / (no_cost + yes2_cost + yes3_cost) - 1, 0.5), 3) AS net_med,
            ROUND(SUM(((1-fav_y) + y2 + y3) / (no_cost + yes2_cost + yes3_cost) - 1), 2) AS cum
        FROM t
    """).df())


def variant_3_upward_hedge(con: duckdb.DuckDBPyConnection) -> None:
    """V3: short argmax + long the NEXT HOTTER bucket (lo_f + 2 to lo_f + 3).

    Motivation: the market's errors on peaked-ladder days are systematically
    UPWARD — the actual max lands 2-10°F HIGHER than the favorite bucket. So
    the right hedge is not "2nd most likely by price" (usually lower-temp
    neighbor in range) but "one bucket up in temperature".
    """
    print("\n=== V3 — SHORT argmax + LONG (argmax + 2°F hotter) ===")
    print("    The hotter-neighbor is priced cheaply but wins when the day is warmer than forecast.")
    print(con.execute(f"""
        WITH fav AS (
            SELECT p.local_day, p.fav_strike, p.fav_p, p.fav_y,
                   CAST(regexp_extract(p.fav_strike, '(-?\\d+)-', 1) AS INT) AS fav_lo,
                   CAST(regexp_extract(p.fav_strike, '-(-?\\d+)', 1) AS INT) AS fav_hi
            FROM peaked p
        ),
        hotter AS (
            SELECT f.local_day, f.fav_strike, f.fav_p, f.fav_y,
                   r.strike AS hot_strike,
                   r.p12    AS hot_p,
                   CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS hot_y
            FROM fav f
            JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_hi + 1
            JOIN metar_daily md ON md.local_date = f.local_day
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(fav_p), 3) AS avg_fav_p,
            ROUND(AVG(hot_p), 3) AS avg_hot_p,
            ROUND(AVG(1 - fav_y), 3) AS fav_miss,
            ROUND(AVG(hot_y), 3) AS hot_hit,
            ROUND(AVG(
                ((1 - fav_y) + hot_y) /
                ((1 - fav_p + {SPREAD}) * (1 + {FEE}) + (hot_p + {SPREAD}) * (1 + {FEE}))
                - 1
            ), 3) AS net_avg,
            ROUND(QUANTILE_CONT(
                ((1 - fav_y) + hot_y) /
                ((1 - fav_p + {SPREAD}) * (1 + {FEE}) + (hot_p + {SPREAD}) * (1 + {FEE}))
                - 1
            , 0.5), 3) AS net_med,
            ROUND(SUM(
                ((1 - fav_y) + hot_y) /
                ((1 - fav_p + {SPREAD}) * (1 + {FEE}) + (hot_p + {SPREAD}) * (1 + {FEE}))
                - 1
            ), 2) AS cum
        FROM hotter
    """).df())


def variant_3b_two_hotter(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== V3b — SHORT argmax + LONG (next 2 hotter buckets) ===")
    print(con.execute(f"""
        WITH fav AS (
            SELECT p.local_day, p.fav_strike, p.fav_p, p.fav_y,
                   CAST(regexp_extract(p.fav_strike, '-(-?\\d+)', 1) AS INT) AS fav_hi
            FROM peaked p
        ),
        hot1 AS (
            SELECT f.local_day, r.p12 AS p1,
                   CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y1
            FROM fav f
            JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_hi + 1
            JOIN metar_daily md ON md.local_date = f.local_day
        ),
        hot2 AS (
            SELECT f.local_day, r.p12 AS p2,
                   CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y2
            FROM fav f
            JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_hi + 3
            JOIN metar_daily md ON md.local_date = f.local_day
        ),
        combined AS (
            SELECT f.local_day, f.fav_p, f.fav_y,
                   h1.p1, h1.y1, h2.p2, h2.y2
            FROM fav f
            LEFT JOIN hot1 h1 USING (local_day)
            LEFT JOIN hot2 h2 USING (local_day)
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(fav_p), 3) AS avg_fav_p,
            ROUND(AVG(p1), 3) AS avg_p1,
            ROUND(AVG(p2), 3) AS avg_p2,
            ROUND(AVG(y1), 3) AS p1_hit,
            ROUND(AVG(y2), 3) AS p2_hit,
            ROUND(AVG(
                ((1 - fav_y) + COALESCE(y1,0) + COALESCE(y2,0)) /
                ((1 - fav_p + {SPREAD}) * (1 + {FEE})
                 + COALESCE((p1 + {SPREAD}) * (1 + {FEE}), 0)
                 + COALESCE((p2 + {SPREAD}) * (1 + {FEE}), 0))
                - 1
            ), 3) AS net_avg,
            ROUND(QUANTILE_CONT(
                ((1 - fav_y) + COALESCE(y1,0) + COALESCE(y2,0)) /
                ((1 - fav_p + {SPREAD}) * (1 + {FEE})
                 + COALESCE((p1 + {SPREAD}) * (1 + {FEE}), 0)
                 + COALESCE((p2 + {SPREAD}) * (1 + {FEE}), 0))
                - 1
            , 0.5), 3) AS net_med,
            ROUND(SUM(
                ((1 - fav_y) + COALESCE(y1,0) + COALESCE(y2,0)) /
                ((1 - fav_p + {SPREAD}) * (1 + {FEE})
                 + COALESCE((p1 + {SPREAD}) * (1 + {FEE}), 0)
                 + COALESCE((p2 + {SPREAD}) * (1 + {FEE}), 0))
                - 1
            ), 2) AS cum
        FROM combined
    """).df())


def per_day_detail(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== PER-DAY DETAIL (peaked-ladder days, actual outcomes) ===")
    print(con.execute("""
        SELECT
            p.local_day,
            p.fav_strike, ROUND(p.fav_p,3) AS fav_p, p.fav_y,
            p.s2, ROUND(p.p2,3) AS p2, p.y2,
            p.s3, ROUND(p.p3,3) AS p3, p.y3,
            (SELECT day_max_whole FROM metar_daily WHERE local_date = p.local_day) AS day_max
        FROM peaked p
        ORDER BY p.local_day
    """).df())


def paired_vs_solo_where_2nd_hit(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== HOW OFTEN DOES THE 2nd FAVORITE HIT WHEN THE FAVORITE MISSES? ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_days,
            COUNT(*) FILTER (WHERE fav_y = 0) AS n_fav_miss,
            COUNT(*) FILTER (WHERE fav_y = 0 AND y2 = 1) AS n_2nd_won,
            COUNT(*) FILTER (WHERE fav_y = 0 AND y3 = 1) AS n_3rd_won,
            COUNT(*) FILTER (WHERE fav_y = 0 AND y2 = 0 AND y3 = 0) AS n_neither_top3_won,
            ROUND(COUNT(*) FILTER (WHERE fav_y = 0 AND y2 = 1)::FLOAT /
                  NULLIF(COUNT(*) FILTER (WHERE fav_y = 0), 0), 3) AS pct_2nd_given_miss
        FROM peaked
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    baseline_solo_short(con)
    variant_1(con)
    variant_2(con)
    variant_3_upward_hedge(con)
    variant_3b_two_hotter(con)
    paired_vs_solo_where_2nd_hit(con)
    per_day_detail(con)


if __name__ == "__main__":
    main()

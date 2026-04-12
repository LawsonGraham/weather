"""Experiment 07 — Ladder shape as a filter on the fade strategy.

Exp05 showed the fade-morning-favorite strategy has big outlier concentration
(top 5 trades = 85% of cum PnL). Hypothesis: ladders where the favorite is
a single sharp peak (narrow, highly confident market) are the days where the
market is most over-committed and most exploitable. Ladders where the price
mass is spread across 4-5 strikes are days where the market is honestly
uncertain, and fading the (weak) argmax is noise.

Test: for each day, compute shape metrics on the range-strike ladder at 12 EDT:
    • entropy           — entropy of normalized price distribution across strikes
    • max_over_second   — ratio of argmax to 2nd highest
    • num_over_10c      — count of strikes priced > 10¢
    • concentration     — sum(p^2) / sum(p)^2 (Herfindahl-style)

Then segment fade returns by each metric and see if one separates profitable
from unprofitable fades.
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

    # 12 EDT price per range strike
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE range_12 AS
        SELECT nr.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug
               AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p12
        FROM nyc_range nr
    """)

    # Per-day ladder shape
    con.execute("""
        CREATE OR REPLACE TEMP TABLE ladder_shape AS
        WITH norm AS (
            SELECT local_day, strike, p12,
                   p12 / NULLIF(SUM(p12) OVER (PARTITION BY local_day), 0) AS pn
            FROM range_12
            WHERE p12 IS NOT NULL
        )
        SELECT
            local_day,
            COUNT(*) AS n_strikes,
            SUM(p12) AS range_sum,
            ROUND(MAX(p12), 3) AS p_max,
            -- Shannon entropy of the normalized distribution (log base 2)
            ROUND(-SUM(pn * LOG2(pn + 1e-12)), 3) AS entropy_bits,
            -- Herfindahl-style concentration = sum(pn^2)
            ROUND(SUM(pn*pn), 3) AS herfindahl,
            -- Number of strikes above 10 cents
            COUNT(*) FILTER (WHERE p12 >= 0.10) AS n_over_10c,
            -- Ratio of argmax to second place (peakedness)
            MAX(p12) AS max_p12,
            ROUND(MAX(p12) / NULLIF((
                SELECT MAX(p12) FROM range_12 r2
                WHERE r2.local_day = norm.local_day AND r2.p12 < (SELECT MAX(p12) FROM range_12 r3 WHERE r3.local_day = norm.local_day)
            ), 0), 3) AS peak_ratio
        FROM norm
        GROUP BY local_day
    """)

    # Fav trade data joined with shape + outcome
    con.execute("""
        CREATE OR REPLACE TEMP TABLE fav_trades_shape AS
        WITH favs AS (
            SELECT local_day,
                   arg_max(strike, p12) AS strike,
                   max(p12)             AS p_fav,
                   arg_max(lo_f, p12)   AS lo_f,
                   arg_max(hi_f, p12)   AS hi_f
            FROM range_12 WHERE p12 IS NOT NULL
            GROUP BY 1
        )
        SELECT
            f.*, ls.n_strikes, ls.range_sum, ls.entropy_bits, ls.herfindahl,
            ls.n_over_10c, ls.peak_ratio, md.day_max_whole,
            CASE WHEN md.day_max_whole BETWEEN f.lo_f AND f.hi_f THEN 1 ELSE 0 END AS y,
            -- Net return with 3c spread + 2% fee
            (1.0 - f.p_fav + 0.03) * 1.02 AS entry_cost
        FROM favs f
        JOIN ladder_shape ls ON ls.local_day = f.local_day
        JOIN metar_daily md ON md.local_date = f.local_day
        WHERE md.day_max_whole IS NOT NULL
          AND (1.0 - f.p_fav + 0.03) < 0.99
    """)


def shape_overview(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== LADDER SHAPE STATS (n=days) ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(n_strikes), 2) AS avg_n_strikes,
            ROUND(AVG(entropy_bits), 3) AS avg_entropy,
            ROUND(MIN(entropy_bits), 3) AS min_entropy,
            ROUND(MAX(entropy_bits), 3) AS max_entropy,
            ROUND(AVG(herfindahl), 3) AS avg_herf,
            ROUND(AVG(n_over_10c), 2) AS avg_n_10c,
            ROUND(AVG(peak_ratio), 3) AS avg_peak_ratio
        FROM fav_trades_shape
    """).df())


def segment_by_entropy(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FADE NET RETURN BY ENTROPY TERCILE ===")
    print("    Lower entropy = more peaked ladder = market more confident.")
    print("    Hypothesis: lower entropy = higher miss rate = better fade.")
    print(con.execute("""
        WITH q AS (
            SELECT NTILE(3) OVER (ORDER BY entropy_bits) AS tercile,
                   entropy_bits, p_fav, y, entry_cost
            FROM fav_trades_shape
        )
        SELECT tercile,
               COUNT(*) AS n,
               ROUND(AVG(entropy_bits), 3) AS avg_entropy,
               ROUND(AVG(p_fav), 3) AS avg_p_fav,
               ROUND(AVG(1-y), 3) AS miss_rate,
               ROUND(AVG((1-y)/entry_cost - 1), 3) AS net_avg_ret,
               ROUND(QUANTILE_CONT((1-y)/entry_cost - 1, 0.5), 3) AS net_med_ret,
               ROUND(SUM((1-y)/entry_cost - 1), 2) AS cum_pnl
        FROM q GROUP BY tercile ORDER BY tercile
    """).df())


def segment_by_herfindahl(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FADE NET RETURN BY HERFINDAHL TERCILE ===")
    print("    Higher herfindahl = more concentrated (one or two strikes dominate).")
    print(con.execute("""
        WITH q AS (
            SELECT NTILE(3) OVER (ORDER BY herfindahl) AS tercile,
                   herfindahl, p_fav, y, entry_cost
            FROM fav_trades_shape
        )
        SELECT tercile,
               COUNT(*) AS n,
               ROUND(AVG(herfindahl), 3) AS avg_herf,
               ROUND(AVG(p_fav), 3) AS avg_p_fav,
               ROUND(AVG(1-y), 3) AS miss_rate,
               ROUND(AVG((1-y)/entry_cost - 1), 3) AS net_avg_ret,
               ROUND(QUANTILE_CONT((1-y)/entry_cost - 1, 0.5), 3) AS net_med_ret,
               ROUND(SUM((1-y)/entry_cost - 1), 2) AS cum_pnl
        FROM q GROUP BY tercile ORDER BY tercile
    """).df())


def segment_by_p_fav(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FADE NET RETURN BY p_fav BAND ===")
    print("    Simplest filter: is the favorite's price itself a filter?")
    print(con.execute("""
        SELECT
            CASE
                WHEN p_fav < 0.25 THEN '1:[0-25c)'
                WHEN p_fav < 0.40 THEN '2:[25-40c)'
                WHEN p_fav < 0.60 THEN '3:[40-60c)'
                WHEN p_fav < 0.80 THEN '4:[60-80c)'
                ELSE '5:[80c+)'
            END AS band,
            COUNT(*) AS n,
            ROUND(AVG(p_fav), 3) AS avg_p,
            ROUND(AVG(1-y), 3) AS miss_rate,
            ROUND(AVG((1-y)/entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT((1-y)/entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM((1-y)/entry_cost - 1), 2) AS cum_pnl
        FROM fav_trades_shape
        GROUP BY band ORDER BY band
    """).df())


def segment_by_n_over_10c(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FADE NET RETURN BY COUNT OF STRIKES PRICED ≥10¢ ===")
    print("    Fewer non-trivial strikes = tighter distribution = potentially more fadeable.")
    print(con.execute("""
        SELECT n_over_10c,
               COUNT(*) AS n,
               ROUND(AVG(p_fav), 3) AS avg_p,
               ROUND(AVG(1-y), 3) AS miss_rate,
               ROUND(AVG((1-y)/entry_cost - 1), 3) AS net_avg,
               ROUND(QUANTILE_CONT((1-y)/entry_cost - 1, 0.5), 3) AS net_med
        FROM fav_trades_shape
        GROUP BY n_over_10c ORDER BY n_over_10c
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    shape_overview(con)
    segment_by_entropy(con)
    segment_by_herfindahl(con)
    segment_by_p_fav(con)
    segment_by_n_over_10c(con)


if __name__ == "__main__":
    main()

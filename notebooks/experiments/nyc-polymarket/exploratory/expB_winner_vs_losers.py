"""Exploratory B — how does the winning bucket's 1-min path differ from losers?

april-10 is the only resolved day we have 1-min data for. From markets.parquet,
the winner was 58-59°F. This script inspects the 1-min price paths of all 11
buckets through the last ~24 h before resolution and answers:

  1. When did the eventual winner first become the favorite?
  2. How much did the favorite drift before resolution?
  3. How big was the "last move" as resolution approached?
  4. Did any bucket have an information-rich jump that preceded the winner
     becoming dominant (i.e. did a smart trader move the market)?

This is pure observation of structure, not a strategy backtest — aims to build
intuition about how these markets actually behave in the final 24 h.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)


MIN1 = "data/processed/polymarket_prices_history/min1/**/*.parquet"
DAY = "april-10-2026"
WINNER = "58-59f"   # from markets.parquet outcome_prices


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW d AS
        SELECT DISTINCT ON (slug, date_trunc('minute', timestamp))
               slug, p_yes,
               date_trunc('minute', timestamp) AS mt,
               regexp_extract(slug, 'nyc-on-[a-z]+-[0-9]+-[0-9]+-(.+)', 1) AS strike
        FROM '{MIN1}'
        WHERE slug ILIKE '%{DAY}%'
        ORDER BY slug, date_trunc('minute', timestamp), timestamp
    """)

    print(f"=== per-bucket coverage for {DAY} ===")
    print(con.execute("""
        SELECT strike, COUNT(*) AS n_min, MIN(mt) AS first, MAX(mt) AS last,
               ROUND(MIN(p_yes),3) AS min_p, ROUND(MAX(p_yes),3) AS max_p,
               ROUND(AVG(p_yes),3) AS avg_p
        FROM d GROUP BY 1 ORDER BY strike
    """).df())

    print("\n=== pivoted: price path of each bucket over time (hourly sample) ===")
    df_pivot = con.execute("""
        SELECT date_trunc('hour', mt) AS h,
               ROUND(AVG(CASE WHEN strike = '55forbelow' THEN p_yes END), 3) AS "<=55",
               ROUND(AVG(CASE WHEN strike = '56-57f' THEN p_yes END), 3) AS "56-57",
               ROUND(AVG(CASE WHEN strike = '58-59f' THEN p_yes END), 3) AS "58-59★",
               ROUND(AVG(CASE WHEN strike = '60-61f' THEN p_yes END), 3) AS "60-61",
               ROUND(AVG(CASE WHEN strike = '62-63f' THEN p_yes END), 3) AS "62-63",
               ROUND(AVG(CASE WHEN strike = '64-65f' THEN p_yes END), 3) AS "64-65",
               ROUND(AVG(CASE WHEN strike = '66-67f' THEN p_yes END), 3) AS "66-67",
               ROUND(AVG(CASE WHEN strike = '68-69f' THEN p_yes END), 3) AS "68-69",
               ROUND(AVG(CASE WHEN strike = '70-71f' THEN p_yes END), 3) AS "70-71",
               ROUND(AVG(CASE WHEN strike = '72-73f' THEN p_yes END), 3) AS "72-73",
               ROUND(AVG(CASE WHEN strike = '74forhigher' THEN p_yes END), 3) AS ">=74"
        FROM d GROUP BY 1 ORDER BY 1
    """).df()
    print(df_pivot.to_string(index=False))

    print("\n=== winner's intraday path (last 12 hours) — minute-level ===")
    print(con.execute(f"""
        SELECT mt, ROUND(p_yes, 3) AS p_winner
        FROM d
        WHERE strike = '{WINNER}' AND mt >= '2026-04-10 15:00:00+00:00'
        ORDER BY mt
        LIMIT 40
    """).df())

    print("\n=== current favorite at each hour (biggest p_yes) ===")
    print(con.execute("""
        WITH h AS (
            SELECT date_trunc('hour', mt) AS hr, strike, AVG(p_yes) AS p
            FROM d GROUP BY 1, 2
        )
        SELECT hr,
               arg_max(strike, p) AS favorite,
               ROUND(MAX(p), 3) AS fav_p,
               ROUND(SUM(p), 3) AS ladder_sum
        FROM h GROUP BY 1 ORDER BY 1
    """).df())

    print("\n=== biggest 1-min move of the winner ===")
    print(con.execute(f"""
        WITH w AS (
            SELECT mt, p_yes,
                   LAG(p_yes) OVER (ORDER BY mt) AS prev_p
            FROM d WHERE strike = '{WINNER}'
        )
        SELECT mt, ROUND(prev_p, 3) AS before, ROUND(p_yes, 3) AS after,
               ROUND(p_yes - prev_p, 3) AS delta
        FROM w
        WHERE prev_p IS NOT NULL
        ORDER BY ABS(p_yes - prev_p) DESC
        LIMIT 15
    """).df())


if __name__ == "__main__":
    main()

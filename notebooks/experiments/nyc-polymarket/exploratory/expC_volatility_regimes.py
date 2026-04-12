"""Exploratory C — characterize the volatility regime of 1-min price data.

For each of the 4 days we have 1-min data for (april-10 resolved, april-11
resolving today, april-12 + april-13 still ~2 days out), compute:

  - Volatility per bucket: stddev of 1-min returns
  - Favorite's move magnitude: how far did the favorite drift over the day
  - Average 1-min |Δp| for the top 3 buckets
  - Which hour of day has the most 1-min movement

Goal: understand where the information is in the 1-min data. Which days
are active, which hours, and is any bucket actually worth watching
minute-by-minute vs just polling once an hour?
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)

MIN1 = "data/processed/polymarket_prices_history/min1/**/*.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW d AS
        SELECT DISTINCT ON (slug, date_trunc('minute', timestamp))
               slug, p_yes,
               date_trunc('minute', timestamp) AS mt,
               regexp_extract(slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md,
               regexp_extract(slug, 'nyc-on-[a-z]+-[0-9]+-[0-9]+-(.+)', 1) AS strike
        FROM '{MIN1}'
        ORDER BY slug, date_trunc('minute', timestamp), timestamp
    """)

    print("=== total 1-min movement per day (sum of |Δp| across all buckets) ===")
    print(con.execute("""
        WITH steps AS (
            SELECT md, mt, strike, p_yes,
                   LAG(p_yes) OVER (PARTITION BY slug ORDER BY mt) AS prev_p
            FROM d
        )
        SELECT md,
               COUNT(*) AS n_steps,
               ROUND(SUM(ABS(p_yes - prev_p)), 2) AS total_abs_delta,
               ROUND(AVG(ABS(p_yes - prev_p)) * 10000, 2) AS avg_bp_per_min
        FROM steps
        WHERE prev_p IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).df())

    print("\n=== per-bucket volatility within each day (stddev of 1-min moves) ===")
    print(con.execute("""
        WITH steps AS (
            SELECT md, strike, p_yes,
                   LAG(p_yes) OVER (PARTITION BY slug ORDER BY mt) AS prev_p
            FROM d
        )
        SELECT md, strike,
               COUNT(*) AS n,
               ROUND(AVG(p_yes), 3) AS avg_p,
               ROUND(STDDEV(p_yes - prev_p) * 10000, 2) AS std_bp_per_min,
               ROUND(MAX(ABS(p_yes - prev_p)) * 10000, 2) AS max_bp_delta
        FROM steps
        WHERE prev_p IS NOT NULL
        GROUP BY 1, 2
        ORDER BY md, std_bp_per_min DESC
    """).df().to_string(index=False))

    print("\n=== hour-of-day vs total movement (april-11 + april-12 + april-13 open markets) ===")
    print(con.execute("""
        WITH steps AS (
            SELECT md, strike,
                   EXTRACT(hour FROM mt) AS hr_utc,
                   p_yes,
                   LAG(p_yes) OVER (PARTITION BY slug ORDER BY mt) AS prev_p
            FROM d
            WHERE md IN ('april-11-2026', 'april-12-2026', 'april-13-2026')
        )
        SELECT hr_utc,
               ROUND(SUM(ABS(p_yes - prev_p)), 2) AS total_delta,
               ROUND(AVG(ABS(p_yes - prev_p)) * 10000, 2) AS avg_bp_per_step,
               COUNT(*) AS n
        FROM steps WHERE prev_p IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).df().to_string(index=False))

    print("\n=== top favorite per day (last known snapshot) ===")
    print(con.execute("""
        WITH latest AS (
            SELECT md, strike, p_yes, mt,
                   ROW_NUMBER() OVER (PARTITION BY md, strike ORDER BY mt DESC) AS rn
            FROM d
        ),
        last_state AS (
            SELECT md, strike, p_yes FROM latest WHERE rn = 1
        )
        SELECT md,
               arg_max(strike, p_yes) AS favorite,
               ROUND(MAX(p_yes), 3) AS fav_p,
               ROUND(QUANTILE_CONT(p_yes, 0.5), 3) AS median_p,
               ROUND(SUM(p_yes), 3) AS ladder_sum
        FROM last_state
        GROUP BY 1 ORDER BY 1
    """).df())

    print("\n=== biggest single 1-min moves across all days and buckets (>=2c) ===")
    print(con.execute("""
        WITH steps AS (
            SELECT md, strike, mt, p_yes,
                   LAG(p_yes) OVER (PARTITION BY slug ORDER BY mt) AS prev_p
            FROM d
        )
        SELECT mt, md, strike,
               ROUND(prev_p, 3) AS from_p,
               ROUND(p_yes, 3) AS to_p,
               ROUND(p_yes - prev_p, 3) AS delta
        FROM steps
        WHERE prev_p IS NOT NULL
          AND ABS(p_yes - prev_p) >= 0.02
        ORDER BY ABS(p_yes - prev_p) DESC
        LIMIT 20
    """).df())


if __name__ == "__main__":
    main()

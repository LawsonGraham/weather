"""Exploratory A — does the NYC daily-temp ladder sum to 1.0 or is there free arb?

Each market day has ~11 mutually-exclusive buckets. The YES token of each
bucket pays $1 if the day's LGA max lands in that bucket, else $0. By no-
arbitrage, the sum of YES midpoints across all buckets of a single day
should equal 1.0 at every instant.

If it drifts away, there's a free basket trade (buy-all-if-sum<1, sell-all
if sum>1). This script plots the distribution of sum-across-ladder across
every 1-min snapshot we have and flags the biggest deviations.

Uses data/processed/polymarket_prices_history/min1/ only.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)
pd.set_option("display.max_rows", 60)

MIN1 = "data/processed/polymarket_prices_history/min1/**/*.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # The raw /prices-history endpoint occasionally emits duplicate points
    # at the same second (observed ~1 dup per 1439-pt series). Dedup to one
    # row per (slug, minute_ts) before aggregating. Also map slug → market_date.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW min1_tagged AS
        SELECT DISTINCT ON (slug, date_trunc('minute', timestamp))
            timestamp,
            slug,
            p_yes,
            regexp_extract(slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS market_date,
            date_trunc('minute', timestamp) AS minute_ts
        FROM '{MIN1}'
        ORDER BY slug, date_trunc('minute', timestamp), timestamp
    """)

    print("=== per-minute ladder sum, grouped by market_date ===")
    df = con.execute("""
        WITH agg AS (
            SELECT
                market_date,
                minute_ts,
                COUNT(DISTINCT slug) AS n_buckets,
                SUM(p_yes) AS ladder_sum
            FROM min1_tagged
            GROUP BY 1, 2
        )
        SELECT
            market_date,
            COUNT(*) AS n_snapshots,
            MIN(n_buckets) AS min_buckets,
            MAX(n_buckets) AS max_buckets,
            ROUND(AVG(ladder_sum), 4) AS avg_sum,
            ROUND(MIN(ladder_sum), 4) AS min_sum,
            ROUND(MAX(ladder_sum), 4) AS max_sum,
            ROUND(STDDEV(ladder_sum), 4) AS std_sum
        FROM agg
        WHERE n_buckets >= 8    -- require most buckets present
        GROUP BY 1
        ORDER BY 1
    """).df()
    print(df.to_string(index=False))

    print("\n=== full distribution of ladder_sum (all days, all complete snapshots) ===")
    df2 = con.execute("""
        WITH agg AS (
            SELECT
                market_date, minute_ts,
                COUNT(DISTINCT slug) AS n_buckets,
                SUM(p_yes) AS ladder_sum
            FROM min1_tagged
            GROUP BY 1, 2
        )
        SELECT
            ROUND(AVG(ladder_sum), 4) AS mean_sum,
            ROUND(QUANTILE_CONT(ladder_sum, 0.01), 4) AS p01,
            ROUND(QUANTILE_CONT(ladder_sum, 0.05), 4) AS p05,
            ROUND(QUANTILE_CONT(ladder_sum, 0.25), 4) AS p25,
            ROUND(QUANTILE_CONT(ladder_sum, 0.50), 4) AS p50,
            ROUND(QUANTILE_CONT(ladder_sum, 0.75), 4) AS p75,
            ROUND(QUANTILE_CONT(ladder_sum, 0.95), 4) AS p95,
            ROUND(QUANTILE_CONT(ladder_sum, 0.99), 4) AS p99,
            COUNT(*) AS n
        FROM agg
        WHERE n_buckets >= 10
    """).df()
    print(df2.to_string(index=False))

    print("\n=== biggest deviations from 1.0 (complete ladders only) ===")
    df3 = con.execute("""
        WITH agg AS (
            SELECT market_date, minute_ts,
                   COUNT(DISTINCT slug) AS n_buckets,
                   SUM(p_yes) AS ladder_sum
            FROM min1_tagged
            GROUP BY 1, 2
        )
        SELECT *, ROUND(ABS(ladder_sum - 1.0), 4) AS abs_dev
        FROM agg
        WHERE n_buckets >= 10
        ORDER BY abs_dev DESC
        LIMIT 25
    """).df()
    print(df3.to_string(index=False))

    print("\n=== how long does a >2c deviation persist? (looking for arb depth) ===")
    df4 = con.execute("""
        WITH agg AS (
            SELECT market_date, minute_ts,
                   COUNT(DISTINCT slug) AS n_buckets,
                   SUM(p_yes) AS ladder_sum
            FROM min1_tagged
            GROUP BY 1, 2
        ),
        deviations AS (
            SELECT *, ABS(ladder_sum - 1.0) AS dev
            FROM agg
            WHERE n_buckets >= 10
        )
        SELECT
            CASE
                WHEN dev < 0.005 THEN '< 0.5c'
                WHEN dev < 0.01  THEN '0.5-1c'
                WHEN dev < 0.02  THEN '1-2c'
                WHEN dev < 0.05  THEN '2-5c'
                WHEN dev < 0.10  THEN '5-10c'
                ELSE '> 10c'
            END AS dev_bucket,
            COUNT(*) AS n_snapshots,
            ROUND(AVG(ladder_sum), 4) AS avg_sum
        FROM deviations
        GROUP BY 1
        ORDER BY 1
    """).df()
    print(df4.to_string(index=False))

    print("\n=== by side: is sum > 1 (over-pricing) or < 1 (under-pricing) more common? ===")
    df5 = con.execute("""
        WITH agg AS (
            SELECT market_date, minute_ts,
                   COUNT(DISTINCT slug) AS n_buckets,
                   SUM(p_yes) AS ladder_sum
            FROM min1_tagged
            GROUP BY 1, 2
        )
        SELECT
            CASE WHEN ladder_sum > 1.01 THEN 'OVER (> 1.01)'
                 WHEN ladder_sum < 0.99 THEN 'UNDER (< 0.99)'
                 ELSE 'flat' END AS side,
            COUNT(*) AS n,
            ROUND(AVG(ladder_sum), 4) AS avg
        FROM agg
        WHERE n_buckets >= 10
        GROUP BY 1
        ORDER BY n DESC
    """).df()
    print(df5.to_string(index=False))


if __name__ == "__main__":
    main()

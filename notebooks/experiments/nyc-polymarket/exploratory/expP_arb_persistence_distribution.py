"""Exploratory P — persistence distribution across all observed arbs.

Walk the tob parquet and compute, for every second where sum_bid
crossed 1.005 with a fresh 11-bucket ladder, the DURATION of that
window (consecutive seconds above 1.005 before falling back below).

Histogram the durations → answer "what does a typical arb-window look
like?" Is 50 seconds normal or a rare long-tail event?
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)

TOB = "data/processed/polymarket_book/tob/**/*.parquet"
MARKETS = "data/processed/polymarket_weather/markets.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # Build the per-second ladder sum, for every market-date
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW yes_tob AS
        SELECT date_trunc('second', t.received_at) AS sec,
               t.slug, t.best_bid, t.received_at,
               regexp_extract(t.slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md
        FROM '{TOB}' t
        INNER JOIN '{MARKETS}' m
          ON m.slug = t.slug AND m.yes_token_id = t.asset_id
        WHERE m.city='New York City' AND t.best_bid IS NOT NULL
    """)

    con.execute("""
        CREATE OR REPLACE TEMP VIEW latest AS
        SELECT DISTINCT ON (md, sec, slug) md, sec, slug, best_bid
        FROM yes_tob
        ORDER BY md, sec, slug, received_at DESC
    """)

    # Build a per-md per-sec cross product + ASOF join to get fresh state
    con.execute("""
        CREATE OR REPLACE TEMP VIEW grid AS
        WITH secs AS (SELECT DISTINCT md, sec FROM latest),
             slugs AS (SELECT DISTINCT md, slug FROM latest)
        SELECT s.md, s.sec, sl.slug
        FROM secs s JOIN slugs sl ON sl.md = s.md
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW state AS
        SELECT g.md, g.sec, g.slug, l.best_bid
        FROM grid g
        ASOF LEFT JOIN latest l
          ON l.md = g.md AND l.slug = g.slug AND l.sec <= g.sec
    """)

    con.execute("""
        CREATE OR REPLACE TEMP VIEW agg AS
        SELECT md, sec,
               COUNT(best_bid) AS n_fresh,
               SUM(best_bid) AS sum_bid
        FROM state GROUP BY 1, 2
    """)

    # Identify arb-open seconds: n_fresh >= 10 AND sum_bid > 1.005
    # Group consecutive arb seconds into "events" (gaps of >= 2 seconds break a window)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW arb_secs AS
        SELECT md, sec, sum_bid
        FROM agg
        WHERE n_fresh >= 10 AND sum_bid > 1.005
    """)

    # Use gap-based windowing: a run breaks when the gap to the previous
    # arb sec is > 2 seconds (allowing for 1-sec drop artifacts)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW windows AS
        WITH ranked AS (
            SELECT md, sec, sum_bid,
                   LAG(sec) OVER (PARTITION BY md ORDER BY sec) AS prev_sec
            FROM arb_secs
        ),
        flagged AS (
            SELECT md, sec, sum_bid,
                   CASE WHEN prev_sec IS NULL OR DATE_DIFF('second', prev_sec, sec) > 2
                        THEN 1 ELSE 0 END AS new_window
            FROM ranked
        ),
        grouped AS (
            SELECT md, sec, sum_bid,
                   SUM(new_window) OVER (PARTITION BY md ORDER BY sec) AS window_id
            FROM flagged
        )
        SELECT md, window_id,
               MIN(sec) AS t0,
               MAX(sec) AS t1,
               DATE_DIFF('second', MIN(sec), MAX(sec)) + 1 AS duration_sec,
               MAX(sum_bid) AS peak_sum,
               AVG(sum_bid) AS avg_sum,
               COUNT(*) AS n_observed_secs
        FROM grouped
        GROUP BY md, window_id
    """)

    print("=== arb window summary (all markets, all time) ===")
    print(con.execute("""
        SELECT md,
               COUNT(*) AS n_windows,
               ROUND(AVG(duration_sec), 1) AS avg_dur,
               ROUND(QUANTILE_CONT(duration_sec, 0.5), 1) AS p50_dur,
               ROUND(QUANTILE_CONT(duration_sec, 0.90), 1) AS p90_dur,
               MAX(duration_sec) AS max_dur,
               ROUND(AVG(peak_sum), 4) AS avg_peak,
               ROUND(MAX(peak_sum), 4) AS max_peak
        FROM windows
        GROUP BY md ORDER BY md
    """).df())

    print("\n=== duration distribution ===")
    print(con.execute("""
        SELECT
            CASE
                WHEN duration_sec <= 1 THEN '01: 1s'
                WHEN duration_sec <= 3 THEN '02: 2-3s'
                WHEN duration_sec <= 5 THEN '03: 4-5s'
                WHEN duration_sec <= 10 THEN '04: 6-10s'
                WHEN duration_sec <= 20 THEN '05: 11-20s'
                WHEN duration_sec <= 40 THEN '06: 21-40s'
                ELSE '07: 40s+'
            END AS bucket,
            COUNT(*) AS n,
            ROUND(AVG(peak_sum), 4) AS avg_peak
        FROM windows
        GROUP BY 1 ORDER BY 1
    """).df())

    print("\n=== top 15 longest arb windows ===")
    print(con.execute("""
        SELECT md, t0, t1, duration_sec,
               ROUND(peak_sum, 4) AS peak,
               ROUND(avg_sum, 4) AS avg_sum
        FROM windows
        ORDER BY duration_sec DESC
        LIMIT 15
    """).df())

    # Also: rate estimate — arbs per hour
    print("\n=== rate estimate: arb-seconds per hour by market-date ===")
    print(con.execute("""
        WITH per_hour AS (
            SELECT md, date_trunc('hour', sec) AS hr, SUM(sum_bid > 1.005)::INT AS n_arb_sec,
                   COUNT(*) AS n_sec
            FROM (SELECT md, sec, sum_bid FROM agg WHERE n_fresh >= 10)
            GROUP BY 1, 2
        )
        SELECT md, hr, n_sec, n_arb_sec,
               ROUND(100.0 * n_arb_sec / NULLIF(n_sec, 0), 1) AS pct_arb
        FROM per_hour
        WHERE n_arb_sec > 0
        ORDER BY hr, md
    """).df())


if __name__ == "__main__":
    main()

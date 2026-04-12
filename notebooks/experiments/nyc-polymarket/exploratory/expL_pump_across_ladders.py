"""Exploratory L — characterize the pre-16-EDT pump across every ladder bucket.

Exp K found that the april-11 +2 bucket (64-65f) pumped 2x between
15:24 and 16:00 EDT. Hypothesis: this is flow piling into specific
buckets on active days. Question: does the pump appear only on the +2
bucket, or is it a general "active bucket" phenomenon?

For every YES-token top-of-book stream we have in tob, trace the
minute-level path from 19:24 UTC to 20:05 UTC (=15:24 to 16:05 EDT)
and compute:

  1. avg mid at 15:24-15:30 EDT ("pre-window start")
  2. avg mid at 15:55-16:00 EDT ("at entry")
  3. peak mid between 15:24 and 16:00 EDT
  4. the "pump magnitude" = (at_entry - pre_window) / pre_window

Classify buckets by their pump magnitude. Look for the active region
that pumps.
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

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW yes_tob AS
        WITH ym AS (
            SELECT slug, yes_token_id
            FROM '{MARKETS}'
            WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%'
        )
        SELECT t.received_at, t.slug, t.best_bid, t.best_ask, t.mid,
               regexp_extract(t.slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md,
               regexp_extract(t.slug, 'nyc-on-[a-z]+-[0-9]+-[0-9]+-(.+)', 1) AS strike,
               CASE WHEN strike LIKE '%forbelow'  THEN -999
                    WHEN strike LIKE '%forhigher' THEN CAST(regexp_extract(strike, '([0-9]+)', 1) AS INT)
                    ELSE CAST(regexp_extract(strike, '([0-9]+)-', 1) AS INT) END AS lo_f
        FROM '{TOB}' t
        INNER JOIN ym ON ym.slug = t.slug AND ym.yes_token_id = t.asset_id
        WHERE t.mid IS NOT NULL AND t.best_ask IS NOT NULL
    """)

    # Aggregate by minute for tractability
    con.execute("""
        CREATE OR REPLACE TEMP VIEW per_min AS
        SELECT md, slug, lo_f,
               date_trunc('minute', received_at) AS mt,
               AVG(mid) AS avg_mid,
               AVG(best_ask) AS avg_ask,
               AVG(best_bid) AS avg_bid
        FROM yes_tob
        GROUP BY 1, 2, 3, 4
    """)

    # Phase windows — pre-entry (15:24-15:35 EDT = 19:24-19:35 UTC),
    # pre-pump (15:35-15:45 = 19:35-19:45), entry (15:55-16:00 = 19:55-20:00),
    # post-entry (16:00-16:05 = 20:00-20:05)
    print("=== per-slug phase summary: april-11 (active day) ===")
    print(con.execute("""
        WITH phases AS (
            SELECT md, slug, lo_f,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 19:24:00+00:00' AND '2026-04-11 19:35:00+00:00'
                            THEN avg_mid END) AS pre_mid,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 19:55:00+00:00' AND '2026-04-11 20:00:00+00:00'
                            THEN avg_mid END) AS entry_mid,
                   MAX(CASE WHEN mt BETWEEN '2026-04-11 19:24:00+00:00' AND '2026-04-11 20:05:00+00:00'
                            THEN avg_mid END) AS peak_mid,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 20:20:00+00:00' AND '2026-04-11 20:30:00+00:00'
                            THEN avg_mid END) AS post20_mid
            FROM per_min
            WHERE md = 'april-11-2026'
            GROUP BY 1, 2, 3
        )
        SELECT lo_f,
               ROUND(pre_mid, 3) AS pre,
               ROUND(entry_mid, 3) AS entry,
               ROUND(peak_mid, 3) AS peak,
               ROUND(post20_mid, 3) AS post_20min,
               ROUND(entry_mid - pre_mid, 3) AS delta_entry,
               ROUND((entry_mid - pre_mid) / NULLIF(pre_mid, 0), 2) AS pct_entry,
               ROUND(post20_mid - entry_mid, 3) AS delta_post
        FROM phases
        WHERE pre_mid IS NOT NULL AND entry_mid IS NOT NULL
        ORDER BY lo_f
    """).df())

    print("\n=== per-slug phase summary: april-12 (tomorrow's market) ===")
    print(con.execute("""
        WITH phases AS (
            SELECT md, slug, lo_f,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 19:24:00+00:00' AND '2026-04-11 19:35:00+00:00'
                            THEN avg_mid END) AS pre_mid,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 19:55:00+00:00' AND '2026-04-11 20:00:00+00:00'
                            THEN avg_mid END) AS entry_mid,
                   MAX(CASE WHEN mt BETWEEN '2026-04-11 19:24:00+00:00' AND '2026-04-11 20:05:00+00:00'
                            THEN avg_mid END) AS peak_mid,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 20:20:00+00:00' AND '2026-04-11 20:30:00+00:00'
                            THEN avg_mid END) AS post20_mid
            FROM per_min
            WHERE md = 'april-12-2026'
            GROUP BY 1, 2, 3
        )
        SELECT lo_f,
               ROUND(pre_mid, 3) AS pre,
               ROUND(entry_mid, 3) AS entry,
               ROUND(peak_mid, 3) AS peak,
               ROUND(post20_mid, 3) AS post_20min,
               ROUND(entry_mid - pre_mid, 3) AS delta_entry,
               ROUND((entry_mid - pre_mid) / NULLIF(pre_mid, 0), 2) AS pct_entry,
               ROUND(post20_mid - entry_mid, 3) AS delta_post
        FROM phases
        WHERE pre_mid IS NOT NULL AND entry_mid IS NOT NULL
        ORDER BY lo_f
    """).df())

    print("\n=== april-13 (day-after-tomorrow market) ===")
    print(con.execute("""
        WITH phases AS (
            SELECT md, slug, lo_f,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 19:24:00+00:00' AND '2026-04-11 19:35:00+00:00'
                            THEN avg_mid END) AS pre_mid,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 19:55:00+00:00' AND '2026-04-11 20:00:00+00:00'
                            THEN avg_mid END) AS entry_mid,
                   MAX(CASE WHEN mt BETWEEN '2026-04-11 19:24:00+00:00' AND '2026-04-11 20:05:00+00:00'
                            THEN avg_mid END) AS peak_mid,
                   AVG(CASE WHEN mt BETWEEN '2026-04-11 20:20:00+00:00' AND '2026-04-11 20:30:00+00:00'
                            THEN avg_mid END) AS post20_mid
            FROM per_min
            WHERE md = 'april-13-2026'
            GROUP BY 1, 2, 3
        )
        SELECT lo_f,
               ROUND(pre_mid, 3) AS pre,
               ROUND(entry_mid, 3) AS entry,
               ROUND(peak_mid, 3) AS peak,
               ROUND(post20_mid, 3) AS post_20min,
               ROUND(entry_mid - pre_mid, 3) AS delta_entry,
               ROUND((entry_mid - pre_mid) / NULLIF(pre_mid, 0), 2) AS pct_entry,
               ROUND(post20_mid - entry_mid, 3) AS delta_post
        FROM phases
        WHERE pre_mid IS NOT NULL AND entry_mid IS NOT NULL
        ORDER BY lo_f
    """).df())


if __name__ == "__main__":
    main()

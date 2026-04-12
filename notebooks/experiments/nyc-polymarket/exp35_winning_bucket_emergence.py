"""Experiment 35 — When does the eventual winning bucket emerge as favorite?

For each day with a clear realized winning bucket, track:
    1. When did that bucket first become the market argmax (favorite)?
    2. How does its price evolve from 06 EDT to 22 EDT?
    3. What fraction of days does the winner emerge as fav by 12 EDT,
       16 EDT, 18 EDT, never?

If the winner usually emerges as fav AFTER 14 EDT, that's evidence
the market is slow to converge on truth and patient traders can wait
for the obvious bucket to declare itself.

If the winner usually starts as fav and stays there, the market is
already pricing efficiently — Strategy D is the only edge.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
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
        CREATE OR REPLACE TEMP TABLE nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)

    # The "winning bucket" each day = the strike whose [lo, hi] contains day_max
    con.execute("""
        CREATE OR REPLACE TEMP VIEW winners AS
        SELECT
            r.local_day, r.slug AS winning_slug, r.strike AS winning_strike,
            r.lo_f AS w_lo, r.hi_f AS w_hi
        FROM nyc_range r
        JOIN metar_daily md ON md.local_date = r.local_day
        WHERE md.day_max_whole BETWEEN r.lo_f AND r.hi_f
    """)

    # Snapshot prices at hours 10, 12, 14, 16, 18, 20, 22 UTC (06, 08, 10, 12, 14, 16, 18 EDT)
    for h_utc in [10, 12, 14, 16, 18, 20, 22]:
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE snap_h{h_utc} AS
            SELECT nr.slug, nr.lo_f, nr.local_day,
                (SELECT yes_price FROM '{PRICES}' p
                 WHERE p.slug=nr.slug
                   AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '{h_utc} hour')
                 ORDER BY p.timestamp DESC LIMIT 1) AS p_at
            FROM nyc_range nr
        """)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE fav_h{h_utc} AS
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p_at DESC NULLS LAST) AS rk
                FROM snap_h{h_utc} WHERE p_at IS NOT NULL
            )
            SELECT local_day, slug AS fav_slug, lo_f AS fav_lo, p_at AS fav_p
            FROM ranked WHERE rk=1
        """)

    print("\n=== AT EACH HOUR, WAS THE EVENTUAL WINNER ALREADY THE MARKET FAV? ===")
    rows = []
    for h_utc, edt in [(10,6),(12,8),(14,10),(16,12),(18,14),(20,16),(22,18)]:
        r = con.execute(f"""
            SELECT
                COUNT(*) AS n,
                COUNT(*) FILTER (WHERE w.winning_slug = f.fav_slug) AS winner_is_fav,
                ROUND(AVG(CASE WHEN w.winning_slug = f.fav_slug THEN 1.0 ELSE 0.0 END), 3) AS pct_winner_is_fav,
                ROUND(AVG((SELECT p_at FROM snap_h{h_utc} s WHERE s.slug=w.winning_slug)), 3) AS avg_winner_price
            FROM winners w JOIN fav_h{h_utc} f USING (local_day)
        """).df()
        r["edt"] = f"{edt:02d} EDT"
        rows.append(r)
    out = pd.concat(rows, ignore_index=True)
    out.insert(0, "edt", out.pop("edt"))
    print(out)

    print("\n=== AVERAGE WINNING-BUCKET PRICE TRAJECTORY ===")
    print("    Mean price of the eventual winner at each snapshot.")
    print("    If the market is slow to acknowledge, this should climb")
    print("    monotonically through the day.")
    print(con.execute("""
        SELECT
            ROUND(AVG((SELECT p_at FROM snap_h10 s WHERE s.slug=w.winning_slug)), 3) AS p_at_06,
            ROUND(AVG((SELECT p_at FROM snap_h12 s WHERE s.slug=w.winning_slug)), 3) AS p_at_08,
            ROUND(AVG((SELECT p_at FROM snap_h14 s WHERE s.slug=w.winning_slug)), 3) AS p_at_10,
            ROUND(AVG((SELECT p_at FROM snap_h16 s WHERE s.slug=w.winning_slug)), 3) AS p_at_12,
            ROUND(AVG((SELECT p_at FROM snap_h18 s WHERE s.slug=w.winning_slug)), 3) AS p_at_14,
            ROUND(AVG((SELECT p_at FROM snap_h20 s WHERE s.slug=w.winning_slug)), 3) AS p_at_16,
            ROUND(AVG((SELECT p_at FROM snap_h22 s WHERE s.slug=w.winning_slug)), 3) AS p_at_18
        FROM winners w
    """).df())

    print("\n=== HOUR AT WHICH WINNER FIRST BECAME FAVORITE (per day) ===")
    print(con.execute("""
        WITH per_day AS (
            SELECT w.local_day, w.winning_slug,
                   CASE WHEN w.winning_slug = (SELECT fav_slug FROM fav_h10 WHERE local_day=w.local_day) THEN '06 EDT'
                        WHEN w.winning_slug = (SELECT fav_slug FROM fav_h12 WHERE local_day=w.local_day) THEN '08 EDT'
                        WHEN w.winning_slug = (SELECT fav_slug FROM fav_h14 WHERE local_day=w.local_day) THEN '10 EDT'
                        WHEN w.winning_slug = (SELECT fav_slug FROM fav_h16 WHERE local_day=w.local_day) THEN '12 EDT'
                        WHEN w.winning_slug = (SELECT fav_slug FROM fav_h18 WHERE local_day=w.local_day) THEN '14 EDT'
                        WHEN w.winning_slug = (SELECT fav_slug FROM fav_h20 WHERE local_day=w.local_day) THEN '16 EDT'
                        WHEN w.winning_slug = (SELECT fav_slug FROM fav_h22 WHERE local_day=w.local_day) THEN '18 EDT'
                        ELSE 'never'
                   END AS first_fav_hour
            FROM winners w
        )
        SELECT first_fav_hour, COUNT(*) AS n
        FROM per_day
        GROUP BY first_fav_hour ORDER BY first_fav_hour
    """).df())


if __name__ == "__main__":
    main()

"""Exploratory E — does the 1-min Polymarket price react to METAR hourly readings?

Key question: when a new METAR observation lands (hourly at :51 past in LGA),
does the market reprice in the next 1-10 minutes? Or is the price already
reflecting the temperature before the observation even comes in?

Data joined:
  - METAR LGA tmpf (hourly 51-past minute) from iem_metar
  - Polymarket 1-min prices_history (prices of the favorite & near-buckets)
  - Coverage: 2026-04-10 19:51 UTC through 2026-04-11 18:51 UTC
    (roughly the 24 hours of 1-min price data we have for april-11 + overlap)

For each METAR reading, compute:
  1. The current temperature from that reading
  2. The "correct" bucket (the one containing that temperature)
  3. p_yes of that correct bucket immediately BEFORE the reading (t=-1 min)
  4. p_yes at t=+1, +5, +10 minutes after the reading
  5. Whether the reading moved the market toward the correct bucket

If (4) shows big moves after the reading: market reacts with lag → tradable.
If (4) shows no moves: market already pre-priced → no direct-temp edge.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 300)
pd.set_option("display.max_rows", 100)

MIN1 = "data/processed/polymarket_prices_history/min1/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # Dedup prices min1 like we did in expA
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW prices AS
        SELECT DISTINCT ON (slug, date_trunc('minute', timestamp))
               slug, p_yes,
               date_trunc('minute', timestamp) AS mt,
               regexp_extract(slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md,
               regexp_extract(slug, 'nyc-on-[a-z]+-[0-9]+-[0-9]+-(.+)', 1) AS strike
        FROM '{MIN1}'
        WHERE slug ILIKE '%april-11-2026%'
        ORDER BY slug, date_trunc('minute', timestamp), timestamp
    """)

    # METAR for LGA during the 1-min coverage window
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar AS
        SELECT
            date_trunc('minute', valid) AS mt,
            tmpf,
            max_temp_6hr_c * 9.0/5.0 + 32.0 AS max6h_f
        FROM '{METAR}'
        WHERE station = 'LGA'
          AND valid >= '2026-04-10 18:00:00+00:00'
          AND valid <  '2026-04-11 20:00:00+00:00'
    """)

    print("=== METAR readings available for the april-11 1-min window ===")
    print(con.execute("""
        SELECT mt, ROUND(tmpf, 1) AS tmpf, ROUND(max6h_f, 1) AS max6h_f
        FROM metar
        ORDER BY mt
    """).df())

    # Parse bucket lo/hi from the strike name (e.g. "60-61f" -> 60, 61)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW prices_bkt AS
        SELECT *,
            CASE WHEN strike LIKE '%forbelow'
                    THEN -999
                 WHEN strike LIKE '%forhigher'
                    THEN CAST(regexp_extract(strike, '([0-9]+)', 1) AS INT)
                 ELSE CAST(regexp_extract(strike, '([0-9]+)-', 1) AS INT)
            END AS lo_f,
            CASE WHEN strike LIKE '%forbelow'
                    THEN CAST(regexp_extract(strike, '([0-9]+)', 1) AS INT)
                 WHEN strike LIKE '%forhigher'
                    THEN 999
                 ELSE CAST(regexp_extract(strike, '-([0-9]+)', 1) AS INT)
            END AS hi_f
        FROM prices
    """)

    # Show the april-11 favorite trajectory with METAR readings overlaid
    print("\n=== favorite vs METAR (hourly) — round-by-round ===")
    print(con.execute("""
        WITH fav_per_min AS (
            SELECT mt, arg_max(strike, p_yes) AS favorite, MAX(p_yes) AS fav_p
            FROM prices GROUP BY 1
        ),
        joined AS (
            SELECT
                m.mt,
                ROUND(m.tmpf, 1) AS tmpf,
                f.favorite,
                ROUND(f.fav_p, 3) AS fav_p
            FROM metar m
            JOIN fav_per_min f USING (mt)
            ORDER BY m.mt
        )
        SELECT * FROM joined
    """).df())

    # Per-METAR event study: for each METAR reading, check how much the
    # bucket CONTAINING tmpf moved in the ±5 minute window
    print("\n=== event study: bucket containing current tmpf — p_yes at t-5, t0, t+1, t+5, t+10 ===")
    print(con.execute("""
        WITH events AS (
            SELECT mt AS ev_mt, tmpf, ROUND(tmpf) AS tmpf_int
            FROM metar
        ),
        matched AS (
            SELECT
                e.ev_mt,
                e.tmpf,
                p.strike,
                p.mt,
                p.p_yes,
                DATE_DIFF('minute', e.ev_mt, p.mt) AS rel_min
            FROM events e
            JOIN prices_bkt p
              ON e.tmpf_int BETWEEN p.lo_f AND p.hi_f
             AND p.mt BETWEEN e.ev_mt - INTERVAL '5 minute' AND e.ev_mt + INTERVAL '10 minute'
        )
        SELECT
            ev_mt, ROUND(tmpf, 1) AS tmpf, strike,
            ROUND(AVG(CASE WHEN rel_min = -5 THEN p_yes END), 3) AS t_m5,
            ROUND(AVG(CASE WHEN rel_min = -1 THEN p_yes END), 3) AS t_m1,
            ROUND(AVG(CASE WHEN rel_min = 0  THEN p_yes END), 3) AS t0,
            ROUND(AVG(CASE WHEN rel_min = 1  THEN p_yes END), 3) AS t_p1,
            ROUND(AVG(CASE WHEN rel_min = 5  THEN p_yes END), 3) AS t_p5,
            ROUND(AVG(CASE WHEN rel_min = 10 THEN p_yes END), 3) AS t_p10
        FROM matched
        GROUP BY 1, 2, 3
        ORDER BY ev_mt
    """).df())

    # Correlation test: sign of METAR-implied bucket move vs post-reading market move
    print("\n=== does the market move in the direction of the temperature reading? ===")
    print(con.execute("""
        WITH events AS (
            SELECT mt AS ev_mt, tmpf, ROUND(tmpf) AS tmpf_int
            FROM metar
        ),
        -- For each event, take the "candidate bucket" = bucket whose lo_f
        -- equals the even number at or below tmpf_int
        cand AS (
            SELECT
                e.ev_mt, e.tmpf, e.tmpf_int,
                (e.tmpf_int / 2) * 2 AS cand_lo
            FROM events e
        ),
        p_at AS (
            SELECT c.ev_mt, c.tmpf,
                   p.mt, p.p_yes,
                   DATE_DIFF('minute', c.ev_mt, p.mt) AS rel_min
            FROM cand c
            JOIN prices_bkt p ON p.lo_f = c.cand_lo
                              AND p.mt BETWEEN c.ev_mt - INTERVAL '2 minute'
                                            AND c.ev_mt + INTERVAL '10 minute'
        )
        SELECT
            COUNT(DISTINCT ev_mt) AS n_events,
            ROUND(AVG(CASE WHEN rel_min = 0 THEN p_yes END), 3) AS avg_p_t0,
            ROUND(AVG(CASE WHEN rel_min = 1 THEN p_yes END), 3) AS avg_p_t1,
            ROUND(AVG(CASE WHEN rel_min = 5 THEN p_yes END), 3) AS avg_p_t5,
            ROUND(AVG(CASE WHEN rel_min = 10 THEN p_yes END), 3) AS avg_p_t10
        FROM p_at
    """).df())


if __name__ == "__main__":
    main()

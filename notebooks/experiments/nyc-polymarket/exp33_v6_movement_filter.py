"""Experiment 33 — Strategy D V6: skip flat favorites at 14 EDT.

Exp32 found 15 days (27% of all days) where the favorite price stayed
within ±5¢ of its 12 EDT value through the afternoon. Hit rate on those
days: 0/15. They are systematic guaranteed losers — humans bought
lottery tickets and walked away, no actual repricing happened.

V6 hypothesis: at 14 EDT (or 16 EDT), check the favorite's price
movement since 12 EDT. If |drift| < 5¢, SKIP. Otherwise enter the +2
bucket at 14/16 EDT real ask.

Variants tested:
    V6a: enter at 14 EDT after movement check (early-afternoon entry)
    V6b: enter at 16 EDT after movement check (more time for the
         signal to develop)
    V6c: enter at 14 EDT after a tighter movement check (|drift| > 10c)
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02


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

    # Build a per-day view with fav identification + drift + +2 target
    for entry_h in [18, 20]:  # 14 EDT (18 UTC), 16 EDT (20 UTC)
        edt = entry_h - 4
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE day_view_{entry_h} AS
            WITH p12 AS (
                SELECT nr.slug, nr.lo_f, nr.hi_f, nr.local_day,
                    (SELECT yes_price FROM '{PRICES}' p
                     WHERE p.slug=nr.slug
                       AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
                     ORDER BY p.timestamp DESC LIMIT 1) AS p_at_12
                FROM nyc_range nr
            ),
            fav AS (
                SELECT local_day,
                       arg_max(slug, p_at_12) AS fav_slug,
                       arg_max(lo_f, p_at_12) AS fav_lo,
                       arg_max(hi_f, p_at_12) AS fav_hi,
                       max(p_at_12) AS fav_p_12
                FROM p12 WHERE p_at_12 IS NOT NULL GROUP BY 1
            )
            SELECT
                f.local_day, f.fav_slug, f.fav_lo, f.fav_hi, f.fav_p_12,
                (SELECT yes_price FROM '{PRICES}' p
                 WHERE p.slug = f.fav_slug
                   AND p.timestamp <= (CAST(f.local_day AS TIMESTAMPTZ) + INTERVAL '{entry_h} hour')
                 ORDER BY p.timestamp DESC LIMIT 1) AS fav_p_at_entry,
                p12_d.slug AS d_slug, p12_d.lo_f AS d_lo, p12_d.hi_f AS d_hi,
                (SELECT yes_price FROM '{PRICES}' p
                 WHERE p.slug = p12_d.slug
                   AND p.timestamp <= (CAST(f.local_day AS TIMESTAMPTZ) + INTERVAL '{entry_h} hour')
                 ORDER BY p.timestamp DESC LIMIT 1) AS d_p_at_entry,
                md.day_max_whole,
                CASE WHEN md.day_max_whole BETWEEN p12_d.lo_f AND p12_d.hi_f THEN 1 ELSE 0 END AS y
            FROM fav f
            JOIN p12 p12_d ON p12_d.local_day = f.local_day AND p12_d.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
        """)

        for label, drift_threshold in [
            (f"V6 @ {edt} EDT (skip |drift|<5c)", 0.05),
            (f"V6 @ {edt} EDT (skip |drift|<10c)", 0.10),
            (f"V1 baseline @ {edt} EDT (no skip)", -1.0),
        ]:
            print(f"\n=== {label} ===")
            print(con.execute(f"""
                SELECT
                    COUNT(*) AS n,
                    ROUND(AVG(d_p_at_entry), 3) AS avg_d_p,
                    ROUND(AVG(y), 3) AS hit_rate,
                    ROUND(AVG(y / (d_p_at_entry * (1 + {FEE})) - 1), 3) AS net_avg,
                    ROUND(QUANTILE_CONT(y / (d_p_at_entry * (1 + {FEE})) - 1, 0.5), 3) AS net_med,
                    ROUND(SUM(y / (d_p_at_entry * (1 + {FEE})) - 1), 2) AS cum_pnl
                FROM day_view_{entry_h}
                WHERE d_p_at_entry IS NOT NULL
                  AND d_p_at_entry >= 0.02
                  AND ABS(fav_p_at_entry - fav_p_12) >= {drift_threshold}
            """).df())


if __name__ == "__main__":
    main()

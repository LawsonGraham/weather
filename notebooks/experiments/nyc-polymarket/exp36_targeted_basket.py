"""Experiment 36 — Targeted multi-bucket basket strategy.

Exp35 found the eventual winning bucket has only 38¢ market price even
at 18 EDT after the peak. The market spreads probability across 4-6
plausible buckets. Strategy D bets on ONE candidate; a basket bets on
SEVERAL.

Setup: at 14 EDT (entry hour), use the LGA tmpf_at_14_EDT (from METAR)
as the "expected day max anchor". Buy YES on every range strike whose
lo is in [tmpf_14 - 2, tmpf_14 + 5]. That's roughly 4-7 strikes. Cost
should be ~30-50¢ per basket; payoff $1 if any single strike hits.

Two key choices:
    1. Anchor on tmpf_at_14_EDT (current temp) — assumes we believe
       the day_max is somewhere within +/- 5°F of current. From exp12
       this is true ~80% of days (rise_needed distribution).
    2. Compete head-to-head with Strategy D V1 +2 on the same window.

Variants:
    Basket A: lo in [tmpf+0, tmpf+5]   (5 strikes, expects mostly upward)
    Basket B: lo in [tmpf+1, tmpf+5]   (5 strikes, slightly biased up)
    Basket C: lo in [tmpf+2, tmpf+6]   (5 strikes, more aggressive)
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

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
    # METAR at 14 EDT (= 18 UTC)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_14edt AS
        WITH ranked AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   valid, tmpf,
                   ROW_NUMBER() OVER (
                       PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                       ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '18 hour'))))
                   ) AS rk
            FROM '{METAR}' WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 13 AND 15
        )
        SELECT local_date, ROUND(tmpf)::INT AS tmpf14 FROM ranked WHERE rk=1
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE range_14 AS
        WITH r AS (
            SELECT slug, group_item_title AS strike,
                   CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
                   CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
                   CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
            FROM '{MARKETS}'
            WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
              AND group_item_title NOT ILIKE '%or %'
        )
        SELECT r.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=r.slug
               AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '18 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_at_14edt
        FROM r
    """)

    # Run a basket variant
    def basket(lo_offset: int, hi_offset: int, label: str) -> None:
        print(f"\n=== {label} (lo in [tmpf+{lo_offset}, tmpf+{hi_offset}]) ===")
        print(con.execute(f"""
            WITH legs AS (
                SELECT
                    r.local_day, r.lo_f, r.hi_f, r.p_at_14edt,
                    md.day_max_whole, m14.tmpf14,
                    CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y
                FROM range_14 r
                JOIN metar_daily md ON md.local_date = r.local_day
                JOIN metar_14edt m14 ON m14.local_date = r.local_day
                WHERE r.p_at_14edt IS NOT NULL
                  AND r.lo_f BETWEEN m14.tmpf14 + {lo_offset} AND m14.tmpf14 + {hi_offset}
                  AND r.p_at_14edt >= 0.005
            ),
            per_day AS (
                SELECT local_day,
                       COUNT(*) AS n_legs,
                       ROUND(SUM(p_at_14edt), 4) AS total_mid_cost,
                       ROUND(SUM(p_at_14edt * (1 + {FEE})), 4) AS total_cost,
                       SUM(y) AS total_hits
                FROM legs
                GROUP BY local_day
                HAVING COUNT(*) >= 3 AND SUM(p_at_14edt) > 0
            )
            SELECT
                COUNT(*) AS n_days,
                ROUND(AVG(n_legs), 1) AS avg_legs,
                ROUND(AVG(total_cost), 3) AS avg_basket_cost,
                ROUND(AVG(total_hits), 3) AS avg_hits,
                ROUND(AVG(total_hits / total_cost - 1), 3) AS net_avg,
                ROUND(QUANTILE_CONT(total_hits / total_cost - 1, 0.5), 3) AS net_med,
                ROUND(SUM(total_hits / total_cost - 1), 2) AS cum_pnl
            FROM per_day
        """).df())

    basket(0, 5, "Basket A: tmpf+0 to tmpf+5")
    basket(1, 5, "Basket B: tmpf+1 to tmpf+5")
    basket(2, 6, "Basket C: tmpf+2 to tmpf+6")
    basket(0, 8, "Basket D: tmpf+0 to tmpf+8 (wide)")
    basket(2, 4, "Basket E: tmpf+2 to tmpf+4 (narrow)")

    # Compare with Strategy D V1 at 14 EDT for the same set of days
    print("\n=== STRATEGY D V1 +2 at 14 EDT (single bucket comparison) ===")
    print(con.execute(f"""
        WITH p14 AS (
            SELECT slug, lo_f, hi_f, local_day, p_at_14edt FROM range_14
        ),
        fav AS (
            SELECT local_day, arg_max(lo_f, p_at_14edt) AS fav_lo
            FROM p14 WHERE p_at_14edt IS NOT NULL GROUP BY 1
        ),
        d AS (
            SELECT f.local_day, p.p_at_14edt, p.lo_f, p.hi_f, md.day_max_whole,
                   CASE WHEN md.day_max_whole BETWEEN p.lo_f AND p.hi_f THEN 1 ELSE 0 END AS y
            FROM fav f
            JOIN p14 p ON p.local_day = f.local_day AND p.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE p.p_at_14edt IS NOT NULL AND p.p_at_14edt >= 0.02
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(p_at_14edt), 3) AS avg_p,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y / (p_at_14edt * (1 + {FEE})) - 1), 3) AS net_avg,
            ROUND(SUM(y / (p_at_14edt * (1 + {FEE})) - 1), 2) AS cum_pnl
        FROM d
    """).df())


if __name__ == "__main__":
    main()

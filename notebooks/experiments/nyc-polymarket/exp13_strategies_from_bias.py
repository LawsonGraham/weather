"""Experiment 13 — Backtest the strategies derived from exp12's universal bias.

Exp12 finding: mean signed_gap across 55 days is +4.07°F. 80% upward misses.
Bias correlates strongly with sky cover, rise_needed, and wind direction.

Five strategies:

    D:  Every day, BUY (go long) the strike 2°F above the favorite's low
        edge — i.e. the strike at `lo_f = fav_lo + 2`. That's 1 bucket up.

    D2: Buy the strike 4°F above fav_lo (2 buckets up). This is where the
        mean upward miss lands.

    D3: Buy the strike 6°F above fav_lo (3 buckets up).

    E:  Buy all three of {+2, +4, +6} buckets as a basket (1/3 size each).

    F:  Clear/scattered sky filter AND rise_needed < 3°F, then short the
        favorite. This is the exp12 Strategy B.

All at 12 EDT entry price from the prices parquet. 3¢ spread + 2% fee haircut.

Baseline: fade the favorite (exp05 headline).
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

SPREAD = 0.03
FEE = 0.02


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
        CREATE OR REPLACE TEMP VIEW metar_12edt AS
        WITH ranked AS (
            SELECT
                CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                valid, tmpf, skyc1,
                ROW_NUMBER() OVER (
                    PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                    ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                ) AS rk
            FROM '{METAR}' WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT local_date, tmpf, skyc1
        FROM ranked WHERE rk = 1
    """)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE range_12 AS
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
               AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p12
        FROM r
    """)

    # Favorite per day
    con.execute("""
        CREATE OR REPLACE TEMP TABLE fav AS
        WITH ranked AS (
            SELECT r12.*, md.day_max_whole,
                   ROW_NUMBER() OVER (PARTITION BY r12.local_day ORDER BY r12.p12 DESC NULLS LAST) AS rk
            FROM range_12 r12 JOIN metar_daily md ON md.local_date = r12.local_day
            WHERE r12.p12 IS NOT NULL AND md.day_max_whole IS NOT NULL
        )
        SELECT local_day, strike AS fav_strike, lo_f AS fav_lo, hi_f AS fav_hi,
               p12 AS fav_p, day_max_whole
        FROM ranked WHERE rk = 1
    """)


def strategy_baseline_fade(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== BASELINE — fade favorite every day (no filter) ===")
    print(con.execute(f"""
        WITH s AS (
            SELECT fav_p,
                   CASE WHEN day_max_whole BETWEEN fav_lo AND fav_hi THEN 1 ELSE 0 END AS y,
                   (1 - fav_p + {SPREAD}) * (1 + {FEE}) AS entry_cost
            FROM fav
            WHERE (1 - fav_p + {SPREAD}) < 0.99
        )
        SELECT COUNT(*) AS n,
               ROUND(AVG(fav_p), 3) AS avg_p,
               ROUND(AVG(1-y), 3)   AS miss_rate,
               ROUND(AVG((1-y)/entry_cost - 1), 3) AS net_avg,
               ROUND(QUANTILE_CONT((1-y)/entry_cost - 1, 0.5), 3) AS net_med,
               ROUND(SUM((1-y)/entry_cost - 1), 2) AS cum_pnl
        FROM s
    """).df())


def strategy_D(con: duckdb.DuckDBPyConnection, offset: int, label: str) -> None:
    print(f"\n=== STRATEGY D (offset=+{offset}°F) — BUY strike at lo_f = fav_lo + {offset} ===")
    print(con.execute(f"""
        WITH trade AS (
            SELECT f.local_day, f.fav_lo, f.day_max_whole,
                   r.strike, r.lo_f, r.hi_f, r.p12,
                   CASE WHEN f.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                   (r.p12 + {SPREAD}) * (1 + {FEE}) AS entry_cost
            FROM fav f
            JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + {offset}
            WHERE r.p12 IS NOT NULL
              AND (r.p12 + {SPREAD}) < 0.97  -- skip already-locked YES
        )
        SELECT
            '{label}' AS strategy,
            COUNT(*) AS n,
            ROUND(AVG(p12), 3) AS avg_p_entry,
            ROUND(AVG(y), 3)   AS hit_rate,
            ROUND(AVG(y/entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(y/entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(y/entry_cost - 1), 2) AS cum_pnl
        FROM trade
    """).df())


def strategy_E_basket(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY E — BUY 3-bucket basket {+2, +4, +6} (1/3 stake each) ===")
    print(con.execute(f"""
        WITH legs AS (
            SELECT f.local_day, f.fav_lo, f.day_max_whole, offset_f,
                   r.p12,
                   CASE WHEN f.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                   (r.p12 + {SPREAD}) * (1 + {FEE}) AS entry_cost
            FROM fav f
            CROSS JOIN (VALUES (2), (4), (6)) v(offset_f)
            JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + v.offset_f
            WHERE r.p12 IS NOT NULL AND (r.p12 + {SPREAD}) < 0.97
        ),
        per_day AS (
            SELECT local_day,
                   SUM(entry_cost) AS total_cost,
                   SUM(y) AS total_hits    -- can be 0, 1 at most (strikes are exclusive)
            FROM legs GROUP BY local_day
            HAVING COUNT(*) = 3
        )
        SELECT
            COUNT(*) AS n_days,
            ROUND(AVG(total_cost), 3) AS avg_stake,
            ROUND(AVG(total_hits), 3) AS avg_hit_in_basket,
            ROUND(AVG(total_hits / total_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(total_hits / total_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(total_hits / total_cost - 1), 2) AS cum_pnl
        FROM per_day
    """).df())


def strategy_F_filtered_fade(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY F — clear sky + rise_needed<3 filter, fade favorite ===")
    print(con.execute(f"""
        WITH s AS (
            SELECT f.fav_p, f.day_max_whole,
                   CASE WHEN f.day_max_whole BETWEEN f.fav_lo AND f.fav_hi THEN 1 ELSE 0 END AS y,
                   (1 - f.fav_p + {SPREAD}) * (1 + {FEE}) AS entry_cost,
                   m12.skyc1,
                   (f.fav_lo - m12.tmpf) AS rise_needed
            FROM fav f
            JOIN metar_12edt m12 ON m12.local_date = f.local_day
            WHERE m12.skyc1 IN ('CLR', 'FEW', 'SCT')
              AND (f.fav_lo - m12.tmpf) < 3
              AND (1 - f.fav_p + {SPREAD}) < 0.99
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(fav_p), 3)  AS avg_p,
            ROUND(AVG(1-y), 3)    AS miss_rate,
            ROUND(AVG((1-y)/entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT((1-y)/entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM((1-y)/entry_cost - 1), 2) AS cum_pnl
        FROM s
    """).df())


def best_single_offset_by_hit_rate(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== SCAN STRATEGY D OFFSETS — which offset from +0 to +12°F is best? ===")
    print("    Shows hit_rate, avg_p, and net_med for each integer offset.")
    for offset in [0, 2, 4, 6, 8, 10]:
        q = f"""
            WITH trade AS (
                SELECT r.p12,
                       CASE WHEN f.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                       (r.p12 + {SPREAD}) * (1 + {FEE}) AS entry_cost
                FROM fav f
                JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + {offset}
                WHERE r.p12 IS NOT NULL AND (r.p12 + {SPREAD}) < 0.97
            )
            SELECT {offset} AS offset_f, COUNT(*) n,
                   ROUND(AVG(p12), 3) AS avg_p,
                   ROUND(AVG(y), 3) AS hit_rate,
                   ROUND(AVG(y/entry_cost - 1), 3) AS net_avg,
                   ROUND(QUANTILE_CONT(y/entry_cost - 1, 0.5), 3) AS net_med,
                   ROUND(SUM(y/entry_cost - 1), 2) AS cum_pnl
            FROM trade
        """
        print(con.execute(q).df())


def per_day_strategy_D2(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== PER-DAY detail for D (offset=+2) — are the wins clustered? ===")
    print(con.execute(f"""
        SELECT f.local_day, f.fav_strike, f.fav_lo, f.day_max_whole,
               r.strike AS bought_strike,
               ROUND(r.p12, 3) AS p_entry,
               CASE WHEN f.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
               ROUND(CASE WHEN r.p12 IS NOT NULL AND r.p12 > 0
                          THEN (CASE WHEN f.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1.0 ELSE 0.0 END) / ((r.p12 + {SPREAD}) * (1 + {FEE})) - 1
                     END, 3) AS net_ret
        FROM fav f
        JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
        WHERE r.p12 IS NOT NULL AND (r.p12 + {SPREAD}) < 0.97
        ORDER BY net_ret DESC
        LIMIT 15
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    strategy_baseline_fade(con)
    best_single_offset_by_hit_rate(con)
    strategy_D(con, 2, "D  (+2°F)")
    strategy_D(con, 4, "D2 (+4°F)")
    strategy_D(con, 6, "D3 (+6°F)")
    strategy_E_basket(con)
    strategy_F_filtered_fade(con)
    per_day_strategy_D2(con)


if __name__ == "__main__":
    main()

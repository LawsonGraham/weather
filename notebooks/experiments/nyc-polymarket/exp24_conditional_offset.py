"""Experiment 24 — Strategy D with CONDITIONAL offset by METAR regime.

Exp22 revealed:
    • rise_needed >= 6°F: Strategy D loses (market forecast usually right)
    • humidity < 40%: Strategy D loses (gap is 5°F, +2 is too small)
    • The edge lives in 0-6°F rise_needed + mid/humid regime

Hypothesis: a CONDITIONAL offset rule will boost hit rate by:
    1. SKIPPING days where the market already forecasts a big rise
    2. Using a LARGER offset (+4 or +5) on dry clear days where the gap is wider
    3. Defaulting to +2 on humid/moderate days

Test several conditional rules against the unconditional +2 baseline.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

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
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   valid, tmpf, relh, skyc1,
                   ROW_NUMBER() OVER (
                       PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                       ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                   ) AS rk
            FROM '{METAR}' WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT local_date, tmpf, relh, skyc1 FROM ranked WHERE rk=1
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
             ORDER BY p.timestamp DESC LIMIT 1) AS p12_mid
        FROM r
    """)
    # Favorite + full ladder with METAR context
    con.execute("""
        CREATE OR REPLACE TEMP TABLE fav_ctx AS
        WITH favs AS (
            SELECT local_day,
                   arg_max(strike, p12_mid) AS fav_strike,
                   max(p12_mid)             AS fav_p,
                   arg_max(lo_f, p12_mid)   AS fav_lo,
                   arg_max(hi_f, p12_mid)   AS fav_hi
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT
            f.*, md.day_max_whole,
            m12.tmpf, m12.relh, m12.skyc1,
            (f.fav_lo - m12.tmpf) AS rise_needed
        FROM favs f
        JOIN metar_daily md ON md.local_date = f.local_day
        LEFT JOIN metar_12edt m12 ON m12.local_date = f.local_day
        WHERE md.day_max_whole IS NOT NULL
    """)


def run_offset_rule(con: duckdb.DuckDBPyConnection, rule_sql: str, label: str) -> None:
    """Apply a conditional offset rule. The rule_sql should compute `chosen_offset`
    (or NULL if skip). We then look up the bucket and score it."""
    df = con.execute(f"""
        WITH chosen AS (
            SELECT
                fc.local_day, fc.fav_lo, fc.day_max_whole, fc.relh, fc.skyc1,
                fc.rise_needed, fc.tmpf, fc.fav_p,
                ({rule_sql}) AS offset_f
            FROM fav_ctx fc
        ),
        trade AS (
            SELECT
                c.local_day, c.offset_f, c.day_max_whole,
                r.strike AS bought, r.lo_f, r.hi_f, r.p12_mid,
                CASE WHEN c.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                (r.p12_mid + 0.00) * (1 + {FEE}) AS entry_cost_real,   -- real-ask ~ mid
                (r.p12_mid + 0.03) * (1 + {FEE}) AS entry_cost_pessim
            FROM chosen c
            JOIN range_12 r ON r.local_day = c.local_day AND r.lo_f = c.fav_lo + c.offset_f
            WHERE c.offset_f IS NOT NULL
              AND r.p12_mid IS NOT NULL
              AND r.p12_mid >= 0.02
              AND (r.p12_mid + 0.00) * (1 + {FEE}) < 0.97
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(offset_f)::FLOAT, 1) AS avg_offset,
            ROUND(AVG(p12_mid), 3) AS avg_entry,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y / entry_cost_real - 1), 3) AS net_avg_real,
            ROUND(QUANTILE_CONT(y / entry_cost_real - 1, 0.5), 3) AS net_med_real,
            ROUND(SUM(y / entry_cost_real - 1), 2) AS cum_real,
            ROUND(SUM(y / entry_cost_pessim - 1), 2) AS cum_pessim
        FROM trade
    """).df()
    print(f"\n=== {label} ===")
    print(df)


def main() -> None:
    con = duckdb.connect()
    build(con)

    rules = [
        ("Fixed +2 (baseline)", "2"),
        ("Skip if rise_needed >= 6, else +2",
         "CASE WHEN fc.rise_needed >= 6 THEN NULL ELSE 2 END"),
        ("Skip if dry (<40% RH), else +2",
         "CASE WHEN fc.relh < 40 THEN NULL ELSE 2 END"),
        ("Skip if dry OR rise_needed>=6, else +2",
         "CASE WHEN fc.relh < 40 OR fc.rise_needed >= 6 THEN NULL ELSE 2 END"),
        ("+2 and +3 BASKET (two buckets per day)",
         "2"),  # special-cased below
    ]

    for label, rule in rules[:-1]:
        run_offset_rule(con, rule, label)

    # Special basket: buy both +2 and +3 on every day
    print("\n=== +2 and +3 BASKET (stake half on each) ===")
    print(con.execute(f"""
        WITH legs AS (
            SELECT fc.local_day, fc.fav_lo, fc.day_max_whole, v.off,
                   r.strike, r.lo_f, r.hi_f, r.p12_mid,
                   CASE WHEN fc.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                   r.p12_mid * (1 + {FEE}) AS cost
            FROM fav_ctx fc
            CROSS JOIN (VALUES (2), (3)) v(off)
            JOIN range_12 r ON r.local_day = fc.local_day AND r.lo_f = fc.fav_lo + v.off
            WHERE r.p12_mid IS NOT NULL AND r.p12_mid >= 0.02
        ),
        per_day AS (
            SELECT local_day, SUM(cost) AS total_cost, SUM(y) AS total_hits
            FROM legs GROUP BY local_day HAVING COUNT(*) = 2
        )
        SELECT
            COUNT(*) AS n_days,
            ROUND(AVG(total_cost), 3) AS avg_stake,
            ROUND(AVG(total_hits), 3) AS avg_hit_count,
            ROUND(AVG(total_hits / total_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(total_hits / total_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(total_hits / total_cost - 1), 2) AS cum_pnl
        FROM per_day
    """).df())

    # Check the dry-days-lose claim directly
    print("\n=== DRY DAYS SANITY CHECK — +2 offset, dry only (relh < 40) ===")
    print(con.execute(f"""
        WITH trade AS (
            SELECT fc.local_day, fc.relh, fc.day_max_whole,
                   r.strike, r.lo_f, r.hi_f, r.p12_mid,
                   CASE WHEN fc.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                   r.p12_mid * (1 + {FEE}) AS entry_cost
            FROM fav_ctx fc
            JOIN range_12 r ON r.local_day = fc.local_day AND r.lo_f = fc.fav_lo + 2
            WHERE r.p12_mid IS NOT NULL AND r.p12_mid >= 0.02 AND fc.relh < 40
        )
        SELECT
            COUNT(*) AS n, SUM(y) AS wins,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(SUM(y / entry_cost - 1), 2) AS cum_pnl
        FROM trade
    """).df())


if __name__ == "__main__":
    main()

"""Experiment 03 — METAR running max (fix ASOS 1-min gap confound).

Exp02 showed running-max from ASOS 1-min is systematically undercounted
(33% of 18-EDT snapshots had day_max - rmax >= 2°F). Redo with METAR
hourly + 6-hr RMK max, which is gap-free in the relevant window.

Signal anchor: at each afternoon snapshot, the METAR-derived running max
in whole F is the "known-so-far daily max". Map to range strike, compare
to market favorite, backtest the same two strategies as exp02.

This is exp02 with the signal bug fixed. If the numbers still look like
lottery, running-max-style signals are dead. If we see a clean pattern at
one snapshot, promote the running-max thesis.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 100)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone = 'UTC'")

    # METAR rows with rowwise-effective tmpf (max of instant tmpf and any
    # reported 6-hr-max converted to F). These are the real observations
    # the market would have to react to.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_obs AS
        SELECT
            valid AS ts_utc,
            CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
            GREATEST(COALESCE(tmpf, -999),
                     COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999)) AS tmpf_effective
        FROM '{METAR}'
        WHERE station='LGA'
          AND (tmpf IS NOT NULL OR max_temp_6hr_c IS NOT NULL)
    """)

    # Running max per local day via window function. Strictly causal.
    con.execute("""
        CREATE OR REPLACE TEMP VIEW metar_running AS
        SELECT
            ts_utc, local_date, tmpf_effective,
            MAX(tmpf_effective) OVER (PARTITION BY local_date ORDER BY ts_utc
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_max_f
        FROM metar_obs
        WHERE tmpf_effective > -900
    """)

    # Day max from METAR (for scoring).
    con.execute("""
        CREATE OR REPLACE TEMP VIEW metar_day_max AS
        SELECT local_date, MAX(tmpf_effective) AS day_max_raw,
               ROUND(MAX(tmpf_effective))::INT AS day_max_whole
        FROM metar_obs WHERE tmpf_effective > -900
        GROUP BY local_date
    """)

    # NYC daily-temp closed markets
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW nyc_strikes AS
        SELECT
            slug, group_item_title AS strike,
            CASE
                WHEN group_item_title ILIKE '%or higher%' THEN 'or_higher'
                WHEN group_item_title ILIKE '%or below%'  THEN 'or_below'
                ELSE 'range'
            END AS kind,
            CASE
                WHEN group_item_title ILIKE '%or%'
                    THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                ELSE CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT)
            END AS lo_f,
            CASE
                WHEN group_item_title ILIKE '%or%'
                    THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                ELSE CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT)
            END AS hi_f,
            CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
    """)

    # METAR-derived running max at each snapshot.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE metar_snapshots AS
        SELECT DISTINCT
            nyc.local_day,
            (CAST(nyc.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour') AS t_12edt,
            (CAST(nyc.local_day AS TIMESTAMPTZ) + INTERVAL '18 hour') AS t_14edt,
            (CAST(nyc.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour') AS t_16edt,
            (CAST(nyc.local_day AS TIMESTAMPTZ) + INTERVAL '22 hour') AS t_18edt
        FROM nyc_strikes nyc
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE rmax_snaps AS
        SELECT
            s.local_day,
            ROUND((SELECT MAX(running_max_f) FROM metar_running r
                   WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_12edt))::INT AS rmax_12,
            ROUND((SELECT MAX(running_max_f) FROM metar_running r
                   WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_14edt))::INT AS rmax_14,
            ROUND((SELECT MAX(running_max_f) FROM metar_running r
                   WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_16edt))::INT AS rmax_16,
            ROUND((SELECT MAX(running_max_f) FROM metar_running r
                   WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_18edt))::INT AS rmax_18
        FROM metar_snapshots s
    """)

    # Per-strike per-snapshot yes_price
    con.execute("""
        CREATE OR REPLACE TEMP TABLE prices_snap AS
        WITH snaps AS (
            SELECT ns.slug, ns.strike, ns.kind, ns.lo_f, ns.hi_f, ns.local_day,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour') AS t_12,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '18 hour') AS t_14,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour') AS t_16,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '22 hour') AS t_18
            FROM nyc_strikes ns
        )
        SELECT sn.*,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = sn.slug AND p.timestamp <= sn.t_12
             ORDER BY p.timestamp DESC LIMIT 1) AS p_12,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = sn.slug AND p.timestamp <= sn.t_14
             ORDER BY p.timestamp DESC LIMIT 1) AS p_14,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = sn.slug AND p.timestamp <= sn.t_16
             ORDER BY p.timestamp DESC LIMIT 1) AS p_16,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = sn.slug AND p.timestamp <= sn.t_18
             ORDER BY p.timestamp DESC LIMIT 1) AS p_18
        FROM snaps sn
    """)


def distribution(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== METAR RUNNING-MAX GAP (day_max - running_max) — should be tight ===")
    print(con.execute("""
        WITH d AS (
            SELECT md.day_max_whole - rs.rmax_14 AS d14,
                   md.day_max_whole - rs.rmax_16 AS d16,
                   md.day_max_whole - rs.rmax_18 AS d18
            FROM rmax_snaps rs
            JOIN metar_day_max md ON md.local_date = rs.local_day
            WHERE md.day_max_whole IS NOT NULL AND rs.rmax_14 IS NOT NULL
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(d14), 2) AS mean_d14,
            ROUND(AVG(d16), 2) AS mean_d16,
            ROUND(AVG(d18), 2) AS mean_d18,
            COUNT(*) FILTER (WHERE d14=0) AS n14_eq0,
            COUNT(*) FILTER (WHERE d14>=2) AS n14_ge2,
            COUNT(*) FILTER (WHERE d16=0) AS n16_eq0,
            COUNT(*) FILTER (WHERE d16>=2) AS n16_ge2,
            COUNT(*) FILTER (WHERE d18=0) AS n18_eq0,
            COUNT(*) FILTER (WHERE d18>=2) AS n18_ge2
        FROM d
    """).df())


def strategy(con: duckdb.DuckDBPyConnection) -> None:
    for hour_label, rmax_col, price_col in [
        ("12 EDT", "rmax_12", "p_12"),
        ("14 EDT", "rmax_14", "p_14"),
        ("16 EDT", "rmax_16", "p_16"),
        ("18 EDT", "rmax_18", "p_18"),
    ]:
        q = f"""
            WITH bets AS (
                SELECT
                    sps.local_day,
                    sps.strike,
                    sps.{price_col} AS p_entry,
                    CASE
                        WHEN sps.kind='range' AND md.day_max_whole BETWEEN sps.lo_f AND sps.hi_f THEN 1
                        WHEN sps.kind='range' THEN 0
                    END AS y
                FROM prices_snap sps
                JOIN rmax_snaps rs ON rs.local_day = sps.local_day
                JOIN metar_day_max md ON md.local_date = sps.local_day
                WHERE sps.kind='range'
                  AND rs.{rmax_col} IS NOT NULL
                  AND md.day_max_whole IS NOT NULL
                  AND rs.{rmax_col} BETWEEN sps.lo_f AND sps.hi_f
                  AND sps.{price_col} IS NOT NULL
            )
            SELECT
                '{hour_label}'                             AS snap,
                COUNT(*)                                   AS n,
                ROUND(AVG(p_entry), 3)                     AS avg_entry,
                ROUND(AVG(y), 3)                           AS hit_rate,
                ROUND(AVG(CASE WHEN p_entry > 0 THEN y/p_entry - 1 END), 3) AS avg_ret,
                ROUND(QUANTILE_CONT(CASE WHEN p_entry > 0 THEN y/p_entry - 1 END, 0.5), 3) AS med_ret,
                ROUND(SUM(CASE WHEN p_entry > 0 THEN y/p_entry - 1 END), 2)  AS cum_pnl
            FROM bets
        """
        print(con.execute(q).df())


def misalignment_table(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== RMAX vs DAY_MAX DISAGREEMENT ROWS (for diagnostics) ===")
    print(con.execute("""
        SELECT rs.local_day,
               md.day_max_whole AS dmax,
               rs.rmax_12, rs.rmax_14, rs.rmax_16, rs.rmax_18
        FROM rmax_snaps rs
        JOIN metar_day_max md ON md.local_date = rs.local_day
        WHERE md.day_max_whole - rs.rmax_16 >= 2
        ORDER BY rs.local_day
        LIMIT 15
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    distribution(con)
    print("\n=== STRATEGY: buy METAR-running-max bucket at each snapshot ===")
    strategy(con)
    misalignment_table(con)


if __name__ == "__main__":
    main()

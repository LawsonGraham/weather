"""Experiment 02 — "Follow the running max" naive strategy.

Hypothesis: the simplest possible temperature-derived signal is "the running
max so far is your best guess at the day's final max." At each afternoon
snapshot, we can read the 1-min LGA running-max in whole F and map it to a
range strike. If the market at that snapshot doesn't already fully agree,
we have a trade.

Floor question: can a whole-F running-max predictor beat Polymarket pricing
on any afternoon snapshot? If yes → simple rules work, deeper features will
work better. If no → the market is already absorbing temperature information
faster than whole-F running max can read it, and we need information the
market doesn't have (e.g., HRRR forecast updates).

Snapshots (all local NY):
    12 EDT / 16 UTC — early afternoon, max typically still rising
    14 EDT / 18 UTC — prime climbing window
    16 EDT / 20 UTC — typical daily max hour
    18 EDT / 22 UTC — after peak, running max near final

For each (day, snapshot), compare:
    running_max_bucket  — whole-F running max from 1-min so far (naive pred)
    market_favorite     — argmax range strike by yes_price at snapshot
    realized_bucket     — day's actual max bucket from METAR

and backtest:
    "buy the running_max bucket at snapshot price if it's < 50¢,
     hold to resolution, score by realized_yes / price - 1 per $1 in"

vs the analogous market-favorite strategy.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
ASOS1 = "data/raw/iem_asos_1min/LGA/*.csv"
METAR = "data/processed/iem_metar/LGA/*.parquet"


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone = 'UTC'")

    # 1-min LGA with running max per local NY day.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW lga_running AS
        WITH lga AS (
            SELECT
                ("valid(UTC)" AT TIME ZONE 'UTC') AS ts_utc,
                CAST((("valid(UTC)" AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                TRY_CAST(tmpf AS DOUBLE) AS tmpf
            FROM read_csv_auto('{ASOS1}', union_by_name=true)
            WHERE station='LGA' AND TRY_CAST(tmpf AS DOUBLE) IS NOT NULL
        )
        SELECT
            ts_utc, local_date, tmpf,
            MAX(tmpf) OVER (PARTITION BY local_date ORDER BY ts_utc
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_max
        FROM lga
    """)

    # METAR daily truth
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_daily AS
        WITH m AS (
            SELECT
                CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                GREATEST(COALESCE(tmpf, -999),
                         COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999)) AS tmpf_effective
            FROM '{METAR}'
            WHERE station='LGA'
        )
        SELECT
            local_date,
            ROUND(MAX(tmpf_effective))::INT AS day_max_whole
        FROM m WHERE tmpf_effective > -900
        GROUP BY 1
    """)

    # NYC daily-temp strikes
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
            CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day,
            volume_num
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
    """)

    # Snapshot times per day
    con.execute("""
        CREATE OR REPLACE TEMP VIEW snapshots AS
        SELECT DISTINCT local_day,
               (CAST(local_day AS TIMESTAMPTZ) + INTERVAL '16 hour') AS t_12edt,
               (CAST(local_day AS TIMESTAMPTZ) + INTERVAL '18 hour') AS t_14edt,
               (CAST(local_day AS TIMESTAMPTZ) + INTERVAL '20 hour') AS t_16edt,
               (CAST(local_day AS TIMESTAMPTZ) + INTERVAL '22 hour') AS t_18edt
        FROM nyc_strikes
    """)

    # Running-max value at each snapshot time per local day.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE rmax_snapshots AS
        SELECT
            s.local_day,
            (SELECT running_max FROM lga_running r
             WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_12edt
             ORDER BY r.ts_utc DESC LIMIT 1) AS rmax_raw_12,
            (SELECT running_max FROM lga_running r
             WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_14edt
             ORDER BY r.ts_utc DESC LIMIT 1) AS rmax_raw_14,
            (SELECT running_max FROM lga_running r
             WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_16edt
             ORDER BY r.ts_utc DESC LIMIT 1) AS rmax_raw_16,
            (SELECT running_max FROM lga_running r
             WHERE r.local_date = s.local_day AND r.ts_utc <= s.t_18edt
             ORDER BY r.ts_utc DESC LIMIT 1) AS rmax_raw_18
        FROM snapshots s
    """)
    # Add whole-F columns
    con.execute("""
        ALTER TABLE rmax_snapshots ADD COLUMN rmax_whole_12 INT;
        ALTER TABLE rmax_snapshots ADD COLUMN rmax_whole_14 INT;
        ALTER TABLE rmax_snapshots ADD COLUMN rmax_whole_16 INT;
        ALTER TABLE rmax_snapshots ADD COLUMN rmax_whole_18 INT;
        UPDATE rmax_snapshots SET
            rmax_whole_12 = ROUND(rmax_raw_12)::INT,
            rmax_whole_14 = ROUND(rmax_raw_14)::INT,
            rmax_whole_16 = ROUND(rmax_raw_16)::INT,
            rmax_whole_18 = ROUND(rmax_raw_18)::INT;
    """)

    # For each (strike, snapshot), the yes_price at that moment.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE strike_prices_snap AS
        WITH snaps AS (
            SELECT ns.slug, ns.strike, ns.kind, ns.lo_f, ns.hi_f, ns.local_day,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour') AS t_12,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '18 hour') AS t_14,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour') AS t_16,
                   (CAST(ns.local_day AS TIMESTAMPTZ) + INTERVAL '22 hour') AS t_18
            FROM nyc_strikes ns
        )
        SELECT
            sn.*,
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

    # Final scorable table: one row per (day, snapshot_hour) with:
    #  - running max bucket
    #  - market favorite (argmax p) at that hour
    #  - realized bucket
    con.execute("""
        CREATE OR REPLACE TEMP TABLE scenario AS
        SELECT
            sps.local_day,
            md.day_max_whole,
            rs.rmax_whole_12, rs.rmax_whole_14, rs.rmax_whole_16, rs.rmax_whole_18,
            -- favorite strike per snapshot (argmax range-strike p)
            arg_max(sps.strike, sps.p_12) FILTER (WHERE sps.kind='range' AND sps.p_12 IS NOT NULL) AS fav_strike_12,
            arg_max(sps.strike, sps.p_14) FILTER (WHERE sps.kind='range' AND sps.p_14 IS NOT NULL) AS fav_strike_14,
            arg_max(sps.strike, sps.p_16) FILTER (WHERE sps.kind='range' AND sps.p_16 IS NOT NULL) AS fav_strike_16,
            arg_max(sps.strike, sps.p_18) FILTER (WHERE sps.kind='range' AND sps.p_18 IS NOT NULL) AS fav_strike_18,
            max(sps.p_12) FILTER (WHERE sps.kind='range' AND sps.p_12 IS NOT NULL) AS fav_p_12,
            max(sps.p_14) FILTER (WHERE sps.kind='range' AND sps.p_14 IS NOT NULL) AS fav_p_14,
            max(sps.p_16) FILTER (WHERE sps.kind='range' AND sps.p_16 IS NOT NULL) AS fav_p_16,
            max(sps.p_18) FILTER (WHERE sps.kind='range' AND sps.p_18 IS NOT NULL) AS fav_p_18
        FROM strike_prices_snap sps
        LEFT JOIN rmax_snapshots rs ON rs.local_day = sps.local_day
        LEFT JOIN metar_daily md ON md.local_date = sps.local_day
        GROUP BY 1, 2, 3, 4, 5, 6
    """)


def agreement(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== AGREEMENT: does running-max whole-F equal market favorite's lower bound? ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_days,
            COUNT(*) FILTER (WHERE rmax_whole_12 IS NOT NULL AND fav_strike_12 IS NOT NULL) AS n_12_both,
            -- How often rmax equals strike lo or hi (adjacent bucket)
            SUM(CASE WHEN rmax_whole_12 IS NOT NULL AND fav_strike_12 IS NOT NULL
                     AND (rmax_whole_12 = CAST(regexp_extract(fav_strike_12, '(-?\\d+)-', 1) AS INT)
                         OR rmax_whole_12 = CAST(regexp_extract(fav_strike_12, '-(-?\\d+)', 1) AS INT))
                     THEN 1 ELSE 0 END) AS agree_12,
            SUM(CASE WHEN rmax_whole_16 IS NOT NULL AND fav_strike_16 IS NOT NULL
                     AND (rmax_whole_16 = CAST(regexp_extract(fav_strike_16, '(-?\\d+)-', 1) AS INT)
                         OR rmax_whole_16 = CAST(regexp_extract(fav_strike_16, '-(-?\\d+)', 1) AS INT))
                     THEN 1 ELSE 0 END) AS agree_16,
            SUM(CASE WHEN rmax_whole_18 IS NOT NULL AND fav_strike_18 IS NOT NULL
                     AND (rmax_whole_18 = CAST(regexp_extract(fav_strike_18, '(-?\\d+)-', 1) AS INT)
                         OR rmax_whole_18 = CAST(regexp_extract(fav_strike_18, '-(-?\\d+)', 1) AS INT))
                     THEN 1 ELSE 0 END) AS agree_18
        FROM scenario
        WHERE day_max_whole IS NOT NULL
    """).df())


def running_max_vs_final(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== HOW MUCH DOES DAY-MAX RISE AFTER EACH SNAPSHOT? ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(day_max_whole - rmax_whole_12), 2) AS mean_delta_12,
            ROUND(AVG(day_max_whole - rmax_whole_14), 2) AS mean_delta_14,
            ROUND(AVG(day_max_whole - rmax_whole_16), 2) AS mean_delta_16,
            ROUND(AVG(day_max_whole - rmax_whole_18), 2) AS mean_delta_18,
            ROUND(STDDEV(day_max_whole - rmax_whole_12), 2) AS std_delta_12,
            ROUND(STDDEV(day_max_whole - rmax_whole_14), 2) AS std_delta_14,
            ROUND(STDDEV(day_max_whole - rmax_whole_16), 2) AS std_delta_16,
            ROUND(STDDEV(day_max_whole - rmax_whole_18), 2) AS std_delta_18
        FROM scenario
        WHERE day_max_whole IS NOT NULL AND rmax_whole_12 IS NOT NULL
    """).df())

    print("\n=== DISTRIBUTION of (day_max - running_max) AT EACH SNAPSHOT ===")
    print("    0 = running max already = day max. Positive = day still had room to climb.")
    print(con.execute("""
        WITH delta AS (
            SELECT day_max_whole - rmax_whole_14 AS d_14,
                   day_max_whole - rmax_whole_16 AS d_16,
                   day_max_whole - rmax_whole_18 AS d_18
            FROM scenario
            WHERE day_max_whole IS NOT NULL AND rmax_whole_14 IS NOT NULL
        )
        SELECT
            COUNT(*) FILTER (WHERE d_14 = 0) AS n14_eq0,
            COUNT(*) FILTER (WHERE d_14 = 1) AS n14_eq1,
            COUNT(*) FILTER (WHERE d_14 >= 2) AS n14_ge2,
            COUNT(*) FILTER (WHERE d_16 = 0) AS n16_eq0,
            COUNT(*) FILTER (WHERE d_16 = 1) AS n16_eq1,
            COUNT(*) FILTER (WHERE d_16 >= 2) AS n16_ge2,
            COUNT(*) FILTER (WHERE d_18 = 0) AS n18_eq0,
            COUNT(*) FILTER (WHERE d_18 = 1) AS n18_eq1,
            COUNT(*) FILTER (WHERE d_18 >= 2) AS n18_ge2,
            COUNT(*) AS n_total
        FROM delta
    """).df())


def backtest_strategy(con: duckdb.DuckDBPyConnection) -> None:
    # For each (day, snapshot), find the range strike that matches running_max,
    # look up its price at that snapshot, and score its realized outcome.
    print("\n=== STRATEGY: 'buy running-max-bucket at snapshot' — PnL per $1 invested ===")
    for hour_label, hour_col, price_col in [
        ("12 EDT", "rmax_whole_12", "p_12"),
        ("14 EDT", "rmax_whole_14", "p_14"),
        ("16 EDT", "rmax_whole_16", "p_16"),
        ("18 EDT", "rmax_whole_18", "p_18"),
    ]:
        q = f"""
            WITH picks AS (
                SELECT
                    sps.local_day,
                    sps.strike,
                    sps.{price_col} AS p_snap,
                    CASE
                        WHEN sps.kind='range' AND md.day_max_whole BETWEEN sps.lo_f AND sps.hi_f THEN 1
                        WHEN sps.kind='range' THEN 0
                    END AS realized_yes
                FROM strike_prices_snap sps
                JOIN rmax_snapshots rs ON rs.local_day = sps.local_day
                JOIN metar_daily md ON md.local_date = sps.local_day
                WHERE sps.kind='range'
                  AND rs.{hour_col} IS NOT NULL
                  AND md.day_max_whole IS NOT NULL
                  AND rs.{hour_col} BETWEEN sps.lo_f AND sps.hi_f
                  AND sps.{price_col} IS NOT NULL
            )
            SELECT
                '{hour_label}' AS snap,
                COUNT(*) AS n_bets,
                ROUND(AVG(p_snap), 4) AS avg_entry_price,
                ROUND(AVG(realized_yes), 4) AS hit_rate,
                ROUND(AVG(CASE WHEN p_snap > 0 THEN realized_yes/p_snap - 1 END), 4) AS avg_return,
                ROUND(SUM(CASE WHEN p_snap > 0 THEN realized_yes/p_snap - 1 END), 3) AS cum_pnl
            FROM picks
        """
        print(con.execute(q).df())

    print("\n=== BASELINE: 'follow the market favorite at snapshot' PnL ===")
    for hour_label, fav_col, p_col in [
        ("12 EDT", "fav_strike_12", "fav_p_12"),
        ("14 EDT", "fav_strike_14", "fav_p_14"),
        ("16 EDT", "fav_strike_16", "fav_p_16"),
        ("18 EDT", "fav_strike_18", "fav_p_18"),
    ]:
        q = f"""
            WITH picks AS (
                SELECT
                    sc.local_day,
                    sc.{fav_col} AS fav,
                    sc.{p_col}   AS p_snap,
                    sc.day_max_whole,
                    CASE
                        WHEN sc.day_max_whole BETWEEN
                            CAST(regexp_extract(sc.{fav_col}, '(-?\\d+)-', 1) AS INT) AND
                            CAST(regexp_extract(sc.{fav_col}, '-(-?\\d+)', 1) AS INT)
                            THEN 1 ELSE 0
                    END AS realized_yes
                FROM scenario sc
                WHERE sc.{fav_col} IS NOT NULL AND sc.day_max_whole IS NOT NULL
                  AND sc.{fav_col} ILIKE '%°F' AND sc.{fav_col} NOT ILIKE '%or%'
            )
            SELECT
                '{hour_label}' AS snap,
                COUNT(*) AS n,
                ROUND(AVG(p_snap), 4) AS avg_entry,
                ROUND(AVG(realized_yes), 4) AS hit_rate,
                ROUND(AVG(CASE WHEN p_snap > 0 THEN realized_yes/p_snap - 1 END), 4) AS avg_return,
                ROUND(SUM(CASE WHEN p_snap > 0 THEN realized_yes/p_snap - 1 END), 3) AS cum_pnl
            FROM picks
        """
        print(con.execute(q).df())


def edge_table(con: duckdb.DuckDBPyConnection) -> None:
    # For the 14/16 EDT snapshots, list cases where running max bucket
    # disagreed with market favorite, and score both outcomes.
    print("\n=== DISAGREEMENT TABLE (14 EDT) ===")
    print(con.execute("""
        SELECT
            sc.local_day,
            sc.day_max_whole AS actual_max,
            sc.rmax_whole_14 AS rmax,
            sc.fav_strike_14 AS fav,
            ROUND(sc.fav_p_14, 3) AS fav_p
        FROM scenario sc
        WHERE sc.day_max_whole IS NOT NULL
          AND sc.rmax_whole_14 IS NOT NULL
          AND sc.fav_strike_14 IS NOT NULL
          AND sc.rmax_whole_14 NOT BETWEEN
                CAST(regexp_extract(sc.fav_strike_14, '(-?\\d+)-', 1) AS INT) AND
                CAST(regexp_extract(sc.fav_strike_14, '-(-?\\d+)', 1) AS INT)
        ORDER BY sc.local_day
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    running_max_vs_final(con)
    agreement(con)
    backtest_strategy(con)
    edge_table(con)


if __name__ == "__main__":
    main()

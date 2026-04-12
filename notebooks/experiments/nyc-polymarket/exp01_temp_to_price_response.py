"""Experiment 01 — Temperature → price response function.

Goal: when LGA `tmpf` ticks up or down at the 1-minute cadence, how does the
price on the nearest strike react, and on what lag?

Concretely:
    • For every strike market, every minute where the 1-min tmpf changed from
      the prior minute, record the directed move Δt.
    • Look up the yes_price for that slug at the minute boundary and at
      Δ+{10s, 30s, 60s, 180s, 300s}.
    • Aggregate Δprice per Δtemp. Separate by strike type (range vs end rung)
      and by distance from the strike threshold (in °F).

Hypothesis: the "nearest live" strike (the one currently carrying the highest
price) should have the steepest response. Upstream buckets should dampen; tail
strikes should barely move.

Emitted artifacts (all text tables printed to stdout, no files):
    • Response by kind × magnitude of temp change
    • Response by strike-to-threshold distance
    • Per-day top-moves listing (the raw material for the vault writeup)
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


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone = 'UTC'")

    # 1-min LGA with temp deltas vs the previous minute (skipped NULLs, strictly
    # causal). Only emit rows where the delta is non-zero — that's the event
    # set we care about.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW lga_ticks AS
        WITH lga AS (
            SELECT
                ("valid(UTC)" AT TIME ZONE 'UTC') AS ts_utc,
                CAST((("valid(UTC)" AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                TRY_CAST(tmpf AS DOUBLE) AS tmpf
            FROM read_csv_auto('{ASOS1}', union_by_name=true)
            WHERE station='LGA'
              AND TRY_CAST(tmpf AS DOUBLE) IS NOT NULL
        ),
        shifted AS (
            SELECT
                ts_utc, local_date, tmpf,
                LAG(tmpf)  OVER (ORDER BY ts_utc) AS prev_tmpf,
                LAG(ts_utc) OVER (ORDER BY ts_utc) AS prev_ts
            FROM lga
        )
        SELECT
            ts_utc, local_date, tmpf, prev_tmpf,
            (tmpf - prev_tmpf) AS dtemp
        FROM shifted
        WHERE prev_tmpf IS NOT NULL
          AND tmpf != prev_tmpf
          -- Only "adjacent" minutes: reject gaps > 5 min
          AND EXTRACT(EPOCH FROM (ts_utc - prev_ts)) <= 300
    """)

    # NYC closed daily temp markets
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

    # For each (slug, tick): price at tick, and at +10s/+30s/+60s/+180s/+300s.
    # We subsample to keep the data tractable: only ticks during the local "hot
    # window" 10:00-22:00 local NY (these are the minutes when the daily max is
    # actively evolving). Also restrict to strikes whose local_day matches the
    # tick's local_date.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE tick_responses AS
        WITH matched AS (
            SELECT
                s.slug, s.strike, s.kind, s.lo_f, s.hi_f, s.local_day,
                s.volume_num,
                t.ts_utc, t.tmpf, t.prev_tmpf, t.dtemp,
                -- strike midpoint and distance of current tmpf to strike center
                CASE
                    WHEN s.kind='or_higher' THEN s.lo_f
                    WHEN s.kind='or_below'  THEN s.hi_f
                    ELSE (s.lo_f + s.hi_f) / 2.0
                END AS strike_center,
                CASE
                    WHEN s.kind='or_higher' THEN t.tmpf - s.lo_f
                    WHEN s.kind='or_below'  THEN s.hi_f - t.tmpf
                    ELSE t.tmpf - (s.lo_f + s.hi_f) / 2.0
                END AS tmpf_vs_strike
            FROM nyc_strikes s
            JOIN lga_ticks t ON t.local_date = s.local_day
            WHERE EXTRACT(HOUR FROM (t.ts_utc AT TIME ZONE 'America/New_York')) BETWEEN 10 AND 21
        )
        SELECT
            m.*,
            -- Price at the tick minute (last price <= tick ts)
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = m.slug AND p.timestamp <= m.ts_utc
             ORDER BY p.timestamp DESC LIMIT 1) AS p0,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = m.slug AND p.timestamp >= m.ts_utc + INTERVAL '10 second'
             ORDER BY p.timestamp LIMIT 1) AS p_10s,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = m.slug AND p.timestamp >= m.ts_utc + INTERVAL '30 second'
             ORDER BY p.timestamp LIMIT 1) AS p_30s,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = m.slug AND p.timestamp >= m.ts_utc + INTERVAL '60 second'
             ORDER BY p.timestamp LIMIT 1) AS p_1m,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = m.slug AND p.timestamp >= m.ts_utc + INTERVAL '180 second'
             ORDER BY p.timestamp LIMIT 1) AS p_3m,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = m.slug AND p.timestamp >= m.ts_utc + INTERVAL '300 second'
             ORDER BY p.timestamp LIMIT 1) AS p_5m
        FROM matched m
        -- Keep the total set tractable: only big single-minute moves
        WHERE ABS(m.dtemp) >= 1.0
          -- Only strikes within ±4°F of current temp (the live-ish bucket cluster)
          AND ABS(m.tmpf_vs_strike) <= 4
    """)


def response_by_dtemp(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== RESPONSE BY Δtemp BUCKET (all strikes within ±4°F of current) ===")
    print(con.execute("""
        SELECT
            CASE WHEN dtemp >= 1 THEN '+1F'
                 WHEN dtemp <= -1 THEN '-1F'
                 END AS dir,
            CASE WHEN ABS(dtemp) >= 3 THEN '≥3'
                 WHEN ABS(dtemp) >= 2 THEN '2'
                 ELSE '1' END AS magnitude,
            COUNT(*) AS n,
            ROUND(AVG(p0),          4) AS mean_p0,
            ROUND(AVG(p_10s - p0),  4) AS mean_dp_10s,
            ROUND(AVG(p_30s - p0),  4) AS mean_dp_30s,
            ROUND(AVG(p_1m  - p0),  4) AS mean_dp_1m,
            ROUND(AVG(p_3m  - p0),  4) AS mean_dp_3m,
            ROUND(AVG(p_5m  - p0),  4) AS mean_dp_5m
        FROM tick_responses
        WHERE p0 IS NOT NULL AND p_5m IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2
    """).df())


def response_by_strike_kind(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== RESPONSE BY STRIKE KIND × direction (|Δtemp| ≥ 1°F) ===")
    print(con.execute("""
        SELECT
            kind,
            CASE WHEN dtemp > 0 THEN '+ warming'
                 WHEN dtemp < 0 THEN '- cooling'
                 END AS dir,
            COUNT(*) AS n,
            ROUND(AVG(p0),          4) AS mean_p0,
            ROUND(AVG(p_30s - p0),  4) AS dp_30s,
            ROUND(AVG(p_1m  - p0),  4) AS dp_1m,
            ROUND(AVG(p_5m  - p0),  4) AS dp_5m
        FROM tick_responses
        WHERE p0 IS NOT NULL AND p_5m IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2
    """).df())


def response_by_distance(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== RESPONSE BY DISTANCE-TO-STRIKE (F) — where does the market react most? ===")
    print("    For range strikes, distance = tmpf - center; for or_higher, distance = tmpf - lo_f")
    print("    A +1F move with distance=-1 crosses INTO the strike from below.")
    print(con.execute("""
        SELECT
            kind,
            ROUND(tmpf_vs_strike)::INT AS dist_f,
            COUNT(*) AS n,
            ROUND(AVG(p0), 3)              AS p0,
            ROUND(AVG(p_1m - p0), 4)        AS dp_1m_warm,
            ROUND(AVG(p_5m - p0), 4)        AS dp_5m_warm
        FROM tick_responses
        WHERE p0 IS NOT NULL AND p_5m IS NOT NULL AND dtemp > 0
        GROUP BY 1, 2 ORDER BY 1, 2
    """).df())


def biggest_single_reactions(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== TOP 15 SINGLE-TICK REACTIONS (largest price move following a Δtemp) ===")
    print(con.execute("""
        SELECT
            ts_utc,
            strike, kind,
            prev_tmpf, tmpf, dtemp,
            ROUND(tmpf_vs_strike, 1) AS dist,
            ROUND(p0, 3) AS p0,
            ROUND(p_5m, 3) AS p_5m,
            ROUND(p_5m - p0, 3) AS dp_5m
        FROM tick_responses
        WHERE p0 IS NOT NULL AND p_5m IS NOT NULL
        ORDER BY ABS(p_5m - p0) DESC
        LIMIT 15
    """).df())


def ticks_per_day(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== EVENT COUNT BY DAY (how much raw material do we have per day?) ===")
    print(con.execute("""
        SELECT
            local_day,
            COUNT(*) AS n_tick_events,
            COUNT(DISTINCT ts_utc) AS n_unique_ticks,
            COUNT(*) FILTER (WHERE p0 IS NOT NULL AND p_5m IS NOT NULL) AS n_with_prices
        FROM tick_responses
        GROUP BY 1 ORDER BY 1 DESC LIMIT 20
    """).df())


def summary_stats(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== OVERALL EVENT SET ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_events,
            COUNT(DISTINCT slug) AS n_slugs,
            COUNT(DISTINCT local_day) AS n_days,
            ROUND(AVG(p0), 3) AS mean_p0,
            ROUND(STDDEV(p0), 3) AS std_p0
        FROM tick_responses
        WHERE p0 IS NOT NULL
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    summary_stats(con)
    ticks_per_day(con)
    response_by_dtemp(con)
    response_by_strike_kind(con)
    response_by_distance(con)
    biggest_single_reactions(con)


if __name__ == "__main__":
    main()

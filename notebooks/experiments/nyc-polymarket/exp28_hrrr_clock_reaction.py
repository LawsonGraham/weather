"""Experiment 28 — Fill-volume reaction to the HRRR release clock.

Hypothesis (mechanistic story from exp12/20): the Polymarket NYC daily-
temperature market is anchoring on an overnight HRRR (or NBM) forecast
that under-predicts afternoon rise on clear/dry/still-morning days.

DIRECT TEST: HRRR publishes new runs at 00 / 06 / 12 / 18 UTC, with
products available ~30-60 min after the cycle start. If the market is
consuming HRRR, we should see fill-volume spikes at:
    ~00:30-01:30 UTC (post-00Z release)
    ~06:30-07:30 UTC
    ~12:30-13:30 UTC   ← ~08:30 EDT morning kick-in
    ~18:30-19:30 UTC   ← ~14:30 EDT afternoon kick-in

And between releases we should see quieter trading.

Method: aggregate fill counts and USD volume by hour-of-day (UTC) across
all 55 NYC daily-temp market days. Look for peaks at 01/07/13/19 UTC.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 30)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone='UTC'")
    # Restrict fills to NYC daily-temp slugs
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW nyc_slugs AS
        SELECT slug FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%'
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE nyc_fills AS
        SELECT
            f.slug, f.timestamp,
            EXTRACT(HOUR FROM f.timestamp) AS hour_utc,
            EXTRACT(HOUR FROM (f.timestamp AT TIME ZONE 'America/New_York')) AS hour_edt,
            EXTRACT(MINUTE FROM f.timestamp) AS min_utc,
            f.price, f.usd, f.outcome, f.side,
            CAST((f.timestamp AT TIME ZONE 'America/New_York') AS DATE) AS local_date
        FROM '{FILLS}' f
        JOIN nyc_slugs s USING (slug)
    """)


def hourly_profile(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FILL VOLUME BY HOUR UTC (all NYC daily-temp fills) ===")
    print("    HRRR releases at 00/06/12/18 UTC; products ready ~30-60 min later.")
    print("    Expect spikes at 01/07/13/19 UTC if market consumes HRRR.")
    print(con.execute("""
        SELECT
            hour_utc AS h_utc,
            (hour_utc - 4) % 24 AS h_edt,
            COUNT(*) AS n_fills,
            ROUND(SUM(usd), 0) AS total_usd,
            ROUND(AVG(usd), 2) AS avg_usd,
            COUNT(DISTINCT slug) AS n_slugs
        FROM nyc_fills
        GROUP BY hour_utc ORDER BY hour_utc
    """).df())


def minute_profile_around_hrrr(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== MINUTE-RESOLVED VOLUME AROUND HRRR RELEASE WINDOWS ===")
    print("    Windows: 00-02 / 06-08 / 12-14 / 18-20 UTC (release + 2h)")
    print(con.execute("""
        SELECT
            CASE
                WHEN hour_utc BETWEEN 0 AND 1 THEN '00Z-02Z'
                WHEN hour_utc BETWEEN 6 AND 7 THEN '06Z-08Z'
                WHEN hour_utc BETWEEN 12 AND 13 THEN '12Z-14Z'
                WHEN hour_utc BETWEEN 18 AND 19 THEN '18Z-20Z'
                ELSE 'other'
            END AS cycle_bucket,
            (hour_utc * 60 + min_utc) % 120 AS min_since_cycle,
            COUNT(*) AS n_fills,
            ROUND(SUM(usd), 0) AS vol_usd
        FROM nyc_fills
        WHERE hour_utc IN (0,1,6,7,12,13,18,19)
        GROUP BY cycle_bucket, min_since_cycle
        ORDER BY cycle_bucket, min_since_cycle
        LIMIT 30
    """).df())


def volume_share_compare(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== HRRR-WINDOW HOURS vs OTHER HOURS (2-hr post-release) ===")
    print("    Post-release windows are 01-02 / 07-08 / 13-14 / 19-20 UTC.")
    print(con.execute("""
        WITH agg AS (
            SELECT
                CASE WHEN hour_utc IN (1,2,7,8,13,14,19,20) THEN 'post_hrrr' ELSE 'other' END AS bucket,
                COUNT(*) AS n_fills,
                SUM(usd) AS total_usd
            FROM nyc_fills GROUP BY bucket
        ),
        tot AS (SELECT SUM(n_fills) AS tf, SUM(total_usd) AS tu FROM agg)
        SELECT
            bucket,
            agg.n_fills,
            ROUND(agg.n_fills::DOUBLE / tot.tf, 3) AS share_fills,
            ROUND(agg.total_usd, 0) AS total_usd,
            ROUND(agg.total_usd / tot.tu, 3) AS share_usd
        FROM agg CROSS JOIN tot
    """).df())


def by_local_hour(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FILL VOLUME BY HOUR EDT (local NY hour) ===")
    print("    Peaks here tell us when US traders are active, separate from HRRR clock.")
    print(con.execute("""
        SELECT
            hour_edt AS h_edt,
            COUNT(*) AS n_fills,
            ROUND(SUM(usd), 0) AS total_usd
        FROM nyc_fills
        GROUP BY hour_edt ORDER BY hour_edt
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    hourly_profile(con)
    volume_share_compare(con)
    by_local_hour(con)
    minute_profile_around_hrrr(con)


if __name__ == "__main__":
    main()

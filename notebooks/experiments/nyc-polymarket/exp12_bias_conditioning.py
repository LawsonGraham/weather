"""Experiment 12 — Condition the upward bias on METAR features.

Exp08 found: on peaked-ladder days, the market's favorite bucket is
systematically too cold — actual max lands +2 to +10°F above the favorite,
never below. Average upward gap on the 6 miss days = +4.7°F. Never zero
downward misses.

Hypothesis: the market is anchoring on a forecast that doesn't fully
incorporate the afternoon warming signals. If we can find a METAR feature
at 12 EDT that predicts "gap" (day_max - fav_lo), we have a meta-filter
that converts a noisy 5-trades-per-55-days rule into something bigger.

Concretely: for ALL scorable days (not just peaked-ladder), compute at
12 EDT:
    - tmpf_12       — current temperature
    - dwpf_12       — dewpoint
    - relh_12       — relative humidity
    - sknt_12       — wind speed (knots)
    - drct_12       — wind direction (degrees)
    - sky_cover     — parsed from skyc1 (CLR/FEW/SCT/BKN/OVC)
    - p01i_12       — 1-hr precip

And join to:
    - favorite_gap  — day_max_whole - fav_lo  (positive = upward miss)
    - fav_p         — favorite price at 12 EDT
    - fav_bucket    — the favorite bucket name

Segment the gap distribution by each feature. Look for: is there a setting
that cleanly predicts upward gaps?

Also specifically: does SKY CLEAR (CLR/FEW) at 12 EDT predict a bigger gap?
Cloudy days → limited afternoon rise. Clear days → unimpeded solar heating
→ bigger afternoon rise than the overnight forecast captured.
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

    # METAR 12 EDT features: pick the hourly observation closest to 16:00 UTC.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW metar_12edt AS
        WITH ranked AS (
            SELECT
                CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                valid, tmpf, dwpf, relh, sknt, drct, p01i,
                skyc1, skyl1, feel,
                ROW_NUMBER() OVER (
                    PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                    ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                ) AS rk
            FROM '{METAR}'
            WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT local_date, tmpf, dwpf, relh, sknt, drct, p01i, skyc1, skyl1, feel, valid
        FROM ranked WHERE rk = 1
    """)

    # Range strikes + 12 EDT price
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
             WHERE p.slug = r.slug
               AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p12
        FROM r
    """)

    # Per-day favorite + ladder shape
    con.execute("""
        CREATE OR REPLACE TEMP TABLE day_feat AS
        WITH favs AS (
            SELECT local_day,
                   arg_max(strike, p12) AS fav_strike,
                   max(p12)             AS fav_p,
                   arg_max(lo_f, p12)   AS fav_lo,
                   arg_max(hi_f, p12)   AS fav_hi,
                   COUNT(*) FILTER (WHERE p12 >= 0.10) AS n_over_10c
            FROM range_12
            WHERE p12 IS NOT NULL
            GROUP BY 1
        )
        SELECT
            f.*, md.day_max_whole,
            (md.day_max_whole - f.fav_lo) AS signed_gap,
            (md.day_max_whole - (f.fav_lo + f.fav_hi)/2.0) AS signed_gap_center,
            ABS(md.day_max_whole - (f.fav_lo + f.fav_hi)/2.0) AS abs_gap,
            CASE WHEN md.day_max_whole BETWEEN f.fav_lo AND f.fav_hi THEN 1 ELSE 0 END AS fav_hit,
            m12.tmpf, m12.dwpf, m12.relh, m12.sknt, m12.drct, m12.p01i, m12.skyc1, m12.feel,
            CASE
                WHEN m12.skyc1 IN ('CLR', 'FEW') THEN 'clear'
                WHEN m12.skyc1 = 'SCT' THEN 'scattered'
                WHEN m12.skyc1 = 'BKN' THEN 'broken'
                WHEN m12.skyc1 = 'OVC' THEN 'overcast'
                ELSE 'unknown'
            END AS sky_bucket,
            -- Temp gap from current to favorite's lower bound (how much rise needed?)
            (f.fav_lo - m12.tmpf) AS rise_needed_to_fav
        FROM favs f
        LEFT JOIN metar_daily md ON md.local_date = f.local_day
        LEFT JOIN metar_12edt m12 ON m12.local_date = f.local_day
        WHERE md.day_max_whole IS NOT NULL
    """)


def overview(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== UPWARD-BIAS OVERVIEW (all 55 scorable days, not just peaked) ===")
    print("    signed_gap = day_max - fav_lo.")
    print("    Positive → day got hotter than the favorite bucket's low edge.")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(signed_gap), 2) AS mean_signed_gap,
            ROUND(AVG(signed_gap_center), 2) AS mean_signed_gap_center,
            ROUND(AVG(abs_gap), 2) AS mean_abs_gap,
            ROUND(STDDEV(signed_gap), 2) AS std_signed_gap,
            COUNT(*) FILTER (WHERE signed_gap > 0) AS n_upward,
            COUNT(*) FILTER (WHERE signed_gap < 0) AS n_downward,
            COUNT(*) FILTER (WHERE signed_gap = 0) AS n_at_lower_edge
        FROM day_feat
    """).df())


def by_sky(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== GAP BY SKY COVER AT 12 EDT ===")
    print("    Hypothesis: clear skies → bigger upward afternoon rise than forecast.")
    print(con.execute("""
        SELECT sky_bucket, COUNT(*) AS n,
               ROUND(AVG(signed_gap), 2) AS mean_signed_gap,
               ROUND(AVG(signed_gap_center), 2) AS mean_gap_from_center,
               ROUND(AVG(abs_gap), 2) AS mean_abs_gap,
               ROUND(AVG(fav_hit), 3) AS fav_hit_rate
        FROM day_feat
        GROUP BY sky_bucket ORDER BY sky_bucket
    """).df())


def by_relh_tercile(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== GAP BY RELATIVE HUMIDITY TERCILE ===")
    print(con.execute("""
        WITH q AS (
            SELECT NTILE(3) OVER (ORDER BY relh) AS tercile, *
            FROM day_feat WHERE relh IS NOT NULL
        )
        SELECT tercile, COUNT(*) AS n,
               ROUND(AVG(relh), 2) AS avg_relh,
               ROUND(AVG(signed_gap), 2) AS mean_signed_gap,
               ROUND(AVG(fav_hit), 3) AS fav_hit_rate
        FROM q GROUP BY tercile ORDER BY tercile
    """).df())


def by_tmpf_at_12(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== GAP BY 12 EDT TEMPERATURE TERCILE (cold mornings vs warm mornings) ===")
    print(con.execute("""
        WITH q AS (
            SELECT NTILE(3) OVER (ORDER BY tmpf) AS tercile, *
            FROM day_feat WHERE tmpf IS NOT NULL
        )
        SELECT tercile, COUNT(*) AS n,
               ROUND(AVG(tmpf), 1) AS avg_tmpf_12,
               ROUND(AVG(day_max_whole), 1) AS avg_day_max,
               ROUND(AVG(signed_gap), 2) AS mean_signed_gap,
               ROUND(AVG(fav_hit), 3) AS fav_hit_rate
        FROM q GROUP BY tercile ORDER BY tercile
    """).df())


def by_wind_direction(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== GAP BY WIND DIRECTION AT 12 EDT ===")
    print("    Onshore (90-180° = E to S): sea breeze, dampens afternoon rise at LGA.")
    print("    Offshore (270-360° = W to N): warm advection, supports rise.")
    print(con.execute("""
        SELECT
            CASE
                WHEN drct BETWEEN 0 AND 45 THEN '1: N (offshore)'
                WHEN drct BETWEEN 45 AND 135 THEN '2: E (onshore)'
                WHEN drct BETWEEN 135 AND 225 THEN '3: S (onshore)'
                WHEN drct BETWEEN 225 AND 315 THEN '4: W (offshore)'
                WHEN drct > 315 THEN '5: NW (offshore)'
                ELSE 'unknown'
            END AS wind_sector,
            COUNT(*) AS n,
            ROUND(AVG(signed_gap), 2) AS mean_signed_gap,
            ROUND(AVG(fav_hit), 3) AS fav_hit_rate
        FROM day_feat
        WHERE drct IS NOT NULL
        GROUP BY wind_sector ORDER BY wind_sector
    """).df())


def by_rise_needed(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== GAP BY 'RISE NEEDED' TO FAV BUCKET (fav_lo - current_tmpf at 12 EDT) ===")
    print("    Hypothesis: when the favorite is much ABOVE current, market is forecast-")
    print("    anchored and the gap may be wider.")
    print(con.execute("""
        SELECT
            CASE
                WHEN rise_needed_to_fav < 0 THEN '1: fav below current (strange)'
                WHEN rise_needed_to_fav < 2 THEN '2: rise < 2°F'
                WHEN rise_needed_to_fav < 5 THEN '3: rise 2-5°F'
                WHEN rise_needed_to_fav < 10 THEN '4: rise 5-10°F'
                ELSE '5: rise >10°F'
            END AS band,
            COUNT(*) AS n,
            ROUND(AVG(rise_needed_to_fav), 2) AS avg_rise_needed,
            ROUND(AVG(signed_gap), 2) AS mean_signed_gap,
            ROUND(AVG(fav_hit), 3) AS fav_hit_rate
        FROM day_feat
        WHERE rise_needed_to_fav IS NOT NULL
        GROUP BY band ORDER BY band
    """).df())


def peaked_rows(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== PEAKED-LADDER DAYS WITH METAR 12 EDT CONTEXT ===")
    print(con.execute("""
        SELECT local_day, fav_strike, ROUND(fav_p,3) AS fav_p, day_max_whole AS day_max,
               signed_gap AS gap,
               fav_hit AS hit,
               ROUND(tmpf, 0) AS tmpf_12,
               ROUND(dwpf, 0) AS dwpf_12,
               ROUND(relh, 0) AS relh_12,
               ROUND(sknt, 0) AS wind_kts,
               drct AS wind_dir,
               skyc1 AS sky
        FROM day_feat
        WHERE fav_p >= 0.60 AND n_over_10c <= 2
        ORDER BY local_day
    """).df())


def correlation_matrix(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== CORRELATIONS OF METAR FEATURES WITH signed_gap (all 55 days) ===")
    print(con.execute("""
        SELECT
            ROUND(CORR(signed_gap, tmpf), 3)  AS corr_tmpf,
            ROUND(CORR(signed_gap, dwpf), 3)  AS corr_dwpf,
            ROUND(CORR(signed_gap, relh), 3)  AS corr_relh,
            ROUND(CORR(signed_gap, sknt), 3)  AS corr_sknt,
            ROUND(CORR(signed_gap, rise_needed_to_fav), 3) AS corr_rise_needed,
            ROUND(CORR(signed_gap, fav_p), 3) AS corr_fav_p
        FROM day_feat
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    overview(con)
    by_sky(con)
    by_relh_tercile(con)
    by_tmpf_at_12(con)
    by_wind_direction(con)
    by_rise_needed(con)
    peaked_rows(con)
    correlation_matrix(con)


if __name__ == "__main__":
    main()

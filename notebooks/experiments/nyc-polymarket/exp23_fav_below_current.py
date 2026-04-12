"""Experiment 23 — Investigate the 6 "fav below current" days.

Exp12 showed 6 days where the 12 EDT market favorite's lower bound was
BELOW the current LGA temperature. Mean gap: +16°F (vs +4°F average).
0% hit rate. These are extreme mispricings that, if prospectively
identifiable, could be a huge edge play.

Question: are these real market errors or stale-book artifacts? And
is there a common prospective feature?

Method:
    1. List the 6 days, pull context (market, fills, METAR)
    2. Check whether each day had active 12 EDT book trading
    3. Look at per-day ladder shape to see if the market is actually
       pricing a "cooling trend"
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"


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
        CREATE OR REPLACE TEMP VIEW metar_12edt AS
        WITH ranked AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   valid, tmpf, dwpf, relh, sknt, drct, skyc1,
                   ROW_NUMBER() OVER (
                       PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                       ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                   ) AS rk
            FROM '{METAR}' WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT local_date, valid AS ts_utc, tmpf, dwpf, relh, sknt, drct, skyc1
        FROM ranked WHERE rk=1
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

    # Find the fav-below-current days
    print("=== DAYS WHERE FAV_LO < TMPF_12 (market favorite is COLDER than now) ===")
    offending = con.execute("""
        WITH favs AS (
            SELECT local_day,
                   arg_max(strike, p12_mid) AS fav_strike,
                   max(p12_mid) AS fav_p,
                   arg_max(lo_f, p12_mid) AS fav_lo,
                   arg_max(hi_f, p12_mid) AS fav_hi,
                   arg_max(slug, p12_mid) AS fav_slug
            FROM range_12 WHERE p12_mid IS NOT NULL GROUP BY 1
        )
        SELECT
            f.local_day, f.fav_strike, ROUND(f.fav_p,3) AS fav_p,
            md.day_max_whole AS actual_max,
            m12.tmpf AS tmpf_12,
            (f.fav_lo - m12.tmpf) AS rise_needed,
            m12.dwpf, m12.relh, m12.sknt, m12.drct, m12.skyc1,
            f.fav_slug
        FROM favs f
        JOIN metar_daily md ON md.local_date = f.local_day
        JOIN metar_12edt m12 ON m12.local_date = f.local_day
        WHERE (f.fav_lo - m12.tmpf) < 0
        ORDER BY f.local_day
    """).df()
    print(offending)

    if len(offending) == 0:
        print("   (none found)")
        return

    print("\n=== PER-DAY BOOK ACTIVITY FOR FAV AROUND 12 EDT ===")
    for _, r in offending.iterrows():
        fav_slug = r["fav_slug"]
        day_str = str(r["local_day"])[:10]
        print(f"\n-- {day_str} {r['fav_strike']} (tmpf_12={r['tmpf_12']}, fav_lo={r['fav_strike'][:2]}, actual_max={r['actual_max']}) --")
        # fills in the hour around 12 EDT
        window_start = f"{day_str} 15:30:00+00"
        window_end   = f"{day_str} 16:30:00+00"
        fills = con.execute(f"""
            SELECT COUNT(*) AS n, ROUND(AVG(price), 3) AS avg_price,
                   ROUND(MIN(price), 3) AS min_p, ROUND(MAX(price), 3) AS max_p,
                   ROUND(SUM(usd), 2) AS usd_vol
            FROM '{FILLS}' f
            WHERE f.slug = '{fav_slug}'
              AND f.timestamp BETWEEN TIMESTAMPTZ '{window_start}' AND TIMESTAMPTZ '{window_end}'
        """).df()
        print(f"   fav book ±30min 12 EDT: {fills.iloc[0].to_dict()}")

    # Also: what does the ladder look like on these days — is the market predicting cooling?
    print("\n=== LADDER SHAPE ON FAV-BELOW-CURRENT DAYS ===")
    print(con.execute(f"""
        WITH days AS (
            SELECT DISTINCT local_day FROM range_12
            WHERE local_day IN (
                {",".join(f"DATE '{str(d)[:10]}'" for d in offending['local_day'])}
            )
        )
        SELECT
            r.local_day, r.strike, ROUND(r.p12_mid, 3) AS p,
            r.lo_f, r.hi_f,
            m12.tmpf AS tmpf_12
        FROM range_12 r
        JOIN days d USING (local_day)
        JOIN metar_12edt m12 ON m12.local_date = r.local_day
        WHERE r.p12_mid IS NOT NULL AND r.p12_mid >= 0.01
        ORDER BY r.local_day, r.lo_f
    """).df().to_string(index=False))


if __name__ == "__main__":
    main()

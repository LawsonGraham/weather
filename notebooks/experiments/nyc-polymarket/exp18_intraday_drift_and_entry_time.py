"""Experiment 18 — Intraday favorite drift + optimal Strategy D entry time.

Strategy D (exp13/14/16/17) fires at 12 EDT. But is 12 EDT the BEST clock
hour to enter? Earlier entries might catch more upward moves; later
entries are better informed but the market may have already corrected.

Two questions:

1. **Favorite stability**: how often does the 12 EDT favorite match the
   06/10/14/16 EDT favorite? If the favorite is volatile intraday, we
   can choose when to "commit".

2. **Strategy D by entry hour**: what's the D cum_pnl if we enter at
   06 / 08 / 10 / 12 / 14 / 16 / 18 EDT instead of 12 EDT fixed?

Bonus:

3. **Lo-of-fav drift**: how much does `fav_lo` change between snapshots?
   This is the observable version of the "the market is updating its
   forecast" — big shifts = big new information.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

SPREAD = 0.03
FEE = 0.02
SNAPSHOT_HOURS_UTC = [10, 12, 14, 16, 18, 20, 22]  # UTC; EDT = UTC - 4
# i.e., 06 EDT / 08 EDT / 10 EDT / 12 EDT / 14 EDT / 16 EDT / 18 EDT


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
        CREATE OR REPLACE TEMP TABLE nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)

    # Per snapshot, per slug: price at that UTC hour
    for h in SNAPSHOT_HOURS_UTC:
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE prices_h{h} AS
            SELECT nr.slug, nr.strike, nr.lo_f, nr.hi_f, nr.local_day,
                (SELECT yes_price FROM '{PRICES}' p
                 WHERE p.slug = nr.slug
                   AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '{h} hour')
                 ORDER BY p.timestamp DESC LIMIT 1) AS p_h
            FROM nyc_range nr
        """)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE fav_h{h} AS
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p_h DESC NULLS LAST) AS rk
                FROM prices_h{h} WHERE p_h IS NOT NULL
            )
            SELECT local_day, lo_f AS fav_lo, hi_f AS fav_hi, strike AS fav_strike, p_h AS fav_p
            FROM ranked WHERE rk = 1
        """)


def favorite_stability(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FAVORITE STABILITY — how often does fav_lo match across snapshots? ===")
    # Compare fav_h16 (12 EDT) to each other hour
    for h in SNAPSHOT_HOURS_UTC:
        if h == 16:
            continue
        edt = h - 4
        r = con.execute(f"""
            SELECT
                COUNT(*) AS n,
                COUNT(*) FILTER (WHERE a.fav_lo = b.fav_lo) AS same,
                ROUND(AVG(ABS(a.fav_lo - b.fav_lo))::FLOAT, 2) AS mean_abs_diff,
                ROUND(AVG((a.fav_lo - b.fav_lo)::FLOAT), 2) AS mean_diff
            FROM fav_h16 a JOIN fav_h{h} b USING (local_day)
        """).df()
        r["hour_edt"] = f"{edt:02d} EDT (vs 12 EDT)"
        r["pct_same"] = (r["same"] / r["n"] * 100).round(1)
        print(r[["hour_edt", "n", "same", "pct_same", "mean_diff", "mean_abs_diff"]])


def strategy_d_by_hour(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY D BY ENTRY HOUR — where is the edge biggest? ===")
    print("    At each hour, buy `fav_lo+2` using that hour's price snapshot.")
    print("    Backtest metric: n, hit_rate, net_med, cum_pnl (3c + 2% cost).")

    rows = []
    for h in SNAPSHOT_HOURS_UTC:
        edt = h - 4
        q = f"""
            WITH trade AS (
                SELECT
                    f.local_day, f.fav_lo, f.fav_p,
                    ph.p_h, ph.lo_f, ph.hi_f, md.day_max_whole,
                    CASE WHEN md.day_max_whole BETWEEN ph.lo_f AND ph.hi_f THEN 1 ELSE 0 END AS y,
                    (ph.p_h + {SPREAD}) * (1 + {FEE}) AS entry_cost
                FROM fav_h{h} f
                JOIN prices_h{h} ph ON ph.local_day = f.local_day AND ph.lo_f = f.fav_lo + 2
                JOIN metar_daily md ON md.local_date = f.local_day
                WHERE ph.p_h IS NOT NULL
                  AND (ph.p_h + {SPREAD}) < 0.97
                  AND ph.p_h >= 0.02
            )
            SELECT
                '{edt:02d} EDT' AS hour,
                COUNT(*) AS n,
                ROUND(AVG(p_h), 3) AS avg_p_entry,
                ROUND(AVG(y), 3) AS hit_rate,
                ROUND(AVG(y/entry_cost - 1), 3) AS net_avg,
                ROUND(QUANTILE_CONT(y/entry_cost - 1, 0.5), 3) AS net_med,
                ROUND(SUM(y/entry_cost - 1), 2) AS cum_pnl
            FROM trade
        """
        r = con.execute(q).df()
        rows.append(r)
    out = pd.concat(rows, ignore_index=True)
    print(out)


def fav_lo_drift(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FAV_LO DRIFT — how much does the favorite's bucket shift through the day? ===")
    print(con.execute(f"""
        WITH cohort AS (
            SELECT
                a.local_day,
                a.fav_lo AS lo_06,
                b.fav_lo AS lo_10,
                c.fav_lo AS lo_14,
                d.fav_lo AS lo_16,
                e.fav_lo AS lo_18
            FROM fav_h10 a
            LEFT JOIN fav_h14 b USING (local_day)
            LEFT JOIN fav_h18 c USING (local_day)
            LEFT JOIN fav_h20 d USING (local_day)
            LEFT JOIN fav_h22 e USING (local_day)
        )
        SELECT
            COUNT(*) AS n,
            -- Delta from 06 EDT to 18 EDT
            ROUND(AVG(lo_18 - lo_06), 2) AS mean_06_to_18_shift,
            ROUND(STDDEV(lo_18 - lo_06), 2) AS std_06_to_18,
            -- Monotonic increase pattern
            COUNT(*) FILTER (WHERE lo_18 > lo_06) AS n_up,
            COUNT(*) FILTER (WHERE lo_18 = lo_06) AS n_flat,
            COUNT(*) FILTER (WHERE lo_18 < lo_06) AS n_down
        FROM cohort
    """).df())


def drift_table(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== DRIFT TABLE — lo_f trajectory per day ===")
    print(con.execute(f"""
        SELECT
            a.local_day,
            a.fav_lo AS lo_06,
            b.fav_lo AS lo_10,
            c.fav_lo AS lo_14,
            d.fav_lo AS lo_16,
            e.fav_lo AS lo_18,
            (SELECT day_max_whole FROM metar_daily WHERE local_date = a.local_day) AS actual_max
        FROM fav_h10 a
        LEFT JOIN fav_h14 b USING (local_day)
        LEFT JOIN fav_h18 c USING (local_day)
        LEFT JOIN fav_h20 d USING (local_day)
        LEFT JOIN fav_h22 e USING (local_day)
        ORDER BY a.local_day
        LIMIT 25
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    favorite_stability(con)
    fav_lo_drift(con)
    strategy_d_by_hour(con)
    drift_table(con)


if __name__ == "__main__":
    main()

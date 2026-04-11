"""Experiment 06b — Per-day bid/ask via LAST-FILL-BEFORE-t.

Exp06 reconstructed bid/ask from fills in a ±5min window using MAX/MIN,
which created non-physical negative spreads because price drift over the
window flipped the bid above the ask. The fix: for each slug on each day,
take the single last BUY-side fill and the single last SELL-side fill
strictly BEFORE the target snapshot time. That gives a point estimate
of bid/ask right AT the snapshot.

For Strategy D we're buying the +2 bucket (long YES), so the relevant
cost is the YES ask at the target hour. Reconstruct it as:
    last YES BUY price before target  (takers cross the book at ask)

Compare to the mid-style snapshot used in exp05/13 `(yes_price + 0.03)`.
If the real ask is close to mid + 0.03, the 3¢ placeholder is fine.
If it's mid + 0.08, the strategy PnL shrinks.
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
        CREATE OR REPLACE TEMP TABLE nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)

    # 12 EDT snapshot per slug
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE snap_12 AS
        SELECT nr.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug
               AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p12_mid,
            CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour' AS target_ts
        FROM nyc_range nr
    """)

    # Favorite at 12 EDT + strat D target (+2 bucket)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE fav AS
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p12_mid DESC NULLS LAST) AS rk
            FROM snap_12 WHERE p12_mid IS NOT NULL
        )
        SELECT local_day, lo_f AS fav_lo, hi_f AS fav_hi, strike AS fav_strike, p12_mid AS fav_p
        FROM ranked WHERE rk = 1
    """)

    # Strategy D target slug per day (the +2 bucket)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE d_target AS
        SELECT f.local_day, f.fav_lo,
               s.slug AS d_slug, s.strike AS d_strike,
               s.lo_f AS d_lo, s.hi_f AS d_hi, s.p12_mid AS d_mid_price, s.target_ts
        FROM fav f
        JOIN snap_12 s ON s.local_day = f.local_day AND s.lo_f = f.fav_lo + 2
        WHERE s.p12_mid IS NOT NULL AND s.p12_mid >= 0.02
    """)

    # Last YES BUY fill strictly before 12 EDT (= YES ask estimate)
    # Last YES SELL fill strictly before 12 EDT (= YES bid estimate)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE ba_reconstructed AS
        SELECT
            dt.local_day,
            dt.d_slug,
            dt.d_strike,
            dt.d_mid_price,
            (SELECT price FROM '{FILLS}' f
             WHERE f.slug = dt.d_slug
               AND f.timestamp <= dt.target_ts
               AND UPPER(f.outcome) = 'YES'
               AND UPPER(f.side) = 'BUY'
             ORDER BY f.timestamp DESC LIMIT 1) AS last_yes_buy,  -- YES ask
            (SELECT price FROM '{FILLS}' f
             WHERE f.slug = dt.d_slug
               AND f.timestamp <= dt.target_ts
               AND UPPER(f.outcome) = 'YES'
               AND UPPER(f.side) = 'SELL'
             ORDER BY f.timestamp DESC LIMIT 1) AS last_yes_sell, -- YES bid
            (SELECT timestamp FROM '{FILLS}' f
             WHERE f.slug = dt.d_slug
               AND f.timestamp <= dt.target_ts
               AND UPPER(f.outcome) = 'YES'
               AND UPPER(f.side) = 'BUY'
             ORDER BY f.timestamp DESC LIMIT 1) AS last_buy_ts,
            (SELECT timestamp FROM '{FILLS}' f
             WHERE f.slug = dt.d_slug
               AND f.timestamp <= dt.target_ts
               AND UPPER(f.outcome) = 'YES'
               AND UPPER(f.side) = 'SELL'
             ORDER BY f.timestamp DESC LIMIT 1) AS last_sell_ts
        FROM d_target dt
    """)


def report_spreads(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== PER-DAY ASK (YES BUY price) AND BID (YES SELL price) BEFORE 12 EDT ===")
    df = con.execute("""
        SELECT local_day, d_strike,
               ROUND(d_mid_price, 3) AS mid,
               ROUND(last_yes_buy, 3) AS ask,
               ROUND(last_yes_sell, 3) AS bid,
               ROUND(last_yes_buy - last_yes_sell, 3) AS spread,
               ROUND(last_yes_buy - d_mid_price, 3) AS ask_vs_mid
        FROM ba_reconstructed
        ORDER BY local_day
    """).df()
    print(df.to_string(index=False))


def summary(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== SPREAD SUMMARY ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_trades,
            COUNT(*) FILTER (WHERE last_yes_buy IS NOT NULL)  AS n_with_ask,
            COUNT(*) FILTER (WHERE last_yes_sell IS NOT NULL) AS n_with_bid,
            ROUND(AVG(last_yes_buy - last_yes_sell), 4) AS mean_spread,
            ROUND(QUANTILE_CONT(last_yes_buy - last_yes_sell, 0.5), 4) AS med_spread,
            ROUND(QUANTILE_CONT(last_yes_buy - last_yes_sell, 0.75), 4) AS p75_spread,
            ROUND(AVG(last_yes_buy - d_mid_price), 4) AS mean_ask_vs_mid,
            ROUND(QUANTILE_CONT(last_yes_buy - d_mid_price, 0.5), 4) AS med_ask_vs_mid
        FROM ba_reconstructed
        WHERE last_yes_buy IS NOT NULL AND last_yes_sell IS NOT NULL
    """).df())


def strategy_d_with_real_ask(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY D — using real last YES BUY price as entry (instead of mid+3¢) ===")
    print("    Real-ask entry cost = last_yes_buy * (1 + fee). Compare to placeholder 3¢.")
    print(con.execute(f"""
        WITH paired AS (
            SELECT br.local_day,
                   br.last_yes_buy AS real_ask,
                   (br.d_mid_price + 0.03) AS placeholder_ask,
                   md.day_max_whole,
                   dt.d_lo, dt.d_hi,
                   CASE WHEN md.day_max_whole BETWEEN dt.d_lo AND dt.d_hi THEN 1 ELSE 0 END AS y
            FROM ba_reconstructed br
            JOIN d_target dt ON dt.local_day = br.local_day
            JOIN metar_daily md ON md.local_date = br.local_day
            WHERE br.last_yes_buy IS NOT NULL AND md.day_max_whole IS NOT NULL
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(real_ask), 3) AS avg_real_ask,
            ROUND(AVG(placeholder_ask), 3) AS avg_placeholder_ask,
            ROUND(AVG(1-y), 3) AS miss,
            ROUND(AVG(y), 3) AS hit,
            ROUND(AVG((y/(real_ask * (1 + {FEE}))) - 1), 3) AS real_net_avg,
            ROUND(QUANTILE_CONT((y/(real_ask * (1 + {FEE}))) - 1, 0.5), 3) AS real_net_med,
            ROUND(SUM((y/(real_ask * (1 + {FEE}))) - 1), 2) AS real_cum,
            ROUND(AVG((y/(placeholder_ask * (1 + {FEE}))) - 1), 3) AS placeholder_net_avg,
            ROUND(QUANTILE_CONT((y/(placeholder_ask * (1 + {FEE}))) - 1, 0.5), 3) AS placeholder_net_med,
            ROUND(SUM((y/(placeholder_ask * (1 + {FEE}))) - 1), 2) AS placeholder_cum
        FROM paired
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    summary(con)
    report_spreads(con)
    strategy_d_with_real_ask(con)


if __name__ == "__main__":
    main()

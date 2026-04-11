"""Experiment 06 — Reconstruct actual bid/ask from fills for the 12 EDT fade.

Exp05 assumed a flat 3¢ spread haircut (half-spread paid when fading). That's
a placeholder. This exp replaces it with an empirical NO-side ask reconstructed
from actual fills in a ±5min window around 12 EDT, per slug.

Method:
    For a fill on a YES token, UPPER(side)='BUY' means the taker paid price p per YES share
        → p is the YES ask at that moment → the NO bid is (1-p)
    For a YES token fill with UPPER(side)='SELL', the taker sold YES for p
        → p is the YES bid at that moment → the NO ask is (1-p)

So the NO-side ask (what a fader pays to buy NO) in a window is:
    min(1 - p) over SELL-side YES fills (= "lowest NO ask")
    = 1 - max(p) over SELL-side YES fills

And the NO-side bid is:
    max(1 - p) over BUY-side YES fills
    = 1 - min(p) over BUY-side YES fills

If no SELL fills in the window, use `1 - last_yes_price - half_spread_median`.

Questions this answers:
    1. What's the actual NO-side spread at 12 EDT for NYC daily-temp favorites?
    2. Is the 3¢ placeholder in exp05 conservative, accurate, or generous?
    3. Does the fade edge hold when we use real fills instead of a flat haircut?
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 50)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone='UTC'")

    # METAR day max (truth)
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

    # Range strikes only
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)

    # Find the 12 EDT favorite per day (argmax last yes_price ≤ 12 EDT)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE favs AS
        WITH snaps AS (
            SELECT nr.slug, nr.strike, nr.lo_f, nr.hi_f, nr.local_day,
                   (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour') AS t12
            FROM nyc_range nr
        ),
        priced AS (
            SELECT s.*,
                (SELECT yes_price FROM '{PRICES}' p
                 WHERE p.slug=s.slug AND p.timestamp <= s.t12
                 ORDER BY p.timestamp DESC LIMIT 1) AS p12
            FROM snaps s
        )
        SELECT local_day,
               arg_max(slug, p12)   AS fav_slug,
               arg_max(strike, p12) AS fav_strike,
               max(p12)             AS fav_p12,
               arg_max(lo_f, p12)   AS fav_lo,
               arg_max(hi_f, p12)   AS fav_hi,
               (CAST(local_day AS TIMESTAMPTZ) + INTERVAL '16 hour') AS t12
        FROM priced
        WHERE p12 IS NOT NULL
        GROUP BY local_day
    """)

    # Fills in ±5min around 12 EDT for each day's favorite.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE fav_fills AS
        SELECT f.slug, f.timestamp, f.outcome, f.side, f.price, f.usd,
               fv.local_day, fv.fav_p12, fv.t12, fv.fav_lo, fv.fav_hi
        FROM favs fv
        JOIN '{FILLS}' f
          ON f.slug = fv.fav_slug
         AND f.timestamp BETWEEN fv.t12 - INTERVAL '5 minute' AND fv.t12 + INTERVAL '5 minute'
    """)


def coverage(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== FAVORITES WITH FILL COVERAGE (±5min around 12 EDT) ===")
    print(con.execute("""
        SELECT
            (SELECT COUNT(*) FROM favs) AS n_favs,
            COUNT(DISTINCT local_day) AS n_days_with_fills,
            COUNT(*) AS n_total_fills,
            ROUND(AVG(price), 3) AS avg_fill_price,
            COUNT(DISTINCT outcome) AS n_outcomes_seen
        FROM fav_fills
    """).df())

    print("\n=== fill side × outcome breakdown ===")
    print(con.execute("""
        SELECT outcome, side, COUNT(*) AS n, ROUND(AVG(price),3) AS avg_price
        FROM fav_fills GROUP BY 1, 2 ORDER BY 1, 2
    """).df())


def spread_estimate(con: duckdb.DuckDBPyConnection) -> None:
    # For each day, take YES outcome fills in the ±5min window.
    # NO ask = 1 - (max YES SELL price in window)  [crossing the book on NO buy]
    # NO bid = 1 - (min YES BUY price in window)
    # Half-spread = (NO ask - NO bid) / 2
    print("\n=== PER-DAY IMPLIED NO-SIDE SPREAD AT 12 EDT ===")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE per_day_spread AS
        WITH yes_fills AS (
            SELECT local_day, side, price
            FROM fav_fills
            WHERE UPPER(outcome) = 'YES'
        ),
        agg AS (
            SELECT local_day,
                   1 - MAX(price) FILTER (WHERE UPPER(side)='SELL') AS no_ask,
                   1 - MIN(price) FILTER (WHERE UPPER(side)='BUY')  AS no_bid,
                   COUNT(*) FILTER (WHERE UPPER(side)='SELL') AS n_sell,
                   COUNT(*) FILTER (WHERE UPPER(side)='BUY')  AS n_buy
            FROM yes_fills
            GROUP BY local_day
        )
        SELECT * FROM agg
    """)
    print(con.execute("""
        SELECT
            COUNT(*) AS n_days,
            COUNT(*) FILTER (WHERE no_ask IS NOT NULL) AS n_with_no_ask,
            COUNT(*) FILTER (WHERE no_bid IS NOT NULL) AS n_with_no_bid,
            ROUND(AVG(no_ask - no_bid), 4) AS avg_full_spread,
            ROUND(AVG((no_ask - no_bid)/2), 4) AS avg_half_spread,
            ROUND(QUANTILE_CONT(no_ask - no_bid, 0.5), 4) AS med_full_spread,
            ROUND(QUANTILE_CONT(no_ask - no_bid, 0.75), 4) AS p75_full_spread
        FROM per_day_spread
        WHERE no_ask IS NOT NULL AND no_bid IS NOT NULL
    """).df())


def net_return_with_real_ask(con: duckdb.DuckDBPyConnection) -> None:
    print(f"\n=== NET FADE RETURN WITH REAL NO-ASK AT 12 EDT (fee {FEE*100:.0f}%) ===")
    print("""    Uses the actual `no_ask` reconstructed from YES SELL fills in the ±5min
        window. Days with no SELL fills in window are excluded.
        entry_cost = no_ask * (1 + fee)
        payoff      = 1 - y
        net_ret     = payoff / entry_cost - 1
    """)
    print(con.execute(f"""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(fv.fav_p12), 3) AS avg_p_fav,
            ROUND(AVG(pds.no_ask), 3) AS avg_no_ask,
            ROUND(AVG(pds.no_ask - (1 - fv.fav_p12)), 3) AS avg_ask_above_mid,
            ROUND(AVG(1 - y), 3) AS miss_rate,
            ROUND(AVG((1 - y) / (pds.no_ask * (1 + {FEE})) - 1), 3) AS net_avg_ret,
            ROUND(QUANTILE_CONT((1 - y) / (pds.no_ask * (1 + {FEE})) - 1, 0.5), 3) AS net_med_ret,
            ROUND(SUM((1 - y) / (pds.no_ask * (1 + {FEE})) - 1), 2) AS net_cum
        FROM favs fv
        JOIN per_day_spread pds ON pds.local_day = fv.local_day
        JOIN metar_daily md ON md.local_date = fv.local_day
        CROSS JOIN LATERAL (SELECT CASE WHEN md.day_max_whole BETWEEN fv.fav_lo AND fv.fav_hi THEN 1 ELSE 0 END AS y) x
        WHERE pds.no_ask IS NOT NULL AND md.day_max_whole IS NOT NULL AND pds.no_ask < 0.99
    """).df())


def compare_to_exp05(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== SAME DAYS — compare exp05 3c placeholder vs real NO ask ===")
    print(con.execute("""
        WITH base AS (
            SELECT fv.local_day, fv.fav_p12,
                   CASE WHEN md.day_max_whole BETWEEN fv.fav_lo AND fv.fav_hi THEN 1 ELSE 0 END AS y,
                   pds.no_ask AS real_ask,
                   (1 - fv.fav_p12 + 0.03) AS placeholder_ask
            FROM favs fv
            JOIN per_day_spread pds ON pds.local_day = fv.local_day
            JOIN metar_daily md ON md.local_date = fv.local_day
            WHERE pds.no_ask IS NOT NULL AND md.day_max_whole IS NOT NULL
              AND pds.no_ask < 0.99
        )
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(real_ask - placeholder_ask), 3) AS mean_real_minus_placeholder,
            ROUND(MIN(real_ask - placeholder_ask), 3) AS min_diff,
            ROUND(MAX(real_ask - placeholder_ask), 3) AS max_diff,
            -- net_med using real vs placeholder
            ROUND(QUANTILE_CONT((1-y)/(real_ask*1.02) - 1, 0.5), 3) AS real_net_med,
            ROUND(QUANTILE_CONT((1-y)/(placeholder_ask*1.02) - 1, 0.5), 3) AS placeholder_net_med
        FROM base
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    coverage(con)
    spread_estimate(con)
    net_return_with_real_ask(con)
    compare_to_exp05(con)


if __name__ == "__main__":
    main()

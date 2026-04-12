"""Experiment 39 — Who's trading these markets and are they winning?

Aggregate NYC daily-temp fills by taker address. Identify the biggest
traders by volume. For each, compute their realized PnL across the
55-day window: did they net YES on winning buckets, or did they net
YES on losing buckets?

If most volume comes from a few addresses that systematically LOSE,
the market is a "fish market" — Strategy D is competing against
retail. If a few addresses systematically WIN, they're our actual
competitors and we should look at what they do differently.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
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

    # NYC daily-temp range strikes with their bounds
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE nyc_strikes AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)

    print("\n=== TOTAL TAKERS / FILLS / VOLUME ===")
    print(con.execute(f"""
        SELECT
            COUNT(DISTINCT taker) AS distinct_takers,
            COUNT(*) AS total_fills,
            ROUND(SUM(usd), 0) AS total_usd
        FROM '{FILLS}' f JOIN nyc_strikes USING (slug)
        WHERE f.taker IS NOT NULL
    """).df())

    print("\n=== TOP 20 TAKERS BY VOLUME ===")
    print(con.execute(f"""
        SELECT
            taker,
            COUNT(*) AS n_fills,
            ROUND(SUM(usd), 0) AS total_usd,
            COUNT(DISTINCT slug) AS distinct_slugs,
            COUNT(DISTINCT CAST(timestamp AS DATE)) AS active_days
        FROM '{FILLS}' f JOIN nyc_strikes USING (slug)
        WHERE f.taker IS NOT NULL
        GROUP BY taker
        ORDER BY total_usd DESC
        LIMIT 20
    """).df())

    print("\n=== TAKER WIN/LOSS — top 30 by volume ===")
    print("    For each taker, sum YES-buy on winning strikes minus YES-buy on losing strikes.")
    print("    Positive = trader was on the right side. Negative = systematically losing.")
    print(con.execute(f"""
        WITH ranked AS (
            SELECT
                f.taker,
                f.slug,
                f.outcome,
                f.side,
                f.usd,
                ns.lo_f,
                ns.hi_f,
                md.day_max_whole,
                CASE WHEN md.day_max_whole BETWEEN ns.lo_f AND ns.hi_f THEN 1 ELSE 0 END AS strike_won
            FROM '{FILLS}' f
            JOIN nyc_strikes ns USING (slug)
            JOIN metar_daily md ON md.local_date = ns.local_day
            WHERE f.taker IS NOT NULL
        ),
        per_taker AS (
            SELECT
                taker,
                COUNT(*) AS n_fills,
                ROUND(SUM(usd), 0) AS total_usd,
                ROUND(SUM(CASE WHEN UPPER(outcome)='YES' AND UPPER(side)='BUY' AND strike_won=1 THEN usd ELSE 0 END), 0)
                    AS yes_buy_winners_usd,
                ROUND(SUM(CASE WHEN UPPER(outcome)='YES' AND UPPER(side)='BUY' AND strike_won=0 THEN usd ELSE 0 END), 0)
                    AS yes_buy_losers_usd,
                ROUND(SUM(CASE WHEN UPPER(outcome)='NO' AND UPPER(side)='BUY' AND strike_won=0 THEN usd ELSE 0 END), 0)
                    AS no_buy_winners_usd,
                ROUND(SUM(CASE WHEN UPPER(outcome)='NO' AND UPPER(side)='BUY' AND strike_won=1 THEN usd ELSE 0 END), 0)
                    AS no_buy_losers_usd
            FROM ranked
            GROUP BY taker
        )
        SELECT
            taker,
            n_fills,
            total_usd,
            yes_buy_winners_usd AS yes_win_usd,
            yes_buy_losers_usd  AS yes_lose_usd,
            no_buy_winners_usd  AS no_win_usd,
            no_buy_losers_usd   AS no_lose_usd,
            ROUND(yes_buy_winners_usd / NULLIF(yes_buy_winners_usd + yes_buy_losers_usd, 0), 3) AS yes_win_rate
        FROM per_taker
        ORDER BY total_usd DESC
        LIMIT 30
    """).df())


if __name__ == "__main__":
    main()

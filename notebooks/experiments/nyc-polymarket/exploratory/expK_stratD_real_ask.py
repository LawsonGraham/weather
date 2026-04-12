"""Exploratory K — Strategy D V1 replay vs real top-of-ask from tob.

Strategy D V1 = at 16 EDT, buy the favorite+2 bucket at the 2%-fee-adjusted
midpoint. Historical backtests (exp14, exp40) showed ~+$3.36/trade at 46%
hit rate on 28 days of NYC data.

The 2%-fee assumption was a wild guess — replay at 16 EDT for the three
days we have live book data (april-11, 12, 13) and see what the REAL ask
was on the fav+2 bucket. Compare to the midpoint+2% estimate the backtest
used.

If the real ask is materially above (midpoint * 1.02), Strategy D V1's
backtested PnL is over-stated and needs to be down-revised.

Data: data/processed/polymarket_book/tob (the transform output).
Window: ~20:00 UTC = 16 EDT for april-11, and whenever we have coverage
for 12/13 (we don't have tob for 12/13 at 16 EDT, only for "now" = 21:00
UTC). We'll take what we can get.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

TOB = "data/processed/polymarket_book/tob/**/*.parquet"
MARKETS = "data/processed/polymarket_weather/markets.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW yes_tob AS
        WITH ym AS (
            SELECT slug, yes_token_id
            FROM '{MARKETS}'
            WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%'
        )
        SELECT t.received_at, t.slug, t.best_bid, t.best_ask, t.mid,
               regexp_extract(t.slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md,
               regexp_extract(t.slug, 'nyc-on-[a-z]+-[0-9]+-[0-9]+-(.+)', 1) AS strike,
               -- bucket low bound (even F for pair buckets, ignore for forbelow/forhigher)
               CASE WHEN strike LIKE '%forbelow'  THEN -999
                    WHEN strike LIKE '%forhigher' THEN CAST(regexp_extract(strike, '([0-9]+)', 1) AS INT)
                    ELSE CAST(regexp_extract(strike, '([0-9]+)-', 1) AS INT) END AS lo_f
        FROM '{TOB}' t
        INNER JOIN ym ON ym.slug = t.slug AND ym.yes_token_id = t.asset_id
        WHERE t.best_ask IS NOT NULL AND t.best_bid IS NOT NULL
    """)

    print("=== tob coverage window by market-date ===")
    print(con.execute("""
        SELECT md, COUNT(*) AS n, MIN(received_at) AS t0, MAX(received_at) AS t1
        FROM yes_tob GROUP BY md ORDER BY md
    """).df())

    # For each market-date, pick a snapshot at or near 20:00 UTC (16 EDT).
    # If 20:00 isn't available, take the earliest available.
    con.execute("""
        CREATE OR REPLACE TEMP VIEW snaps AS
        WITH ranked AS (
            SELECT md, slug, strike, lo_f, best_bid, best_ask, mid, received_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY md, slug
                       ORDER BY ABS(DATE_DIFF('second', received_at, TIMESTAMPTZ '2026-04-11 20:00:00+00:00'))
                   ) AS rn
            FROM yes_tob
        )
        SELECT md, slug, strike, lo_f, best_bid, best_ask, mid, received_at
        FROM ranked WHERE rn = 1
    """)

    # Find the favorite lo_f per market-date (max mid, excluding the forhigher/forbelow cases)
    print("\n=== 16 EDT snapshot: favorite + real ask + Strategy D +2 bucket ===")
    print(con.execute("""
        WITH fav AS (
            SELECT md,
                   arg_max(lo_f, mid) AS fav_lo,
                   MAX(mid) AS fav_mid
            FROM snaps
            WHERE strike NOT LIKE '%forbelow' AND strike NOT LIKE '%forhigher'
            GROUP BY md
        )
        SELECT f.md,
               f.fav_lo,
               ROUND(f.fav_mid, 3) AS fav_mid,
               ROUND(fav_s.best_bid, 3) AS fav_bid,
               ROUND(fav_s.best_ask, 3) AS fav_ask,
               ROUND(fav_s.best_ask - fav_s.best_bid, 3) AS fav_sprd,
               ROUND(target.mid, 3) AS tgt_mid,
               ROUND(target.best_bid, 3) AS tgt_bid,
               ROUND(target.best_ask, 3) AS tgt_ask,
               ROUND(target.best_ask - target.best_bid, 3) AS tgt_sprd,
               ROUND(target.best_ask - target.mid, 3) AS ask_mid_gap
        FROM fav f
        JOIN snaps fav_s    ON fav_s.md = f.md    AND fav_s.lo_f = f.fav_lo
        JOIN snaps target   ON target.md = f.md   AND target.lo_f = f.fav_lo + 2
    """).df())

    # Compute "what the backtest assumes vs what real execution cost is"
    print("\n=== backtest-assumed cost vs real-ask cost (Strategy D V1, 16 EDT) ===")
    print(con.execute("""
        WITH fav AS (
            SELECT md, arg_max(lo_f, mid) AS fav_lo
            FROM snaps
            WHERE strike NOT LIKE '%forbelow' AND strike NOT LIKE '%forhigher'
            GROUP BY md
        )
        SELECT f.md,
               ROUND(target.mid, 4) AS tgt_mid,
               ROUND(target.mid * 1.02, 4) AS bt_cost,
               ROUND(target.best_ask, 4) AS real_ask,
               ROUND(target.best_ask - target.mid * 1.02, 4) AS cost_gap,
               ROUND((target.best_ask - target.mid * 1.02) / NULLIF(target.mid * 1.02, 0), 3) AS cost_gap_pct
        FROM fav f
        JOIN snaps target ON target.md = f.md AND target.lo_f = f.fav_lo + 2
    """).df())

    # Detailed per-strike at 20:00 UTC on april-11 — for context
    print("\n=== april-11 full ladder at closest-to-20:00 snapshot ===")
    print(con.execute("""
        SELECT lo_f, strike,
               ROUND(best_bid, 3) AS bid,
               ROUND(mid, 3) AS mid,
               ROUND(best_ask, 3) AS ask,
               ROUND(best_ask - best_bid, 3) AS sprd,
               received_at
        FROM yes_tob
        WHERE md = 'april-11-2026'
          AND received_at BETWEEN '2026-04-11 19:59:55+00:00'
                               AND '2026-04-11 20:00:05+00:00'
        ORDER BY lo_f, received_at DESC
        LIMIT 22
    """).df())


if __name__ == "__main__":
    main()

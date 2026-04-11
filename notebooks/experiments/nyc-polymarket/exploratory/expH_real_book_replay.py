"""Exploratory H — real-book replay of the sell-pop + ladder-sum edges.

From exp G we observed a +1.9c sell-pop midpoint edge (65% hit rate).
From exp A we observed the ladder midpoint sum drifts 2-5c above 1.0
on active days. Both signals are in *midpoint* space. This script
replays them against real bid/ask data captured by the live WS book
recorder (data/processed/polymarket_book/tob) and asks:

  1. Does the sell-pop edge survive a taker-execution model?
     (sell at post-pop bid, buy back at t+10 ask)
  2. Does the ladder-sum overround produce a real-ASK arbitrage?
     (sum of all ASK prices across a day's buckets)
  3. What does the typical spread distribution look like, broken out
     by favorite-bucket vs tail-bucket?

Output: print summary stats. If either edge survives, write a follow-up
exploration with a full backtest.
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

    # YES-only view of top-of-book quotes
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW yes_tob AS
        WITH ym AS (
            SELECT slug, yes_token_id
            FROM '{MARKETS}'
            WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%'
        )
        SELECT t.received_at, t.slug, t.best_bid, t.best_ask, t.mid, t.spread,
               regexp_extract(t.slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md,
               regexp_extract(t.slug, 'nyc-on-[a-z]+-[0-9]+-[0-9]+-(.+)', 1) AS strike
        FROM '{TOB}' t
        INNER JOIN ym ON ym.slug = t.slug AND ym.yes_token_id = t.asset_id
        WHERE t.best_bid IS NOT NULL AND t.best_ask IS NOT NULL
    """)

    print("=== spread distribution by avg-mid regime (YES token) ===")
    print(con.execute("""
        SELECT
            CASE
                WHEN mid < 0.05 THEN 'floor [<0.05]'
                WHEN mid < 0.25 THEN 'tail [0.05-0.25]'
                WHEN mid < 0.50 THEN 'low [0.25-0.50]'
                WHEN mid < 0.75 THEN 'high [0.50-0.75]'
                WHEN mid < 0.95 THEN 'fav [0.75-0.95]'
                ELSE 'ceiling [>0.95]'
            END AS regime,
            COUNT(*) AS n,
            ROUND(AVG(spread), 4) AS mean_sp,
            ROUND(QUANTILE_CONT(spread, 0.5), 4) AS p50,
            ROUND(QUANTILE_CONT(spread, 0.95), 4) AS p95
        FROM yes_tob
        GROUP BY 1 ORDER BY 1
    """).df())

    # Snapshot to 1-second grid to reduce noise + dedup
    con.execute("""
        CREATE OR REPLACE TEMP VIEW sec_grid AS
        SELECT DISTINCT ON (slug, date_trunc('second', received_at))
               slug, md, strike,
               date_trunc('second', received_at) AS sec,
               best_bid, best_ask, mid, spread
        FROM yes_tob
        ORDER BY slug, date_trunc('second', received_at), received_at DESC
    """)

    # Compute 60-second-ahead mid move per second
    con.execute("""
        CREATE OR REPLACE TEMP VIEW moves AS
        SELECT
            slug, md, strike, sec, best_bid, best_ask, mid, spread,
            LAG(mid, 60) OVER w AS mid_m60,
            LEAD(mid, 60) OVER w AS mid_p60,
            LEAD(best_bid, 60) OVER w AS bid_p60,
            LEAD(best_ask, 60) OVER w AS ask_p60,
            LAG(best_bid, 60) OVER w AS bid_m60,
            LAG(best_ask, 60) OVER w AS ask_m60
        FROM sec_grid
        WINDOW w AS (PARTITION BY slug ORDER BY sec)
    """)

    # Real-book replay: for every 60-second pop >= 3c in mid, compute
    # TAKER execution cost: sell-at-bid(t0), buy-at-ask(t+60)
    # vs naive midpoint mean-reversion
    print("\n=== 1. real-book SELL-POP replay (60-sec horizon) ===")
    print(con.execute("""
        WITH pops AS (
            SELECT * FROM moves
            WHERE mid_m60 IS NOT NULL AND mid_p60 IS NOT NULL
              AND (mid - mid_m60) >= 0.03
              AND mid BETWEEN 0.10 AND 0.90
              AND mid_m60 BETWEEN 0.05 AND 0.95
        )
        SELECT
            COUNT(*) AS n_pops,
            ROUND(AVG(mid - mid_m60), 4) AS avg_move_size,
            ROUND(AVG(mid_p60 - mid), 4) AS avg_mid_reversion,
            -- Real taker PnL: sell at current_bid, buy back at future_ask
            -- Profit = bid_now - ask_future (if we can sell high and buy low)
            ROUND(AVG(best_bid - ask_p60), 4) AS avg_taker_pnl,
            -- % of pops where the taker trade is profitable
            ROUND(AVG(CASE WHEN best_bid > ask_p60 THEN 1.0 ELSE 0.0 END), 3) AS taker_win_rate,
            -- Average spread at moment-of-pop
            ROUND(AVG(spread), 4) AS avg_spread_at_pop
        FROM pops
    """).df())

    print("\n=== 2. real-book LADDER-ASK sum (full ladder, per second, per day) ===")
    print(con.execute("""
        WITH snap AS (
            SELECT md, sec, COUNT(DISTINCT slug) AS n_buckets,
                   SUM(best_ask) AS sum_ask,
                   SUM(best_bid) AS sum_bid,
                   SUM(mid) AS sum_mid
            FROM sec_grid
            GROUP BY 1, 2
        )
        SELECT md,
               COUNT(*) AS n_snapshots,
               ROUND(AVG(n_buckets), 1) AS avg_n_bkts,
               ROUND(AVG(sum_ask), 4) AS avg_ask_sum,
               ROUND(MIN(sum_ask), 4) AS min_ask_sum,
               ROUND(AVG(sum_bid), 4) AS avg_bid_sum,
               ROUND(MAX(sum_bid), 4) AS max_bid_sum,
               ROUND(AVG(sum_mid), 4) AS avg_mid_sum
        FROM snap
        WHERE n_buckets >= 10
        GROUP BY 1 ORDER BY 1
    """).df())

    print("\n=== 3. ladder-ASK minimum — is there ever an arbitrage? ===")
    print(con.execute("""
        WITH snap AS (
            SELECT md, sec, COUNT(DISTINCT slug) AS n_buckets,
                   SUM(best_ask) AS sum_ask,
                   SUM(best_bid) AS sum_bid
            FROM sec_grid
            GROUP BY 1, 2
        )
        SELECT md, sec,
               ROUND(sum_ask, 4) AS sum_ask,
               ROUND(sum_bid, 4) AS sum_bid,
               n_buckets
        FROM snap
        WHERE n_buckets >= 10
        ORDER BY sum_ask ASC
        LIMIT 15
    """).df())

    # Also: max sum_bid — are there moments the bid-side ladder is > 1?
    # That would be an immediate "sell all bids for > $1" trade.
    print("\n=== 4. ladder-BID maximum — short-the-ladder opportunities? ===")
    print(con.execute("""
        WITH snap AS (
            SELECT md, sec, COUNT(DISTINCT slug) AS n_buckets,
                   SUM(best_bid) AS sum_bid
            FROM sec_grid
            GROUP BY 1, 2
        )
        SELECT md, sec, ROUND(sum_bid, 4) AS sum_bid, n_buckets
        FROM snap
        WHERE n_buckets >= 10
        ORDER BY sum_bid DESC
        LIMIT 15
    """).df())


if __name__ == "__main__":
    main()

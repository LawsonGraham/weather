"""Exploratory I — freshness-filtered ladder-BID-sum arbitrage verification.

Exp H flagged 8 seconds during 65 min of live WS capture with sum(best_bid)
> 1.005 across a full 11-bucket ladder on april-11 (peak 1.042 at 19:54:16
UTC). Cross-sectional sum could be a stale-quote mirage: one bucket's
"last-known bid" might be from 30 seconds ago while others are fresh.

This script re-runs the ladder-bid-sum check with a strict freshness
filter: only count a snapshot as "real" if EVERY bucket has a quote
update within the past N seconds at the snapshot time. Walk through
N = 1, 2, 5, 10, 30 seconds.

If the > 1.0 states persist under even a 2-second freshness filter,
the arbitrage is real and the rate of occurrence should be estimated
(how often per minute does the ladder bid go > 1.0?).
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 300)
pd.set_option("display.max_rows", 60)

TOB = "data/processed/polymarket_book/tob/**/*.parquet"
MARKETS = "data/processed/polymarket_weather/markets.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # YES-token-only quotes
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW yes_tob AS
        WITH ym AS (
            SELECT slug, yes_token_id
            FROM '{MARKETS}'
            WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%'
        )
        SELECT t.received_at, t.slug, t.best_bid, t.best_ask,
               regexp_extract(t.slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md
        FROM '{TOB}' t
        INNER JOIN ym ON ym.slug = t.slug AND ym.yes_token_id = t.asset_id
        WHERE t.best_bid IS NOT NULL AND t.best_ask IS NOT NULL
    """)

    # For every second of every slug, get:
    #   - the most recent quote within that second
    #   - the time since the PREVIOUS quote (staleness indicator)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW slug_sec AS
        SELECT DISTINCT ON (slug, date_trunc('second', received_at))
               slug, md,
               date_trunc('second', received_at) AS sec,
               received_at AS last_quote_at,
               best_bid, best_ask
        FROM yes_tob
        ORDER BY slug, date_trunc('second', received_at), received_at DESC
    """)

    # Now for every second that has SOME slug activity, compute the
    # forward-filled state of all slugs. Cartesian-join every unique
    # second × every slug, then as-of join back to the most recent
    # quote for that slug.
    # DuckDB's ASOF JOIN handles this cleanly.
    # Build the (md, sec, slug) evaluation grid: for every second that has
    # any activity, cross-join with every slug that has ever been observed
    # for that md, then ASOF-join to get the last-known quote for each.
    con.execute("""
        CREATE OR REPLACE TEMP VIEW unique_secs AS
        SELECT DISTINCT md, sec FROM slug_sec
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW md_slugs AS
        SELECT DISTINCT md, slug FROM slug_sec
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW grid AS
        SELECT u.md, u.sec, ms.slug
        FROM unique_secs u
        JOIN md_slugs ms ON ms.md = u.md
    """)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW ladder_state AS
        SELECT g.md, g.sec, g.slug,
               s.best_bid, s.best_ask,
               s.last_quote_at,
               DATE_DIFF('second', s.last_quote_at, g.sec) AS age_sec
        FROM grid g
        ASOF LEFT JOIN slug_sec s
          ON s.slug = g.slug
         AND s.sec <= g.sec
    """)

    # How many slugs per second have fresh quotes within N seconds?
    for max_age in [1, 2, 5, 10, 30]:
        print(f"\n=== ladder-BID-sum with max quote age {max_age}s ===")
        print(con.execute(f"""
            WITH agg AS (
                SELECT md, sec,
                       COUNT(*) FILTER (WHERE age_sec <= {max_age}) AS n_fresh,
                       COUNT(DISTINCT slug) AS n_buckets_total,
                       SUM(best_bid) FILTER (WHERE age_sec <= {max_age}) AS fresh_bid_sum,
                       SUM(best_bid) AS total_bid_sum
                FROM ladder_state
                GROUP BY 1, 2
            )
            SELECT md,
                   COUNT(*) AS n_snapshots,
                   COUNT(*) FILTER (WHERE n_fresh >= 10) AS n_fresh_full_ladder,
                   COUNT(*) FILTER (WHERE n_fresh >= 10 AND fresh_bid_sum > 1.0) AS n_arb,
                   ROUND(MAX(CASE WHEN n_fresh >= 10 THEN fresh_bid_sum END), 4) AS max_fresh_sum
            FROM agg
            GROUP BY 1 ORDER BY 1
        """).df())

    # Drill down: for max_age=2s, show the top-10 arb seconds with full detail
    print("\n=== STRICT (max_age=2s) top-10 ladder-BID arb candidates ===")
    print(con.execute("""
        WITH agg AS (
            SELECT md, sec,
                   COUNT(*) FILTER (WHERE age_sec <= 2) AS n_fresh,
                   SUM(best_bid) FILTER (WHERE age_sec <= 2) AS fresh_bid_sum,
                   MAX(age_sec) FILTER (WHERE age_sec <= 2) AS max_age
            FROM ladder_state
            GROUP BY 1, 2
        )
        SELECT md, sec, n_fresh, ROUND(fresh_bid_sum, 4) AS bid_sum, max_age
        FROM agg
        WHERE n_fresh >= 10 AND fresh_bid_sum > 1.0
        ORDER BY fresh_bid_sum DESC
        LIMIT 10
    """).df())

    # For the top arb second, show all 11 bucket bids + their ages
    print("\n=== per-bucket breakdown of the top arb second (max_age=2s) ===")
    top_sec = con.execute("""
        WITH agg AS (
            SELECT md, sec,
                   COUNT(*) FILTER (WHERE age_sec <= 2) AS n_fresh,
                   SUM(best_bid) FILTER (WHERE age_sec <= 2) AS fresh_bid_sum
            FROM ladder_state
            GROUP BY 1, 2
        )
        SELECT md, sec FROM agg
        WHERE n_fresh >= 10 AND fresh_bid_sum > 1.0
        ORDER BY fresh_bid_sum DESC
        LIMIT 1
    """).fetchone()
    if top_sec:
        print(con.execute(f"""
            SELECT slug, ROUND(best_bid, 4) AS bid, ROUND(best_ask, 4) AS ask, age_sec
            FROM ladder_state
            WHERE md = '{top_sec[0]}' AND sec = '{top_sec[1]}'
            ORDER BY best_bid DESC
        """).df())


if __name__ == "__main__":
    main()

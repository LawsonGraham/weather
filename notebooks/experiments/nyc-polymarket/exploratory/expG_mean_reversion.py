"""Exploratory G — do big 1-min moves mean-revert?

From exp C we saw 17-cent single-minute moves in the active buckets.
If these moves are driven by thin-book actors (one trader walking the
ladder), they should mean-revert within 5-15 minutes when the book
resets.

Test: for every 1-min move of |Δp| >= X cents, measure the average
price relative to the post-move level at t+1, +5, +10, +20 min.
If positive Δp moves revert (price at t+5 < price at t), it's a sell
signal. If negative Δp moves revert (price at t+5 > price at t), it's
a buy signal.

This is a pure momentum/reversal statistic — NOT a backtest, because
we don't have book bid/ask. But if the statistic is strong, it
justifies building a proper backtest with the WS book data.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

MIN1 = "data/processed/polymarket_prices_history/min1/**/*.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW prices AS
        SELECT DISTINCT ON (slug, date_trunc('minute', timestamp))
               slug, p_yes,
               date_trunc('minute', timestamp) AS mt,
               regexp_extract(slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md,
               regexp_extract(slug, 'nyc-on-[a-z]+-[0-9]+-[0-9]+-(.+)', 1) AS strike
        FROM '{MIN1}'
        ORDER BY slug, date_trunc('minute', timestamp), timestamp
    """)

    # Build a per-slug time series with lagged + forward-looking prices
    con.execute("""
        CREATE OR REPLACE TEMP VIEW moves AS
        SELECT
            slug, md, strike, mt, p_yes,
            LAG(p_yes, 1) OVER w AS p_m1,
            LEAD(p_yes, 1) OVER w AS p_p1,
            LEAD(p_yes, 5) OVER w AS p_p5,
            LEAD(p_yes, 10) OVER w AS p_p10,
            LEAD(p_yes, 20) OVER w AS p_p20
        FROM prices
        WINDOW w AS (PARTITION BY slug ORDER BY mt)
    """)

    # Exclude tail buckets that sit at the floor forever — focus on the
    # active region (avg_p between 0.02 and 0.90). Also filter to only
    # look at moves that happen when price was in a meaningful range to
    # begin with.
    for threshold_c in [2, 3, 5, 10]:
        print(f"\n=== moves of |Δp| >= {threshold_c}c (active buckets only) ===")
        thr = threshold_c / 100.0
        df = con.execute(f"""
            WITH filtered AS (
                SELECT * FROM moves
                WHERE p_m1 IS NOT NULL
                  AND ABS(p_yes - p_m1) >= {thr}
                  AND p_m1 BETWEEN 0.05 AND 0.95
                  AND p_yes BETWEEN 0.02 AND 0.98
                  AND strike NOT LIKE '%forbelow'
                  AND strike NOT LIKE '%forhigher'
            )
            SELECT
                'UP' AS direction,
                COUNT(*) AS n,
                ROUND(AVG(p_yes - p_m1), 4) AS avg_move,
                ROUND(AVG(p_p1 - p_yes), 4) AS avg_d_p1,
                ROUND(AVG(p_p5 - p_yes), 4) AS avg_d_p5,
                ROUND(AVG(p_p10 - p_yes), 4) AS avg_d_p10,
                ROUND(AVG(p_p20 - p_yes), 4) AS avg_d_p20
            FROM filtered WHERE p_yes > p_m1
            UNION ALL
            SELECT
                'DOWN' AS direction,
                COUNT(*) AS n,
                ROUND(AVG(p_yes - p_m1), 4) AS avg_move,
                ROUND(AVG(p_p1 - p_yes), 4) AS avg_d_p1,
                ROUND(AVG(p_p5 - p_yes), 4) AS avg_d_p5,
                ROUND(AVG(p_p10 - p_yes), 4) AS avg_d_p10,
                ROUND(AVG(p_p20 - p_yes), 4) AS avg_d_p20
            FROM filtered WHERE p_yes < p_m1
        """).df()
        print(df.to_string(index=False))
        # Interpretation:
        #   avg_move is the 1-min trigger size
        #   avg_d_pX is the subsequent change from the post-move price
        #   If UP moves show negative avg_d_pX, prices revert (sell the top)
        #   If DOWN moves show positive avg_d_pX, prices revert (buy the bottom)

    # Ignoring bucket neutrality — also look at edge buckets (forbelow/forhigher)
    print("\n=== edge buckets ('forbelow'/'forhigher') same analysis ===")
    df2 = con.execute("""
        WITH filtered AS (
            SELECT * FROM moves
            WHERE p_m1 IS NOT NULL
              AND ABS(p_yes - p_m1) >= 0.03
              AND p_m1 BETWEEN 0.05 AND 0.95
              AND (strike LIKE '%forbelow' OR strike LIKE '%forhigher')
        )
        SELECT
            strike,
            COUNT(*) AS n,
            ROUND(AVG(p_yes - p_m1), 4) AS avg_move,
            ROUND(AVG(p_p5 - p_yes), 4) AS avg_d_p5,
            ROUND(AVG(p_p10 - p_yes), 4) AS avg_d_p10
        FROM filtered
        GROUP BY 1 ORDER BY n DESC
    """).df()
    print(df2.to_string(index=False))

    # Proof-of-concept P&L of the mean-reversion strategy
    print("\n=== naive P&L: 'buy on 3c dip, sell at t+10' & 'sell on 3c pop, buy at t+10' ===")
    df3 = con.execute("""
        WITH filtered AS (
            SELECT * FROM moves
            WHERE p_m1 IS NOT NULL
              AND p_p10 IS NOT NULL
              AND ABS(p_yes - p_m1) >= 0.03
              AND p_m1 BETWEEN 0.10 AND 0.90
              AND p_yes BETWEEN 0.05 AND 0.95
        )
        SELECT
            'buy dip, sell @ t+10' AS strategy,
            COUNT(*) AS n,
            ROUND(AVG(CASE WHEN p_yes < p_m1 THEN (p_p10 - p_yes) END), 4) AS avg_pnl,
            ROUND(COUNT(CASE WHEN p_yes < p_m1 AND p_p10 > p_yes THEN 1 END)::DOUBLE /
                  NULLIF(COUNT(CASE WHEN p_yes < p_m1 THEN 1 END), 0), 3) AS hit_rate
        FROM filtered
        UNION ALL
        SELECT
            'sell pop, cover @ t+10' AS strategy,
            COUNT(*) AS n,
            ROUND(AVG(CASE WHEN p_yes > p_m1 THEN -(p_p10 - p_yes) END), 4) AS avg_pnl,
            ROUND(COUNT(CASE WHEN p_yes > p_m1 AND p_p10 < p_yes THEN 1 END)::DOUBLE /
                  NULLIF(COUNT(CASE WHEN p_yes > p_m1 THEN 1 END), 0), 3) AS hit_rate
        FROM filtered
    """).df()
    print(df3.to_string(index=False))


if __name__ == "__main__":
    main()

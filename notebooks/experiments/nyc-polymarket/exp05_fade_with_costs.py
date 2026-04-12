"""Experiment 05 — Fade-morning-favorite with realistic execution costs.

Exp04 finding: fading the 12 EDT argmax range strike on NYC Polymarket
daily-temperature markets earns median +0.47 per $1 invested. But that used
mid-price and ignored overround, spread, and fees. Any of those could eat
the edge.

This experiment validates:

1. **Ladder overround**: sum(yes_price) across all strikes at 12 EDT.
   A well-priced book would sum to 1.00. If it sums to 1.10, we're paying
   10¢ of maker spread on any round trip. Compute per-day and aggregate.

2. **Implied NO spread**: Polymarket range strikes typically trade with a
   2-5¢ spread. Approximate by subtracting a flat 3¢ haircut from entry
   (enter NO at 1 - p - 0.03 instead of 1 - p). Conservative but realistic
   for small size.

3. **Fee model**: Polymarket NegRisk charges a per-trade fee. As a
   conservative floor, subtract 2% of position size per entry. Applied
   to the NO cost so effective entry = (1 - p - 0.03) * 1.02.

4. **Net fade PnL** under these costs. If median drops from +0.47 to +0.10,
   still good. If it drops below 0, the exp04 "finding" was an
   execution-cost illusion.

Also adds:

5. **Per-day ladder shape**: sum of range-strike prices, count of range
   strikes per day, variance across rungs. Tells us if different days have
   different overrounds.

6. **Top-3 fade**: fade the top-3 highest-priced range strikes per day
   instead of just argmax. Catches the "over-committed cluster" case and
   smooths argmax-tiebreak noise.
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

SPREAD_HAIRCUT_C = 0.03  # half-spread paid on NO side (added to mid entry cost)
FEE_BPS = 0.02  # 2% of entry cost as a conservative NegRisk fee proxy


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone = 'UTC'")

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

    # ALL strikes (not just range) so we can compute total-ladder overround.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW nyc_all AS
        SELECT slug, group_item_title AS strike,
               CASE
                   WHEN group_item_title ILIKE '%or higher%' THEN 'or_higher'
                   WHEN group_item_title ILIKE '%or below%'  THEN 'or_below'
                   ELSE 'range'
               END AS kind,
               CASE
                   WHEN group_item_title ILIKE '%or%' THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                   ELSE CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT)
               END AS lo_f,
               CASE
                   WHEN group_item_title ILIKE '%or%' THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                   ELSE CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT)
               END AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
    """)

    # Price at 12 EDT for every strike.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE ladder_12 AS
        SELECT na.*,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = na.slug
               AND p.timestamp <= (CAST(na.local_day AS TIMESTAMPTZ) + INTERVAL '16 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p12
        FROM nyc_all na
    """)


def overround_check(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== LADDER OVERROUND AT 12 EDT ===")
    print("    Sum of yes_price across all strikes per day. Efficient market = 1.00.")
    print(con.execute("""
        WITH ladder_sum AS (
            SELECT local_day,
                   COUNT(*) FILTER (WHERE p12 IS NOT NULL) AS n_strikes,
                   ROUND(SUM(p12) FILTER (WHERE kind='range'), 3) AS range_sum,
                   ROUND(SUM(p12) FILTER (WHERE kind IN ('or_higher','or_below')), 3) AS tail_sum,
                   ROUND(SUM(p12), 3) AS total_sum
            FROM ladder_12
            GROUP BY local_day
            HAVING COUNT(*) FILTER (WHERE p12 IS NOT NULL) >= 8
        )
        SELECT
            COUNT(*) AS n_days,
            ROUND(AVG(range_sum), 3) AS avg_range_sum,
            ROUND(AVG(tail_sum), 3)  AS avg_tail_sum,
            ROUND(AVG(total_sum), 3) AS avg_total_sum,
            ROUND(STDDEV(total_sum), 3) AS std_total_sum,
            ROUND(AVG(total_sum - 1.0), 3) AS mean_overround
        FROM ladder_sum
    """).df())

    print("\n=== OVERROUND DISTRIBUTION ===")
    print(con.execute("""
        WITH ladder_sum AS (
            SELECT local_day, SUM(p12) AS s
            FROM ladder_12 WHERE p12 IS NOT NULL
            GROUP BY 1
            HAVING COUNT(*) >= 8
        )
        SELECT
            COUNT(*) FILTER (WHERE s < 0.95) AS n_under,
            COUNT(*) FILTER (WHERE s BETWEEN 0.95 AND 1.05) AS n_near_1,
            COUNT(*) FILTER (WHERE s BETWEEN 1.05 AND 1.15) AS n_1_05_to_1_15,
            COUNT(*) FILTER (WHERE s > 1.15) AS n_over_1_15,
            ROUND(QUANTILE_CONT(s, 0.25), 3) AS q25,
            ROUND(QUANTILE_CONT(s, 0.5), 3) AS median,
            ROUND(QUANTILE_CONT(s, 0.75), 3) AS q75
        FROM ladder_sum
    """).df())


def fade_pnl_with_costs(con: duckdb.DuckDBPyConnection) -> None:
    print(f"\n=== FADE ARGMAX 12 EDT — cost-haircut ({SPREAD_HAIRCUT_C*100:.0f}¢ spread + {FEE_BPS*100:.0f}% fee) ===")
    print("""    Net per-$1-staked return:
        gross  = (1 - y) / (1 - p) - 1       (fill at mid)
        entry_cost  = (1 - p + spread) * (1 + fee)   (pay above mid by spread, then fee)
        payoff      = 1 - y
        net_ret     = payoff / entry_cost - 1
    """)

    # Favorites per day at 12 EDT with cost-adjusted returns
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE fav_trades AS
        WITH favs AS (
            SELECT l.local_day,
                   arg_max(l.strike, l.p12) AS fav,
                   max(l.p12) AS p_fav,
                   arg_max(l.lo_f, l.p12) AS lo_f,
                   arg_max(l.hi_f, l.p12) AS hi_f
            FROM ladder_12 l
            WHERE l.kind='range' AND l.p12 IS NOT NULL
            GROUP BY 1
        )
        SELECT f.*, md.day_max_whole,
               CASE WHEN md.day_max_whole BETWEEN f.lo_f AND f.hi_f THEN 1 ELSE 0 END AS y,
               -- Cost-adjusted entry on NO side
               (1.0 - f.p_fav + {SPREAD_HAIRCUT_C}) * (1.0 + {FEE_BPS}) AS entry_cost,
               (1.0 - f.p_fav) AS mid_entry
        FROM favs f
        JOIN metar_daily md ON md.local_date = f.local_day
        WHERE md.day_max_whole IS NOT NULL
          -- Require effective entry < 0.99 so p_fav > ~0 (skip already-locked YES)
          AND (1.0 - f.p_fav + {SPREAD_HAIRCUT_C}) < 0.99
    """)

    print(con.execute(f"""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(p_fav), 3) AS avg_p_fav,
            ROUND(AVG(1 - y), 3) AS miss_rate,

            -- Gross (exp04 numbers)
            ROUND(AVG((1 - y) / (1 - p_fav) - 1), 3)       AS gross_avg_ret,
            ROUND(QUANTILE_CONT((1 - y) / (1 - p_fav) - 1, 0.5), 3) AS gross_med_ret,

            -- Net with cost haircut
            ROUND(AVG((1 - y) / entry_cost - 1), 3)        AS net_avg_ret,
            ROUND(QUANTILE_CONT((1 - y) / entry_cost - 1, 0.5), 3) AS net_med_ret,
            ROUND(SUM((1 - y) / entry_cost - 1), 2)        AS net_cum_pnl
        FROM fav_trades
    """).df())


def fade_top3(con: duckdb.DuckDBPyConnection) -> None:
    print(f"\n=== FADE TOP-3 HIGHEST-PRICED RANGE STRIKES AT 12 EDT (with costs) ===")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE top3 AS
        WITH ranked AS (
            SELECT local_day, strike, lo_f, hi_f, p12,
                   ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p12 DESC NULLS LAST) AS rk
            FROM ladder_12
            WHERE kind='range' AND p12 IS NOT NULL
        )
        SELECT r.local_day, r.strike, r.lo_f, r.hi_f, r.p12, md.day_max_whole,
               CASE WHEN md.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
               (1.0 - r.p12 + {SPREAD_HAIRCUT_C}) * (1.0 + {FEE_BPS}) AS entry_cost
        FROM ranked r
        JOIN metar_daily md ON md.local_date = r.local_day
        WHERE r.rk <= 3 AND md.day_max_whole IS NOT NULL
          AND (1.0 - r.p12 + {SPREAD_HAIRCUT_C}) < 0.99
          AND r.p12 >= 0.15   -- skip tails, too low to fade meaningfully
    """)
    print(con.execute("""
        SELECT
            COUNT(*) AS n_bets,
            ROUND(AVG(p12), 3) AS avg_p,
            ROUND(AVG(1 - y), 3) AS miss_rate,
            ROUND(AVG((1 - y) / entry_cost - 1), 3)                    AS net_avg_ret,
            ROUND(QUANTILE_CONT((1 - y) / entry_cost - 1, 0.5), 3)     AS net_med_ret,
            ROUND(SUM((1 - y) / entry_cost - 1), 2)                    AS net_cum_pnl
        FROM top3
    """).df())


def sensitivity(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== COST SENSITIVITY: net median return at various haircut/fee levels ===")
    for spread, fee in [(0.00, 0.00), (0.01, 0.01), (0.02, 0.01), (0.03, 0.02), (0.05, 0.02), (0.07, 0.03)]:
        q = f"""
            WITH favs AS (
                SELECT l.local_day,
                       max(l.p12) AS p_fav,
                       arg_max(l.lo_f, l.p12) AS lo_f,
                       arg_max(l.hi_f, l.p12) AS hi_f
                FROM ladder_12 l WHERE l.kind='range' AND l.p12 IS NOT NULL
                GROUP BY 1
            ),
            scored AS (
                SELECT f.p_fav, md.day_max_whole,
                       CASE WHEN md.day_max_whole BETWEEN f.lo_f AND f.hi_f THEN 1 ELSE 0 END AS y,
                       (1.0 - f.p_fav + {spread}) * (1.0 + {fee}) AS entry_cost
                FROM favs f JOIN metar_daily md ON md.local_date = f.local_day
                WHERE md.day_max_whole IS NOT NULL AND (1.0 - f.p_fav + {spread}) < 0.99
            )
            SELECT
                {spread} AS spread, {fee} AS fee, COUNT(*) AS n,
                ROUND(AVG((1-y)/entry_cost - 1), 3) AS net_avg,
                ROUND(QUANTILE_CONT((1-y)/entry_cost - 1, 0.5), 3) AS net_med,
                ROUND(SUM((1-y)/entry_cost - 1), 2) AS cum
            FROM scored
        """
        print(con.execute(q).df())


def oos_split(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== OUT-OF-SAMPLE SPLIT (chronological, ~60/40 split by date) ===")
    # Total 55 scorable days. Train = earliest 35 days, test = next 20.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE date_rank AS
        SELECT local_day, ROW_NUMBER() OVER (ORDER BY local_day) AS rk
        FROM (SELECT DISTINCT local_day FROM fav_trades)
    """)
    n_total = con.execute("SELECT COUNT(*) FROM date_rank").fetchone()[0]
    print(f"    total scorable days: {n_total}")
    train_n = int(n_total * 0.60) or 1
    print(f"    train: first {train_n} days   |   test: next {n_total - train_n} days")

    q = f"""
        SELECT
            CASE WHEN dr.rk <= {train_n} THEN 'train' ELSE 'test' END AS split,
            COUNT(*) AS n,
            ROUND(AVG(f.p_fav), 3) AS avg_p_fav,
            ROUND(AVG(1 - f.y), 3) AS miss_rate,
            ROUND(AVG((1 - f.y) / f.entry_cost - 1), 3) AS net_avg_ret,
            ROUND(QUANTILE_CONT((1 - f.y) / f.entry_cost - 1, 0.5), 3) AS net_med_ret,
            ROUND(SUM((1 - f.y) / f.entry_cost - 1), 2) AS net_cum_pnl
        FROM fav_trades f JOIN date_rank dr ON dr.local_day = f.local_day
        GROUP BY 1 ORDER BY 1
    """
    print(con.execute(q).df())


def concentration_check(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== CONCENTRATION RISK: per-trade net PnL distribution ===")
    print(con.execute("""
        WITH pnl AS (
            SELECT (1 - y) / entry_cost - 1 AS r
            FROM fav_trades
        )
        SELECT
            COUNT(*) AS n,
            ROUND(MIN(r), 2)                       AS min,
            ROUND(QUANTILE_CONT(r, 0.10), 2)       AS p10,
            ROUND(QUANTILE_CONT(r, 0.25), 2)       AS p25,
            ROUND(QUANTILE_CONT(r, 0.5),  2)       AS p50,
            ROUND(QUANTILE_CONT(r, 0.75), 2)       AS p75,
            ROUND(QUANTILE_CONT(r, 0.90), 2)       AS p90,
            ROUND(MAX(r), 2)                       AS max,
            -- How many bets account for half of cum PnL?
            ROUND(AVG(r), 3)                       AS mean
        FROM pnl
    """).df())

    print("\n=== RANKED TRADE CONTRIBUTIONS (top 5) ===")
    print(con.execute("""
        SELECT local_day, fav, day_max_whole, ROUND(p_fav,3) AS p_fav, y,
               ROUND((1-y)/entry_cost - 1, 3) AS net_ret
        FROM fav_trades
        ORDER BY (1-y)/entry_cost - 1 DESC
        LIMIT 5
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    overround_check(con)
    fade_pnl_with_costs(con)
    fade_top3(con)
    sensitivity(con)
    oos_split(con)
    concentration_check(con)


if __name__ == "__main__":
    main()

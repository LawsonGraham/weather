"""Experiment 14 — Out-of-sample + monthly split for Strategy D.

Strategy D (exp13 winner): every day at 12 EDT, buy the range strike whose
lo_f = favorite's lo_f + 2. 55-day in-sample cum_pnl was +81.6 on 44 bets
(30% hit, -$1 median).

This exp gates the strategy for deployment:

1. Chronological 60/40 OOS split. If test PnL is positive, strategy survives.
2. Monthly split — is the edge seasonal or universal?
3. Conservative-entry filter — skip bets where p_entry < 2¢ (unrealistic
   fill). Tests whether the headline cum is outlier-dominated.
4. Cap-losses variant — max loss -$1 per bet (just confirming arithmetic).
5. Stop-loss drawdown simulation — if we hit N consecutive losses, skip
   the next K bets. Conservative deployment check.
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

SPREAD = 0.03
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
             ORDER BY p.timestamp DESC LIMIT 1) AS p12
        FROM r
    """)

    con.execute("""
        CREATE OR REPLACE TEMP TABLE fav AS
        WITH ranked AS (
            SELECT r.*, md.day_max_whole,
                   ROW_NUMBER() OVER (PARTITION BY r.local_day ORDER BY r.p12 DESC NULLS LAST) AS rk
            FROM range_12 r JOIN metar_daily md ON md.local_date = r.local_day
            WHERE r.p12 IS NOT NULL AND md.day_max_whole IS NOT NULL
        )
        SELECT local_day, strike AS fav_strike, lo_f AS fav_lo, hi_f AS fav_hi,
               p12 AS fav_p, day_max_whole
        FROM ranked WHERE rk = 1
    """)

    # Strategy D trades: one row per day we can enter the trade
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE strat_d AS
        SELECT
            f.local_day,
            EXTRACT(MONTH FROM f.local_day) AS month,
            f.fav_strike, f.fav_lo, f.day_max_whole,
            r.strike AS bought, r.lo_f AS lo_bought, r.hi_f AS hi_bought, r.p12 AS p_entry,
            CASE WHEN f.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
            (r.p12 + {SPREAD}) * (1 + {FEE}) AS entry_cost,
            ROW_NUMBER() OVER (ORDER BY f.local_day) AS date_rank
        FROM fav f
        JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
        WHERE r.p12 IS NOT NULL AND (r.p12 + {SPREAD}) < 0.97
    """)


def oos_split(con: duckdb.DuckDBPyConnection) -> None:
    n_total = con.execute("SELECT COUNT(*) FROM strat_d").fetchone()[0]
    train_n = int(n_total * 0.60)
    print(f"\n=== STRATEGY D — CHRONOLOGICAL 60/40 OOS SPLIT (n_total={n_total}) ===")
    print(f"    train: first {train_n} bets   |   test: next {n_total - train_n} bets")
    print(con.execute(f"""
        SELECT
            CASE WHEN date_rank <= {train_n} THEN 'train' ELSE 'test' END AS split,
            COUNT(*) AS n,
            ROUND(AVG(p_entry), 3) AS avg_p_entry,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(y / entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(y / entry_cost - 1), 2) AS cum
        FROM strat_d
        GROUP BY 1 ORDER BY 1
    """).df())


def monthly_split(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY D — MONTHLY SEGMENTATION ===")
    print(con.execute("""
        SELECT
            month::INT AS month,
            COUNT(*) AS n,
            ROUND(AVG(p_entry), 3) AS avg_p,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(y / entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(y / entry_cost - 1), 2) AS cum_pnl
        FROM strat_d
        GROUP BY month ORDER BY month
    """).df())


def conservative_entry(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== STRATEGY D — SKIP IF p_entry < 2¢ (realistic fill floor) ===")
    print("    The two 30x outlier days had p_entry ~0.001. If real fills are")
    print("    impossible below 2¢, those wins evaporate.")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(p_entry), 3) AS avg_p_entry,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(y / entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(y / entry_cost - 1), 2) AS cum_pnl
        FROM strat_d
        WHERE p_entry >= 0.02
    """).df())


def conservative_p_oos(con: duckdb.DuckDBPyConnection) -> None:
    n_total = con.execute("SELECT COUNT(*) FROM strat_d WHERE p_entry >= 0.02").fetchone()[0]
    train_n = int(n_total * 0.60)
    print(f"\n=== STRATEGY D (p_entry≥2¢) — CHRONOLOGICAL 60/40 OOS SPLIT ===")
    print(f"    train: first {train_n} bets   |   test: next {n_total - train_n} bets")
    print(con.execute(f"""
        WITH filtered AS (
            SELECT *,
                   ROW_NUMBER() OVER (ORDER BY local_day) AS filtered_rank
            FROM strat_d WHERE p_entry >= 0.02
        )
        SELECT
            CASE WHEN filtered_rank <= {train_n} THEN 'train' ELSE 'test' END AS split,
            COUNT(*) AS n,
            ROUND(AVG(p_entry), 3) AS avg_p_entry,
            ROUND(AVG(y), 3) AS hit_rate,
            ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
            ROUND(QUANTILE_CONT(y / entry_cost - 1, 0.5), 3) AS net_med,
            ROUND(SUM(y / entry_cost - 1), 2) AS cum
        FROM filtered GROUP BY 1 ORDER BY 1
    """).df())


def drawdown_sim(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== RUNNING EQUITY CURVE (Kelly 4% per bet, starting at $10,000) ===")
    print("    Shows max drawdown and worst consecutive-loss streak.")
    print(con.execute("""
        WITH walk AS (
            SELECT local_day, y, p_entry, entry_cost,
                   (y / entry_cost - 1) AS ret
            FROM strat_d ORDER BY local_day
        ),
        cum AS (
            SELECT *, SUM(ret) OVER (ORDER BY local_day) AS running_pnl_unit
            FROM walk
        )
        SELECT
            ROUND(MIN(running_pnl_unit), 2) AS min_drawdown_unit,
            ROUND(MAX(running_pnl_unit), 2) AS peak_unit,
            ROUND(SUM(ret), 2) AS final_unit
        FROM cum
    """).df())

    # Streak analysis — max consecutive loss
    df = con.execute("""
        SELECT local_day, y, (y / entry_cost - 1) AS ret
        FROM strat_d ORDER BY local_day
    """).df()

    max_losing_streak = 0
    current_streak = 0
    max_winning_streak = 0
    current_w = 0
    for _, row in df.iterrows():
        if row["ret"] < 0:
            current_streak += 1
            max_losing_streak = max(max_losing_streak, current_streak)
            current_w = 0
        else:
            current_w += 1
            max_winning_streak = max(max_winning_streak, current_w)
            current_streak = 0
    print(f"\n    max losing streak: {max_losing_streak} consecutive bets (pure -$1 losses)")
    print(f"    max winning streak: {max_winning_streak} consecutive bets")
    # At 4% Kelly, max losing streak of N means drawdown = 1 - (1-0.04)^N
    mls = max_losing_streak
    drawdown_4pct = 1 - (1 - 0.04) ** mls
    print(f"    expected drawdown at 4% Kelly per bet: {drawdown_4pct*100:.1f}% of bankroll")


def per_day_train_vs_test(con: duckdb.DuckDBPyConnection) -> None:
    n_total = con.execute("SELECT COUNT(*) FROM strat_d").fetchone()[0]
    train_n = int(n_total * 0.60)
    print(f"\n=== TRAIN/TEST PER-DAY DETAIL (train ≤ rank {train_n}, test > {train_n}) ===")
    print(con.execute(f"""
        SELECT
            local_day, fav_strike, day_max_whole AS dmax, bought,
            ROUND(p_entry, 3) AS p, y,
            CASE WHEN date_rank <= {train_n} THEN 'train' ELSE 'test' END AS split,
            ROUND(y / entry_cost - 1, 2) AS net_ret
        FROM strat_d ORDER BY local_day
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)
    oos_split(con)
    monthly_split(con)
    conservative_entry(con)
    conservative_p_oos(con)
    drawdown_sim(con)
    per_day_train_vs_test(con)


if __name__ == "__main__":
    main()

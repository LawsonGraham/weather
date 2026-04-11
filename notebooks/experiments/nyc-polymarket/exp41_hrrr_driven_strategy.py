"""Experiment 41 — HRRR-driven strategy backtest.

Exp40 found HRRR has +1.27°F bias vs Polymarket's +4.07°F, uncorrelated
(corr 0.113). HRRR is much more accurate than the market. Strategy D V1
catches half of the market's bias; an HRRR-driven strategy catches the
whole bias on average AND captures outlier days (like March 10/11)
where Polymarket was 20-26°F off and HRRR was within 2°F.

Method: for each Polymarket day, compute HRRR's predicted day max from
the morning HRRR runs (init <= 12 EDT), identify the bucket containing
that prediction, and buy YES on it at the 16 EDT real-ask price.

Variants tested:
    P2a: Buy bucket containing HRRR_predicted_max (rounded to int F)
    P2b: Buy the +1°F-above bucket from HRRR (small upward shift)
    P2c: Buy the -1°F-below bucket from HRRR (HRRR has +1.27°F bias)
    P2d: Buy ONLY when HRRR_pred and Poly_fav disagree by 3+ buckets

Each compared to Strategy D V1 baseline.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 60)

HRRR_HOURLY = "data/raw/hrrr/KLGA/hourly.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"
MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"

FEE = 0.02


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
        SELECT local_date, ROUND(MAX(te))::INT AS metar_day_max
        FROM m WHERE te > -900 GROUP BY 1
    """)

    # HRRR prediction: use ONLY morning init runs (init_time before noon UTC)
    # to simulate "what HRRR knew at trade time". Take MAX(t2m) across those.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW hrrr_morning_pred AS
        SELECT
            CAST((valid_time AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
            ROUND(MAX(t2m_heightAboveGround_2 * 9.0/5.0 - 459.67))::INT AS hrrr_pred_max
        FROM '{HRRR_HOURLY}'
        WHERE EXTRACT(HOUR FROM (init_time AT TIME ZONE 'America/New_York')) < 12
        GROUP BY 1
    """)

    # NYC range strikes with 16 EDT entry price
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE range_16 AS
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
               AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_at_16
        FROM r
    """)

    # Backtest a "buy this bucket" strategy
    def backtest(label: str, target_lo_expr: str, filter_expr: str = "TRUE") -> None:
        q = f"""
            WITH targets AS (
                SELECT
                    h.local_date AS local_day,
                    h.hrrr_pred_max,
                    md.metar_day_max,
                    ({target_lo_expr}) AS target_lo
                FROM hrrr_morning_pred h
                JOIN metar_daily md USING (local_date)
                WHERE {filter_expr}
            ),
            trades AS (
                SELECT
                    t.local_day, t.hrrr_pred_max, t.metar_day_max, t.target_lo,
                    r.strike, r.p_at_16,
                    CASE WHEN t.metar_day_max BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                    r.p_at_16 * (1 + {FEE}) AS entry_cost
                FROM targets t
                JOIN range_16 r ON r.local_day = t.local_day AND r.lo_f = t.target_lo
                WHERE r.p_at_16 IS NOT NULL AND r.p_at_16 >= 0.005 AND r.p_at_16 < 0.97
            )
            SELECT
                COUNT(*) AS n,
                ROUND(AVG(p_at_16), 3) AS avg_p,
                ROUND(AVG(y), 3) AS hit_rate,
                ROUND(AVG(y / entry_cost - 1), 3) AS net_avg,
                ROUND(QUANTILE_CONT(y / entry_cost - 1, 0.5), 3) AS net_med,
                ROUND(SUM(y / entry_cost - 1), 2) AS cum_pnl
            FROM trades
        """
        df = con.execute(q).df()
        df["strategy"] = label
        df = df[["strategy", "n", "avg_p", "hit_rate", "net_avg", "net_med", "cum_pnl"]]
        print(df.to_string(index=False))

    print("\n=== STRATEGY VARIANTS (16 EDT entry, real-ask cost, 2% fee) ===\n")

    # Strategy D baseline at 16 EDT (favorite + 2)
    print("Strategy D V1 (favorite + 2 bucket):")
    print(con.execute(f"""
        WITH fav AS (
            SELECT local_day, arg_max(lo_f, p_at_16) AS fav_lo
            FROM range_16 WHERE p_at_16 IS NOT NULL GROUP BY 1
        ),
        d AS (
            SELECT f.local_day, r.p_at_16, r.lo_f, r.hi_f, md.metar_day_max,
                   CASE WHEN md.metar_day_max BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                   r.p_at_16 * (1 + {FEE}) AS entry_cost
            FROM fav f
            JOIN range_16 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
            JOIN metar_daily md ON md.local_date = f.local_day
            WHERE r.p_at_16 IS NOT NULL AND r.p_at_16 >= 0.02
        )
        SELECT COUNT(*) AS n, ROUND(AVG(p_at_16),3) AS avg_p,
               ROUND(AVG(y),3) AS hit_rate,
               ROUND(AVG(y/entry_cost - 1),3) AS net_avg,
               ROUND(QUANTILE_CONT(y/entry_cost - 1, 0.5), 3) AS net_med,
               ROUND(SUM(y/entry_cost - 1),2) AS cum_pnl
        FROM d
    """).df())
    print()

    # P2a: buy the bucket whose lo equals HRRR pred (rounded down to even)
    print("P2a — buy bucket containing HRRR predicted max (lo_f = even floor of pred):")
    backtest("P2a HRRR exact", "(hrrr_pred_max / 2) * 2")

    # P2b: shift +1 to compensate for HRRR's small under-prediction
    print("\nP2b — buy +1°F above HRRR pred (HRRR has +1.27°F bias):")
    backtest("P2b HRRR +1", "((hrrr_pred_max + 1) / 2) * 2")

    # P2c: shift +2 (full bias correction)
    print("\nP2c — buy +2°F above HRRR pred:")
    backtest("P2c HRRR +2", "((hrrr_pred_max + 2) / 2) * 2")

    # P2d: HRRR-vs-market disagreement filter
    print("\nP2d — HRRR vs market disagreement filter (only fire when |HRRR - poly_fav| >= 4°F):")
    print(con.execute(f"""
        WITH poly_fav AS (
            SELECT local_day, arg_max(lo_f, p_at_16) AS poly_fav_lo
            FROM range_16 WHERE p_at_16 IS NOT NULL GROUP BY 1
        ),
        targets AS (
            SELECT
                h.local_date AS local_day,
                h.hrrr_pred_max,
                pf.poly_fav_lo,
                ((h.hrrr_pred_max / 2) * 2) AS target_lo
            FROM hrrr_morning_pred h
            JOIN poly_fav pf ON pf.local_day = h.local_date
            WHERE ABS(h.hrrr_pred_max - pf.poly_fav_lo) >= 4
        ),
        trades AS (
            SELECT
                t.local_day, t.hrrr_pred_max, t.poly_fav_lo, t.target_lo,
                r.p_at_16, md.metar_day_max,
                CASE WHEN md.metar_day_max BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
                r.p_at_16 * (1 + {FEE}) AS entry_cost
            FROM targets t
            JOIN range_16 r ON r.local_day = t.local_day AND r.lo_f = t.target_lo
            JOIN metar_daily md ON md.local_date = t.local_day
            WHERE r.p_at_16 IS NOT NULL AND r.p_at_16 >= 0.005 AND r.p_at_16 < 0.97
        )
        SELECT COUNT(*) AS n, ROUND(AVG(p_at_16),3) AS avg_p,
               ROUND(AVG(y),3) AS hit_rate,
               ROUND(AVG(y/entry_cost - 1),3) AS net_avg,
               ROUND(QUANTILE_CONT(y/entry_cost - 1, 0.5), 3) AS net_med,
               ROUND(SUM(y/entry_cost - 1),2) AS cum_pnl
        FROM trades
    """).df())

    # Show the per-day picks for the disagreement filter
    print("\n=== P2d disagreement-filter trades (HRRR vs Polymarket fav by 4°F+) ===")
    print(con.execute(f"""
        WITH poly_fav AS (
            SELECT local_day, arg_max(lo_f, p_at_16) AS poly_fav_lo
            FROM range_16 WHERE p_at_16 IS NOT NULL GROUP BY 1
        )
        SELECT
            h.local_date AS local_day,
            h.hrrr_pred_max,
            pf.poly_fav_lo,
            md.metar_day_max,
            (h.hrrr_pred_max - pf.poly_fav_lo) AS hrrr_minus_poly,
            ((h.hrrr_pred_max / 2) * 2) AS d_target_lo,
            (SELECT ROUND(p_at_16, 3) FROM range_16 r WHERE r.local_day = h.local_date AND r.lo_f = ((h.hrrr_pred_max / 2) * 2)) AS p_target,
            CASE WHEN md.metar_day_max BETWEEN ((h.hrrr_pred_max / 2) * 2) AND ((h.hrrr_pred_max / 2) * 2 + 1) THEN 'WIN' ELSE 'loss' END AS result
        FROM hrrr_morning_pred h
        JOIN poly_fav pf ON pf.local_day = h.local_date
        JOIN metar_daily md ON md.local_date = h.local_date
        WHERE ABS(h.hrrr_pred_max - pf.poly_fav_lo) >= 4
        ORDER BY h.local_date
    """).df())


if __name__ == "__main__":
    main()

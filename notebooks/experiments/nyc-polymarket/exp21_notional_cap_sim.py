"""Experiment 21 — Notional-cap sim for Strategy D across entry hours.

Exp20 showed the 16/18 EDT combined portfolio produces implausible
multiples (15x / 120x) because compounding 2% of a growing bankroll
through winners paying 20-50x blows up non-linearly. This exp caps
per-bet notional at a FIXED $100 stake (no compounding) and re-scores.

The honest question: if you bet a fixed $100 per trade regardless of
bankroll, what does the per-trade PnL look like at each entry hour?

This is the "unit-capital" view — it strips out sequence-dependence.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02
FIXED_STAKE = 100.0


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


def trade_sequence_real_ask(con: duckdb.DuckDBPyConnection, hour_utc: int) -> pd.DataFrame:
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE snap_h{hour_utc} AS
        SELECT nr.*,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug=nr.slug
               AND p.timestamp <= (CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_mid,
            CAST(nr.local_day AS TIMESTAMPTZ) + INTERVAL '{hour_utc} hour' AS target_ts
        FROM nyc_range nr
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE fav_h{hour_utc} AS
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p_mid DESC NULLS LAST) AS rk
            FROM snap_h{hour_utc} WHERE p_mid IS NOT NULL
        )
        SELECT local_day, lo_f AS fav_lo, target_ts
        FROM ranked WHERE rk = 1
    """)
    df = con.execute(f"""
        WITH d AS (
            SELECT f.local_day, f.target_ts,
                   s.slug AS d_slug, s.strike, s.lo_f, s.hi_f, s.p_mid
            FROM fav_h{hour_utc} f
            JOIN snap_h{hour_utc} s ON s.local_day = f.local_day AND s.lo_f = f.fav_lo + 2
            WHERE s.p_mid IS NOT NULL AND s.p_mid >= 0.02
        ),
        with_ask AS (
            SELECT d.*,
                (SELECT price FROM '{FILLS}' f2
                 WHERE f2.slug = d.d_slug AND f2.timestamp <= d.target_ts
                   AND UPPER(f2.outcome)='YES' AND UPPER(f2.side)='BUY'
                 ORDER BY f2.timestamp DESC LIMIT 1) AS real_ask
            FROM d
        )
        SELECT
            wa.local_day, wa.strike, wa.p_mid,
            COALESCE(wa.real_ask, wa.p_mid) AS entry_price,
            CASE WHEN md.day_max_whole BETWEEN wa.lo_f AND wa.hi_f THEN 1 ELSE 0 END AS y
        FROM with_ask wa
        JOIN metar_daily md ON md.local_date = wa.local_day
        WHERE md.day_max_whole IS NOT NULL
        ORDER BY wa.local_day
    """).df()
    return df


def fixed_stake_pnl(df: pd.DataFrame, label: str) -> None:
    print(f"\n=== FIXED-STAKE ${FIXED_STAKE} PnL — {label} ===")
    if len(df) == 0:
        print("   (no trades)")
        return
    total_pnl = 0.0
    wins = 0
    rets = []
    for _, r in df.iterrows():
        entry = r["entry_price"]
        if entry is None or entry <= 0 or entry >= 0.97:
            continue
        cost = entry * (1 + FEE)
        shares = FIXED_STAKE / cost
        payoff = shares * (1 if r["y"] == 1 else 0)
        pnl = payoff - FIXED_STAKE
        total_pnl += pnl
        rets.append(pnl)
        if r["y"] == 1:
            wins += 1
    n = len(rets)
    mean_pnl = sum(rets) / n if n else 0.0
    med_pnl = sorted(rets)[n // 2] if n else 0.0
    max_pnl = max(rets) if rets else 0.0
    min_pnl = min(rets) if rets else 0.0
    print(f"   n={n}, wins={wins} ({wins/n*100:.1f}%)")
    print(f"   mean  pnl per bet: ${mean_pnl:+,.0f}")
    print(f"   median pnl per bet: ${med_pnl:+,.0f}")
    print(f"   max single win:  ${max_pnl:+,.0f}")
    print(f"   max single loss: ${min_pnl:+,.0f}")
    print(f"   cum pnl on {n} bets: ${total_pnl:+,.0f}")
    print(f"   return on capital (n × stake): {total_pnl / (n * FIXED_STAKE) * 100:+.1f}%")


def main() -> None:
    con = duckdb.connect()
    build(con)
    for h, label in [(16, "12 EDT"), (20, "16 EDT"), (22, "18 EDT")]:
        df = trade_sequence_real_ask(con, h)
        fixed_stake_pnl(df, label)


if __name__ == "__main__":
    main()

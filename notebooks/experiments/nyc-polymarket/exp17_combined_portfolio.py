"""Experiment 17 — Combined portfolio: Strategy D + Strategy F + peaked-ladder short.

Three strategies from the exploration loop:
    D: all days, buy `fav_lo + 2` bucket (exp13)
    F: clear sky + rise_needed < 3°F, short favorite (exp12 Strategy B / exp13)
    P: peaked ladder (p_fav ≥ 0.60, n_over_10c ≤ 2), short favorite (exp07/08)

Question: how do they interact in a single portfolio? Are D wins on days
when F/P also fire? (Correlation = bad diversification.) Or do they fire
on different days? (Low correlation = stacking works.)

Simulate walking all 55 days, applying each strategy when its filter
fires, with a joint Kelly allocation budget.
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
        CREATE OR REPLACE TEMP VIEW metar_12edt AS
        WITH ranked AS (
            SELECT
                CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                valid, tmpf, skyc1,
                ROW_NUMBER() OVER (
                    PARTITION BY CAST((valid AT TIME ZONE 'America/New_York') AS DATE)
                    ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                ) AS rk
            FROM '{METAR}' WHERE station='LGA'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT local_date, tmpf, skyc1 FROM ranked WHERE rk = 1
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

    # Per-day picture: favorite, ladder shape, METAR context, 3 strategy PnLs
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE day_portfolio AS
        WITH fav AS (
            SELECT local_day, strike AS fav_strike, lo_f AS fav_lo, hi_f AS fav_hi,
                   p12 AS fav_p
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY local_day ORDER BY p12 DESC NULLS LAST) AS rk
                FROM range_12 WHERE p12 IS NOT NULL
            ) WHERE rk=1
        ),
        ladder_stats AS (
            SELECT local_day,
                   COUNT(*) FILTER (WHERE p12 >= 0.10) AS n_over_10c
            FROM range_12 WHERE p12 IS NOT NULL GROUP BY local_day
        ),
        base AS (
            SELECT f.*,
                   md.day_max_whole,
                   CASE WHEN md.day_max_whole BETWEEN f.fav_lo AND f.fav_hi THEN 1 ELSE 0 END AS fav_y,
                   m12.tmpf AS tmpf_12, m12.skyc1,
                   (f.fav_lo - m12.tmpf) AS rise_needed,
                   ls.n_over_10c
            FROM fav f
            JOIN metar_daily md ON md.local_date = f.local_day
            LEFT JOIN metar_12edt m12 ON m12.local_date = f.local_day
            LEFT JOIN ladder_stats ls ON ls.local_day = f.local_day
        )
        SELECT
            b.*,
            -- Strategy D: buy +2 bucket
            d.strike AS d_strike, d.p12 AS d_p,
            CASE WHEN d.strike IS NULL THEN NULL
                 WHEN b.day_max_whole BETWEEN d.lo_f AND d.hi_f THEN 1 ELSE 0 END AS d_y,
            CASE WHEN d.p12 IS NOT NULL AND d.p12 >= 0.02
                 THEN (d.p12 + {SPREAD}) * (1 + {FEE})
                 ELSE NULL END AS d_entry_cost,
            -- Strategy F: clear + rise_needed<3, short fav
            CASE WHEN b.skyc1 IN ('CLR','FEW','SCT') AND (b.fav_lo - b.tmpf_12) < 3 THEN 1 ELSE 0 END AS f_fires,
            -- Strategy P: peaked ladder, short fav
            CASE WHEN b.fav_p >= 0.60 AND b.n_over_10c <= 2 THEN 1 ELSE 0 END AS p_fires
        FROM base b
        LEFT JOIN range_12 d ON d.local_day = b.local_day AND d.lo_f = b.fav_lo + 2
    """)


def joint_coverage(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== PER-STRATEGY DAYS FIRED ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_days,
            COUNT(*) FILTER (WHERE d_entry_cost IS NOT NULL) AS d_days,
            COUNT(*) FILTER (WHERE f_fires=1)    AS f_days,
            COUNT(*) FILTER (WHERE p_fires=1)    AS p_days,
            COUNT(*) FILTER (WHERE d_entry_cost IS NOT NULL AND f_fires=1) AS d_and_f,
            COUNT(*) FILTER (WHERE d_entry_cost IS NOT NULL AND p_fires=1) AS d_and_p,
            COUNT(*) FILTER (WHERE f_fires=1 AND p_fires=1) AS f_and_p,
            COUNT(*) FILTER (WHERE d_entry_cost IS NOT NULL AND f_fires=1 AND p_fires=1) AS all_three
        FROM day_portfolio
    """).df())


def portfolio_sim(con: duckdb.DuckDBPyConnection) -> None:
    # Pull daily data as pandas and walk portfolio
    df = con.execute("""
        SELECT local_day, day_max_whole,
               fav_p, fav_y,
               d_p, d_y, d_entry_cost,
               f_fires, p_fires,
               skyc1, rise_needed, n_over_10c
        FROM day_portfolio
        ORDER BY local_day
    """).df()
    print(f"\n=== PORTFOLIO SIM — 3 strategies, 2% bankroll per LEG, start $10k ===")
    # Walk every day; for each fired strategy, stake 2% of bankroll and realize PnL
    bankroll = 10_000.0
    peak = bankroll
    max_dd = 0.0
    rows = []
    for _, r in df.iterrows():
        daily_pnl = 0.0
        legs = []
        # Strategy D
        if pd.notna(r["d_entry_cost"]):
            stake = bankroll * 0.02
            pnl = stake * (r["d_y"] / r["d_entry_cost"]) - stake
            daily_pnl += pnl
            legs.append(("D", r["d_y"], r["d_entry_cost"], stake, pnl))
        # Strategy F (short favorite)
        if r["f_fires"] == 1 and r["fav_p"] < 0.99:
            stake = bankroll * 0.02
            no_cost = (1 - r["fav_p"] + SPREAD) * (1 + FEE)
            pnl = stake * ((1 - r["fav_y"]) / no_cost) - stake
            daily_pnl += pnl
            legs.append(("F", r["fav_y"], no_cost, stake, pnl))
        # Strategy P (short favorite, peaked)
        if r["p_fires"] == 1 and r["fav_p"] < 0.99 and r["f_fires"] != 1:  # don't double-short
            stake = bankroll * 0.02
            no_cost = (1 - r["fav_p"] + SPREAD) * (1 + FEE)
            pnl = stake * ((1 - r["fav_y"]) / no_cost) - stake
            daily_pnl += pnl
            legs.append(("P", r["fav_y"], no_cost, stake, pnl))
        bankroll += daily_pnl
        peak = max(peak, bankroll)
        dd = 1 - bankroll / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
        if legs:
            rows.append({
                "day": str(r["local_day"])[:10],
                "legs": ",".join(l[0] for l in legs),
                "daily_pnl": round(daily_pnl, 0),
                "bankroll": round(bankroll, 0),
            })

    print(f"\nFinal bankroll: ${round(bankroll, 0):,.0f}")
    print(f"Peak:           ${round(peak, 0):,.0f}")
    print(f"Max drawdown:   {round(max_dd*100, 1)}%")
    print(f"\nPer-day entries (combined):")
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))


def solo_d_comparison(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== SOLO D (2% Kelly, conservative) for comparison ===")
    df = con.execute("""
        SELECT local_day, d_y, d_entry_cost
        FROM day_portfolio
        WHERE d_entry_cost IS NOT NULL
        ORDER BY local_day
    """).df()
    bankroll = 10_000.0
    peak = bankroll
    max_dd = 0.0
    for _, r in df.iterrows():
        stake = bankroll * 0.02
        pnl = stake * (r["d_y"] / r["d_entry_cost"]) - stake
        bankroll += pnl
        peak = max(peak, bankroll)
        max_dd = max(max_dd, 1 - bankroll / peak)
    print(f"Final bankroll: ${round(bankroll, 0):,.0f}")
    print(f"Peak:           ${round(peak, 0):,.0f}")
    print(f"Max drawdown:   {round(max_dd*100, 1)}%")


def main() -> None:
    con = duckdb.connect()
    build(con)
    joint_coverage(con)
    portfolio_sim(con)
    solo_d_comparison(con)


if __name__ == "__main__":
    main()

"""Experiment 20 — Combined portfolio with REAL bid/ask costs.

Exp17 ran the D+F+P portfolio with a 3c spread placeholder. Exp06b showed
real spreads at 12 EDT are ~0c median. Exp19 showed 16/18 EDT books are
active. This exp does two things:

1. Reconstruct real bid/ask (last YES BUY / last YES SELL before t) at
   12 EDT, 16 EDT, 18 EDT for the Strategy D +2 bucket AND the favorite
   (for the short legs).

2. Re-run the D + F + P combined-portfolio Kelly sim using those real
   asks/bids. Project the true edge.

Expected uplift vs exp17 (placeholder): ~2x on D per trade, ~1.5x on F/P
short legs. Combined portfolio expected ~4.5x multiple (vs 2.96x).
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 80)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

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
        CREATE OR REPLACE TEMP TABLE nyc_range AS
        SELECT slug, group_item_title AS strike,
               CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
               CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
               CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
        FROM '{MARKETS}'
        WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%' AND closed
          AND group_item_title NOT ILIKE '%or %'
    """)


def build_snapshot(con: duckdb.DuckDBPyConnection, hour_utc: int) -> None:
    """Build per-day favorite + strategy D target + real bid/ask at a given hour."""
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
        SELECT local_day, slug AS fav_slug, lo_f AS fav_lo, hi_f AS fav_hi, strike AS fav_strike,
               p_mid AS fav_p_mid, target_ts
        FROM ranked WHERE rk = 1
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE d_target_h{hour_utc} AS
        SELECT f.local_day, f.fav_slug, f.fav_strike, f.fav_lo, f.target_ts, f.fav_p_mid, f.fav_hi,
               s.slug AS d_slug, s.strike AS d_strike, s.lo_f AS d_lo, s.hi_f AS d_hi,
               s.p_mid AS d_mid
        FROM fav_h{hour_utc} f
        JOIN snap_h{hour_utc} s ON s.local_day = f.local_day AND s.lo_f = f.fav_lo + 2
        WHERE s.p_mid IS NOT NULL AND s.p_mid >= 0.02
    """)

    # Real ask (last YES BUY) and bid (last YES SELL) for BOTH fav (short leg) and d_target (long leg)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE bid_ask_h{hour_utc} AS
        SELECT
            dt.local_day, dt.d_slug, dt.d_strike, dt.d_lo, dt.d_hi, dt.d_mid,
            dt.fav_slug, dt.fav_strike, dt.fav_hi, dt.fav_p_mid,
            -- d_target (long leg): real YES ask
            (SELECT price FROM '{FILLS}' f
             WHERE f.slug = dt.d_slug AND f.timestamp <= dt.target_ts
               AND UPPER(f.outcome) = 'YES' AND UPPER(f.side) = 'BUY'
             ORDER BY f.timestamp DESC LIMIT 1) AS d_ask,
            -- fav (short leg): real YES bid (we're selling YES = buying NO)
            (SELECT price FROM '{FILLS}' f
             WHERE f.slug = dt.fav_slug AND f.timestamp <= dt.target_ts
               AND UPPER(f.outcome) = 'YES' AND UPPER(f.side) = 'SELL'
             ORDER BY f.timestamp DESC LIMIT 1) AS fav_bid
        FROM d_target_h{hour_utc} dt
    """)


def portfolio_sim(con: duckdb.DuckDBPyConnection, hour_utc: int, label: str) -> None:
    edt = hour_utc - 4
    print(f"\n=== COMBINED PORTFOLIO @ {edt} EDT ({label}) — REAL BID/ASK COSTS ===")

    df = con.execute(f"""
        SELECT
            ba.local_day, ba.d_strike, ba.d_lo, ba.d_hi, ba.d_mid, ba.d_ask,
            ba.fav_strike, ba.fav_hi, ba.fav_p_mid, ba.fav_bid,
            md.day_max_whole,
            CASE WHEN md.day_max_whole BETWEEN ba.d_lo AND ba.d_hi THEN 1 ELSE 0 END AS d_y,
            CASE WHEN md.day_max_whole BETWEEN
                    CAST(regexp_extract(ba.fav_strike, '(-?\\d+)-', 1) AS INT) AND ba.fav_hi
                  THEN 1 ELSE 0 END AS fav_y,
            m12.skyc1, (ba.d_lo - 2 - m12.tmpf) AS rise_needed
        FROM bid_ask_h{hour_utc} ba
        JOIN metar_daily md ON md.local_date = ba.local_day
        LEFT JOIN metar_12edt m12 ON m12.local_date = ba.local_day
    """).df()

    bankroll = 10_000.0
    peak = bankroll
    max_dd = 0.0
    events = []

    for _, r in df.iterrows():
        daily_pnl = 0.0
        legs = []
        # Strategy D long leg — real ask or fall back to mid
        d_entry = r["d_ask"] if pd.notna(r["d_ask"]) else r["d_mid"]
        if d_entry is not None and d_entry > 0 and d_entry < 0.97:
            stake = bankroll * 0.02
            cost = d_entry * (1 + FEE)
            pnl = stake * (r["d_y"] / cost) - stake
            daily_pnl += pnl
            legs.append(("D", pnl))

        # Strategy F (clear sky + rise<3 → short fav)
        if r["skyc1"] in ("CLR", "FEW", "SCT") and r["rise_needed"] is not None and r["rise_needed"] < 3:
            # NO cost = 1 - fav_bid (if we have it) or 1 - fav_p_mid
            fav_ask_no = (1 - (r["fav_bid"] if pd.notna(r["fav_bid"]) else r["fav_p_mid"]))
            if fav_ask_no > 0 and fav_ask_no < 0.97:
                stake = bankroll * 0.02
                cost = fav_ask_no * (1 + FEE)
                pnl = stake * ((1 - r["fav_y"]) / cost) - stake
                daily_pnl += pnl
                legs.append(("F", pnl))

        # Strategy P (peaked) — skip if F already fired
        # For simplicity: fire P if fav_p_mid >= 0.60 AND F did not already fire
        if r["fav_p_mid"] is not None and r["fav_p_mid"] >= 0.60 and not any(l[0] == "F" for l in legs):
            fav_ask_no = (1 - (r["fav_bid"] if pd.notna(r["fav_bid"]) else r["fav_p_mid"]))
            if fav_ask_no > 0 and fav_ask_no < 0.97:
                stake = bankroll * 0.02
                cost = fav_ask_no * (1 + FEE)
                pnl = stake * ((1 - r["fav_y"]) / cost) - stake
                daily_pnl += pnl
                legs.append(("P", pnl))

        if legs:
            bankroll += daily_pnl
            peak = max(peak, bankroll)
            dd = 1 - bankroll / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
            events.append({
                "day": str(r["local_day"])[:10],
                "legs": ",".join(l[0] for l in legs),
                "daily_pnl": round(daily_pnl, 0),
                "bankroll": round(bankroll, 0),
            })

    print(f"    n trade-days: {len(events)}")
    print(f"    final bankroll: ${bankroll:,.0f}")
    print(f"    peak:           ${peak:,.0f}")
    print(f"    max drawdown:   {max_dd*100:.1f}%")
    print(f"    multiple:       {bankroll/10_000:.2f}x")


def solo_d_sim(con: duckdb.DuckDBPyConnection, hour_utc: int, label: str) -> None:
    edt = hour_utc - 4
    df = con.execute(f"""
        SELECT ba.local_day, ba.d_lo, ba.d_hi, ba.d_ask, ba.d_mid, md.day_max_whole,
               CASE WHEN md.day_max_whole BETWEEN ba.d_lo AND ba.d_hi THEN 1 ELSE 0 END AS y
        FROM bid_ask_h{hour_utc} ba
        JOIN metar_daily md ON md.local_date = ba.local_day
    """).df()
    bankroll = 10_000.0
    peak = bankroll
    max_dd = 0.0
    n_bets = 0
    for _, r in df.iterrows():
        entry = r["d_ask"] if pd.notna(r["d_ask"]) else r["d_mid"]
        if entry is None or entry <= 0 or entry >= 0.97:
            continue
        stake = bankroll * 0.02
        cost = entry * (1 + FEE)
        pnl = stake * (r["y"] / cost) - stake
        bankroll += pnl
        n_bets += 1
        peak = max(peak, bankroll)
        max_dd = max(max_dd, 1 - bankroll / peak)
    print(f"\n=== SOLO D @ {edt} EDT real ask, 2% Kelly ({label}) ===")
    print(f"    n:             {n_bets}")
    print(f"    final:         ${bankroll:,.0f}")
    print(f"    max drawdown:  {max_dd*100:.1f}%")
    print(f"    multiple:      {bankroll/10_000:.2f}x")


def spread_summary_by_hour(con: duckdb.DuckDBPyConnection, hour_utc: int) -> None:
    edt = hour_utc - 4
    print(f"\n=== SPREAD SUMMARY @ {edt} EDT ===")
    print(con.execute(f"""
        SELECT
            COUNT(*) AS n,
            COUNT(*) FILTER (WHERE d_ask IS NOT NULL) AS n_with_d_ask,
            ROUND(AVG(d_ask - d_mid), 4) AS mean_d_ask_vs_mid,
            ROUND(QUANTILE_CONT(d_ask - d_mid, 0.5), 4) AS med_d_ask_vs_mid,
            COUNT(*) FILTER (WHERE fav_bid IS NOT NULL) AS n_with_fav_bid,
            ROUND(AVG(fav_bid - fav_p_mid), 4) AS mean_fav_bid_vs_mid,
            ROUND(QUANTILE_CONT(fav_bid - fav_p_mid, 0.5), 4) AS med_fav_bid_vs_mid
        FROM bid_ask_h{hour_utc}
    """).df())


def main() -> None:
    con = duckdb.connect()
    build(con)

    for h, label in [(16, "12 EDT"), (20, "16 EDT"), (22, "18 EDT")]:
        build_snapshot(con, h)
        spread_summary_by_hour(con, h)
        solo_d_sim(con, h, label)
        portfolio_sim(con, h, label)


if __name__ == "__main__":
    main()

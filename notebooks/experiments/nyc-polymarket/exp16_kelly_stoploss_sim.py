"""Experiment 16 — Kelly sizing with weekly stop-loss simulation for Strategy D.

Exp14 showed max consecutive losing streak of 6 and drawdown 21.7% at 4%
Kelly. This exp stress-tests the sizing rule by walking the trade sequence
under different Kelly fractions and stop-loss regimes.

Variants:
    - No stop-loss, Kelly ∈ {1%, 2%, 4%, 6%}
    - Weekly 15% drawdown cap (skip new trades until week resets)
    - "Rolling 5-bet" cap: after 5 consecutive losses, pause 3 bets
    - Fixed-notional control (every bet = $100)

All start at $10,000 bankroll. Track: final equity, max drawdown from
peak, Sharpe-ish metric.

The goal is a concrete deployment rule: "bet X% per trade with a stop-loss
at Y" such that max drawdown stays <15% in the 44-bet backtest and we
still capture most of the PnL.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"
SPREAD = 0.03
FEE = 0.02
START_BANKROLL = 10_000.0


def build_trade_sequence(con: duckdb.DuckDBPyConnection, conservative: bool = True) -> pd.DataFrame:
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
        SELECT local_day, lo_f AS fav_lo, hi_f AS fav_hi, p12 AS fav_p, day_max_whole
        FROM ranked WHERE rk = 1
    """)
    cons_filter = "AND r.p12 >= 0.02" if conservative else ""
    df = con.execute(f"""
        SELECT f.local_day,
               r.strike, r.lo_f AS lo_bought, r.p12 AS p_entry,
               CASE WHEN f.day_max_whole BETWEEN r.lo_f AND r.hi_f THEN 1 ELSE 0 END AS y,
               (r.p12 + {SPREAD}) * (1 + {FEE}) AS entry_cost_per_share
        FROM fav f
        JOIN range_12 r ON r.local_day = f.local_day AND r.lo_f = f.fav_lo + 2
        WHERE r.p12 IS NOT NULL AND (r.p12 + {SPREAD}) < 0.97
          {cons_filter}
        ORDER BY f.local_day
    """).df()
    return df


def simulate(df: pd.DataFrame, kelly_frac: float, stop_rule: str = "none") -> dict:
    """Walk trades in order; bet `kelly_frac * bankroll` on each (shares = stake / entry_cost).

    Stop rules:
        "none"           — no stop
        "weekly_15"      — if week-to-date PnL <= -15% of week-start bankroll, pause rest of week
        "streak_5_pause" — after 5 consecutive losses, skip next 3 bets
    """
    bankroll = START_BANKROLL
    peak = bankroll
    max_dd = 0.0
    equity = [bankroll]
    n_bets = 0
    n_skipped = 0
    n_wins = 0
    streak = 0
    skip_count = 0

    # Week tracking
    import datetime as dt
    current_week_start = None
    week_start_bankroll = bankroll

    for _, r in df.iterrows():
        day = pd.to_datetime(r["local_day"]).to_pydatetime().date()
        iso_week = day.isocalendar()

        if stop_rule == "weekly_15":
            if current_week_start is None or iso_week != current_week_start:
                current_week_start = iso_week
                week_start_bankroll = bankroll
            if bankroll <= 0.85 * week_start_bankroll:
                n_skipped += 1
                equity.append(bankroll)
                continue

        if stop_rule == "streak_5_pause" and skip_count > 0:
            skip_count -= 1
            n_skipped += 1
            equity.append(bankroll)
            continue

        stake = bankroll * kelly_frac
        if stake <= 0 or bankroll <= 0:
            break
        shares = stake / r["entry_cost_per_share"]
        payoff = shares * (1 if r["y"] == 1 else 0)
        pnl = payoff - stake
        bankroll += pnl
        n_bets += 1
        if r["y"] == 1:
            n_wins += 1
            streak = 0
        else:
            streak += 1
            if stop_rule == "streak_5_pause" and streak >= 5:
                skip_count = 3

        peak = max(peak, bankroll)
        dd = 1 - bankroll / peak if peak > 0 else 1.0
        max_dd = max(max_dd, dd)
        equity.append(bankroll)

    return {
        "kelly": f"{kelly_frac*100:.0f}%",
        "stop": stop_rule,
        "n_bets": n_bets,
        "n_skipped": n_skipped,
        "n_wins": n_wins,
        "final": round(bankroll, 0),
        "multiple": round(bankroll / START_BANKROLL, 2),
        "peak": round(peak, 0),
        "max_dd_pct": round(max_dd * 100, 1),
    }


def main() -> None:
    con = duckdb.connect()
    df = build_trade_sequence(con, conservative=True)
    print(f"\n=== TRADE SEQUENCE ===")
    print(f"    {len(df)} trades after conservative p_entry≥2¢ filter")
    print(f"    hit rate: {df['y'].mean():.1%}")
    print(f"    avg entry price: {df['p_entry'].mean():.3f}")

    results = []
    for k in [0.01, 0.02, 0.04, 0.06]:
        results.append(simulate(df, k, "none"))
    for k in [0.02, 0.04]:
        results.append(simulate(df, k, "weekly_15"))
    for k in [0.02, 0.04]:
        results.append(simulate(df, k, "streak_5_pause"))

    out = pd.DataFrame(results)
    print("\n=== KELLY + STOP-LOSS SIM (conservative entry, starting $10,000) ===")
    print(out)

    # Also run FULL (no conservative filter) for comparison
    df_full = build_trade_sequence(con, conservative=False)
    print(f"\n=== FULL SEQUENCE (no conservative filter) — n={len(df_full)} ===")
    r = simulate(df_full, 0.02, "none")
    print(f"    2% Kelly, no stop: {r}")
    r = simulate(df_full, 0.04, "none")
    print(f"    4% Kelly, no stop: {r}")

    # Per-day equity curve for 2% Kelly conservative no-stop
    print("\n=== EQUITY CURVE AT 2% KELLY, CONSERVATIVE, NO STOP ===")
    bankroll = START_BANKROLL
    eq_rows = []
    for _, r in df.iterrows():
        stake = bankroll * 0.02
        shares = stake / r["entry_cost_per_share"]
        payoff = shares * (1 if r["y"] == 1 else 0)
        pnl = payoff - stake
        bankroll += pnl
        eq_rows.append({
            "day": str(r["local_day"])[:10],
            "p": round(r["p_entry"], 3),
            "y": int(r["y"]),
            "stake": round(stake, 0),
            "pnl": round(pnl, 0),
            "bankroll": round(bankroll, 0),
        })
    eq = pd.DataFrame(eq_rows)
    print(eq.to_string(index=False))


if __name__ == "__main__":
    main()

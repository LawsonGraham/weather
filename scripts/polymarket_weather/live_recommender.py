"""Live Strategy D trade recommender for NYC Polymarket daily-temperature.

Reads the local processed markets.parquet + prices parquet and outputs a
trade recommendation for the current day's NYC daily-temperature market.
Implements the three deployable variants from the exploration loop:

    V5 @ 12 EDT   — buy fav_lo+2 bucket AFTER checking skip rules
    V1 @ 16 EDT   — buy fav_lo+2 bucket (no skip rules)
    V1 @ 18 EDT   — buy fav_lo+2 bucket (highest edge, smallest n)

Strategies write to stdout in caveman-full style — directly actionable.
Requires an active Polymarket Gamma API session (`markets.parquet`
freshly refreshed) and an IEM METAR feed (for the V5 skip rule).

Usage:
    uv run python scripts/polymarket_weather/live_recommender.py
        [--date YYYY-MM-DD]   # target day (default: today local)
        [--bankroll 10000]    # in USD
        [--kelly 0.02]        # per-bet fraction
        [--hour 12|16|18]     # which entry hour to recommend for
        [--all-hours]         # show V5@12 + V1@16 + V1@18

Example run for today at 16 EDT:
    uv run python scripts/polymarket_weather/live_recommender.py --hour 16

This is a RECOMMENDER, not an autotrader. Human confirms every trade
until the 30-day paper-trade gate passes. Logs to stdout for manual
logging into the paper-trade ledger.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import duckdb

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"
METAR = "data/processed/iem_metar/LGA/*.parquet"

FEE = 0.02
SPREAD_PADDING = 0.01  # conservative per-share safety margin when entering


def today_local() -> dt.date:
    return dt.datetime.now().astimezone().date()


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    return con


def get_range_ladder(con: duckdb.DuckDBPyConnection, target_day: dt.date, snapshot_hour_utc: int) -> list[dict]:
    """Fetch the range-strike ladder at a given UTC hour on target_day.

    Returns list of dicts sorted by price descending.
    """
    rows = con.execute(f"""
        WITH r AS (
            SELECT slug, group_item_title AS strike,
                   CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT) AS lo_f,
                   CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT) AS hi_f,
                   CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day
            FROM '{MARKETS}'
            WHERE city='New York City' AND weather_tags ILIKE '%Daily Temperature%'
              AND group_item_title NOT ILIKE '%or %'
        )
        SELECT r.slug, r.strike, r.lo_f, r.hi_f, r.local_day,
            (SELECT yes_price FROM '{PRICES}' p
             WHERE p.slug = r.slug
               AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '{snapshot_hour_utc} hour')
             ORDER BY p.timestamp DESC LIMIT 1) AS p_mid
        FROM r
        WHERE r.local_day = DATE '{target_day.isoformat()}'
          AND (SELECT yes_price FROM '{PRICES}' p
               WHERE p.slug = r.slug
                 AND p.timestamp <= (CAST(r.local_day AS TIMESTAMPTZ) + INTERVAL '{snapshot_hour_utc} hour')
               ORDER BY p.timestamp DESC LIMIT 1) IS NOT NULL
        ORDER BY p_mid DESC NULLS LAST
    """).df()
    return rows.to_dict(orient="records")


def get_real_ask(con: duckdb.DuckDBPyConnection, slug: str, target_ts_utc: str) -> float | None:
    """Last YES-BUY fill strictly before target_ts — point estimate of YES ask."""
    row = con.execute(f"""
        SELECT price FROM '{FILLS}' f
        WHERE f.slug = '{slug}' AND f.timestamp <= TIMESTAMPTZ '{target_ts_utc}'
          AND UPPER(f.outcome)='YES' AND UPPER(f.side)='BUY'
        ORDER BY f.timestamp DESC LIMIT 1
    """).fetchone()
    return float(row[0]) if row else None


def get_metar_12edt(con: duckdb.DuckDBPyConnection, target_day: dt.date) -> dict | None:
    row = con.execute(f"""
        WITH ranked AS (
            SELECT valid, tmpf, relh, skyc1,
                   ROW_NUMBER() OVER (
                       ORDER BY ABS(EXTRACT(EPOCH FROM (valid - (CAST(DATE '{target_day.isoformat()}' AS TIMESTAMPTZ) + INTERVAL '16 hour'))))
                   ) AS rk
            FROM '{METAR}'
            WHERE station='LGA'
              AND CAST((valid AT TIME ZONE 'America/New_York') AS DATE) = DATE '{target_day.isoformat()}'
              AND EXTRACT(HOUR FROM (valid AT TIME ZONE 'America/New_York')) BETWEEN 11 AND 13
        )
        SELECT tmpf, relh, skyc1 FROM ranked WHERE rk = 1
    """).fetchone()
    if row:
        return {"tmpf": row[0], "relh": row[1], "skyc1": row[2]}
    return None


def format_recommendation(
    version: str,
    hour_edt: int,
    fav: dict,
    target: dict,
    entry_price: float,
    bankroll: float,
    kelly: float,
    skip_reason: str | None,
) -> str:
    out = []
    out.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    out.append(f"  {version} @ {hour_edt:02d} EDT — NYC Polymarket Daily-Temp")
    out.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    out.append(f"  Favorite:     {fav['strike']}  @ ${fav['p_mid']:.3f}")
    out.append(f"  Target (+2):  {target['strike']}  @ ${target['p_mid']:.3f}")
    if skip_reason:
        out.append(f"  STATUS: SKIP ({skip_reason})")
        return "\n".join(out)
    cost = entry_price * (1 + FEE)
    stake = bankroll * kelly
    shares = stake / cost
    payoff = stake / cost
    profit_if_hit = payoff - stake
    out.append(f"  Entry ask:    ${entry_price:.4f}  (real-ask, +{SPREAD_PADDING:.2f} safety)")
    out.append(f"  Entry cost:   ${cost:.4f}  (with {FEE*100:.0f}% fee)")
    out.append(f"  Stake:        ${stake:.2f}  ({kelly*100:.1f}% of ${bankroll:,.0f})")
    out.append(f"  Shares:       {shares:.2f}")
    out.append(f"  Payoff if hit: ${payoff:.2f}  → profit ${profit_if_hit:+,.2f}")
    out.append(f"  Loss if miss:  -${stake:.2f}")
    out.append(f"")
    out.append(f"  ACTION: BUY {shares:.1f} shares YES on slug:")
    out.append(f"     {target['slug']}")
    out.append(f"     at limit price ≤ ${cost:.4f}")
    return "\n".join(out)


def recommend(
    con: duckdb.DuckDBPyConnection,
    target_day: dt.date,
    hour_edt: int,
    bankroll: float,
    kelly: float,
) -> str:
    hour_utc = hour_edt + 4  # EDT = UTC-4
    ladder = get_range_ladder(con, target_day, hour_utc)
    if not ladder:
        return f"[{hour_edt:02d} EDT] no ladder data for {target_day} at UTC hour {hour_utc}"

    fav = ladder[0]  # sorted by p_mid desc
    target_lo = int(fav["lo_f"]) + 2
    target = next((r for r in ladder if int(r["lo_f"]) == target_lo), None)
    if target is None:
        return f"[{hour_edt:02d} EDT] no +2 bucket (fav_lo+2 = {target_lo}) in ladder for {target_day}"
    if target["p_mid"] < 0.02:
        return f"[{hour_edt:02d} EDT] +2 bucket priced too low (${target['p_mid']:.4f} < $0.02), skip"

    # Strategy selection by hour
    version = ""
    skip_reason = None
    if hour_edt == 12:
        version = "V5"
        metar = get_metar_12edt(con, target_day)
        if metar is None:
            skip_reason = "no METAR at 12 EDT for skip-rule evaluation"
        else:
            rise_needed = int(fav["lo_f"]) - metar["tmpf"]
            if metar["relh"] is not None and metar["relh"] < 40:
                skip_reason = f"dry regime (relh={metar['relh']:.0f}% < 40%)"
            elif rise_needed >= 6:
                skip_reason = f"high forecast-rise (rise_needed={rise_needed:.0f}°F ≥ 6°F)"
    elif hour_edt in (16, 18):
        version = "V1"
    else:
        version = f"V1 @{hour_edt}"

    if skip_reason:
        return format_recommendation(version, hour_edt, fav, target, 0, bankroll, kelly, skip_reason)

    # Real ask from last YES BUY fill
    target_ts = f"{target_day.isoformat()} {hour_utc:02d}:00:00+00"
    real_ask = get_real_ask(con, target["slug"], target_ts)
    entry_price = (real_ask if real_ask else float(target["p_mid"])) + SPREAD_PADDING
    return format_recommendation(version, hour_edt, fav, target, entry_price, bankroll, kelly, None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None, help="Target day YYYY-MM-DD (default: today local)")
    ap.add_argument("--bankroll", type=float, default=10_000.0)
    ap.add_argument("--kelly", type=float, default=0.02)
    ap.add_argument("--hour", type=int, default=None, help="Entry hour in EDT (12, 16, or 18)")
    ap.add_argument("--all-hours", action="store_true", help="Show all three hours")
    args = ap.parse_args()

    target_day = dt.date.fromisoformat(args.date) if args.date else today_local()
    con = connect()

    hours = [args.hour] if args.hour else ([12, 16, 18] if args.all_hours else [16])

    print(f"\n== Strategy D live recommender — target day {target_day} ==")
    print(f"   bankroll ${args.bankroll:,.0f}, Kelly {args.kelly*100:.1f}%")
    for h in hours:
        print()
        print(recommend(con, target_day, h, args.bankroll, args.kelly))
    print()


if __name__ == "__main__":
    main()

"""Paper-trade JSON ledger for the NYC Polymarket Strategy D pipeline.

Append-only JSONL file at `data/processed/paper_trades/nyc_strategy_d.jsonl`.
Each line is one paper trade with entry details, target, and outcome.

CLI:
    log    — append a new trade (e.g., from live_recommender output)
    score  — score open trades against METAR realized day_max
    report — print summary of all trades and live PnL

Usage:
    # log a trade you just placed
    uv run python scripts/polymarket_weather/paper_ledger.py log \\
        --slug highest-temperature-in-nyc-on-april-11-2026-64-65f \\
        --strike "64-65°F" --side YES \\
        --entry 0.153 --shares 1307.19 --stake 200 --version "V1@16EDT"

    # score open trades after the day resolves (next morning)
    uv run python scripts/polymarket_weather/paper_ledger.py score

    # print running PnL
    uv run python scripts/polymarket_weather/paper_ledger.py report
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import duckdb

LEDGER = Path("data/processed/paper_trades/nyc_strategy_d.jsonl")
METAR = "data/processed/iem_metar/LGA/*.parquet"


def ensure_ledger() -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if not LEDGER.exists():
        LEDGER.touch()


def load_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    rows: list[dict] = []
    with LEDGER.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_row(row: dict) -> None:
    ensure_ledger()
    with LEDGER.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def parse_strike_bounds(strike: str) -> tuple[int, int]:
    """Parse '64-65°F' into (64, 65)."""
    import re
    m = re.search(r"(-?\d+)-(-?\d+)", strike)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise ValueError(f"could not parse strike: {strike}")


def cmd_log(args: argparse.Namespace) -> None:
    today = dt.date.today() if not args.date else dt.date.fromisoformat(args.date)
    lo, hi = parse_strike_bounds(args.strike)
    row = {
        "logged_at": dt.datetime.now().isoformat(timespec="seconds"),
        "trade_date": today.isoformat(),
        "slug": args.slug,
        "strike": args.strike,
        "lo_f": lo,
        "hi_f": hi,
        "side": args.side.upper(),
        "entry_price": args.entry,
        "shares": args.shares,
        "stake_usd": args.stake,
        "version": args.version,
        "status": "open",
        "outcome_resolved": None,
        "day_max_whole": None,
        "y": None,
        "pnl_usd": None,
    }
    append_row(row)
    print(f"logged: {args.version} {args.strike} {args.side} ${args.shares:.2f} shares "
          f"at ${args.entry:.4f}, stake ${args.stake:.2f}")


def fetch_day_max(con: duckdb.DuckDBPyConnection, target_day: dt.date) -> int | None:
    row = con.execute(f"""
        WITH m AS (
            SELECT CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                   GREATEST(COALESCE(tmpf, -999),
                            COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999)) AS te
            FROM '{METAR}' WHERE station='LGA'
        )
        SELECT ROUND(MAX(te))::INT AS day_max
        FROM m WHERE te > -900 AND local_date = DATE '{target_day.isoformat()}'
    """).fetchone()
    return row[0] if row and row[0] is not None else None


def cmd_score(args: argparse.Namespace) -> None:
    rows = load_ledger()
    if not rows:
        print("ledger empty")
        return
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    updated = 0
    for row in rows:
        if row["status"] != "open":
            continue
        trade_day = dt.date.fromisoformat(row["trade_date"])
        # Don't score until at least the next day
        if trade_day >= dt.date.today():
            continue

        day_max = fetch_day_max(con, trade_day)
        if day_max is None:
            print(f"  no METAR truth yet for {trade_day} — skip")
            continue

        # YES wins if day_max in [lo, hi]; NO wins otherwise
        in_range = row["lo_f"] <= day_max <= row["hi_f"]
        if row["side"] == "YES":
            y = 1 if in_range else 0
        else:
            y = 0 if in_range else 1

        # Payout: 1 share pays $1 if win, $0 if loss
        gross_payout = row["shares"] * y
        pnl_usd = gross_payout - row["stake_usd"]

        row["status"] = "resolved"
        row["outcome_resolved"] = dt.datetime.now().isoformat(timespec="seconds")
        row["day_max_whole"] = day_max
        row["y"] = y
        row["pnl_usd"] = round(pnl_usd, 2)
        updated += 1
        print(f"  {row['trade_date']} {row['strike']} {row['side']}: "
              f"day_max={day_max}, y={y}, pnl=${pnl_usd:+.2f}")

    if updated:
        # Rewrite ledger with updated rows
        ensure_ledger()
        with LEDGER.open("w") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")
        print(f"updated {updated} resolved trades")
    else:
        print("nothing to score")


def cmd_report(args: argparse.Namespace) -> None:
    rows = load_ledger()
    if not rows:
        print("ledger empty")
        return
    n_open = sum(1 for r in rows if r["status"] == "open")
    n_resolved = sum(1 for r in rows if r["status"] == "resolved")
    n_wins = sum(1 for r in rows if r["status"] == "resolved" and r["y"] == 1)
    cum_pnl = sum(r["pnl_usd"] for r in rows if r["status"] == "resolved")
    cum_stake = sum(r["stake_usd"] for r in rows if r["status"] == "resolved")
    print(f"╔══ STRATEGY D PAPER LEDGER ══╗")
    print(f"  trades total:    {len(rows)}")
    print(f"  open:            {n_open}")
    print(f"  resolved:        {n_resolved}")
    if n_resolved:
        hit_rate = n_wins / n_resolved
        roc = cum_pnl / cum_stake * 100 if cum_stake else 0
        print(f"  resolved wins:   {n_wins} ({hit_rate*100:.1f}%)")
        print(f"  cum stake:       ${cum_stake:,.2f}")
        print(f"  cum PnL:         ${cum_pnl:+,.2f}")
        print(f"  return on cap:   {roc:+.1f}%")
    print(f"╚══════════════════════════════╝")
    print()
    if args.detail:
        for r in rows:
            status = r["status"]
            if status == "resolved":
                tag = "WIN" if r["y"] == 1 else "LOSS"
                print(f"  {r['trade_date']}  {r['version']:<10}  {r['strike']:<10}  "
                      f"{r['side']}  stake ${r['stake_usd']:.0f}  → {tag}  pnl ${r['pnl_usd']:+,.0f}")
            else:
                print(f"  {r['trade_date']}  {r['version']:<10}  {r['strike']:<10}  "
                      f"{r['side']}  stake ${r['stake_usd']:.0f}  → OPEN")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("log", help="append a new trade")
    pl.add_argument("--slug", required=True)
    pl.add_argument("--strike", required=True, help="e.g., 64-65°F")
    pl.add_argument("--side", choices=["YES", "NO", "yes", "no"], default="YES")
    pl.add_argument("--entry", type=float, required=True)
    pl.add_argument("--shares", type=float, required=True)
    pl.add_argument("--stake", type=float, required=True)
    pl.add_argument("--version", default="V1@16EDT")
    pl.add_argument("--date", default=None, help="trade date YYYY-MM-DD (default: today)")
    pl.set_defaults(func=cmd_log)

    ps = sub.add_parser("score", help="score open trades against METAR truth")
    ps.set_defaults(func=cmd_score)

    pr = sub.add_parser("report", help="print PnL summary")
    pr.add_argument("--detail", action="store_true")
    pr.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

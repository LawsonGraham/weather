"""Consensus-Fade +1 — CLI.

This is the one-stop operator surface. Read these five subcommands
to understand the whole system:

  cfp setup [--check]
      One-time wallet bootstrap (allowances + L2 API creds). Safe to re-run.

  cfp discover [--date YYYY-MM-DD]
      Show today's tradeable markets (no orders placed).

  cfp run [--max-no-price 0.92] [--shares-per-market 110]
      Start the live trading node.
      One resting NO-buy per tradeable market. Ctrl+C to stop (cancels orders).

  cfp daemon
      Start the weather/market data watchers.
      Keeps data/processed/ fresh so `cfp discover` always has current info.

  cfp watchers
      Show last-poll status of each watcher.

Typical operator flow:
  1. cfp setup             # one time ever
  2. cfp daemon &          # background — keeps data fresh
  3. cfp discover          # sanity check what's tradeable today
  4. cfp run               # foreground — places orders, waits for fills
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def cmd_setup(args: argparse.Namespace) -> int:
    from consensus_fade_plus1.setup import check_setup, run_setup
    return check_setup() if args.check else run_setup(force_rederive=args.rederive)


def cmd_discover(args: argparse.Namespace) -> int:
    from consensus_fade_plus1.discover import (
        discover_tradeable_markets,
        print_discovery_summary,
    )
    target = _parse_date(args.date)
    markets = discover_tradeable_markets(target, consensus_max=args.consensus_max)
    print_discovery_summary(markets)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from consensus_fade_plus1.node import run
    return run(max_no_price=args.max_no_price,
              shares_per_market=args.shares_per_market)


def cmd_daemon(args: argparse.Namespace) -> int:
    import asyncio
    from lib.watchers import (
        FeaturesWatcher, GFSWatcher, HRRRWatcher, MarketsWatcher,
        METARWatcher, NBSWatcher, run_watchers,
    )
    print("[cfp daemon] starting data watchers. Ctrl+C to stop cleanly.")
    asyncio.run(run_watchers([
        NBSWatcher(), GFSWatcher(), HRRRWatcher(),
        METARWatcher(), MarketsWatcher(), FeaturesWatcher(),
    ]))
    return 0


def cmd_watchers(args: argparse.Namespace) -> int:
    state_dir = REPO_ROOT / "data" / "processed" / "watchers"
    if not state_dir.exists():
        print("No watcher state yet — run `cfp daemon` first.")
        return 0
    for f in sorted(state_dir.glob("*.state.json")):
        s = json.loads(f.read_text())
        last_ok = s.get("last_success_at") or "never"
        fails = s.get("consecutive_failures", 0)
        total = s.get("total_polls", 0)
        print(f"  {s['name']:<12}  last_ok={last_ok}  polls={total}  streak={fails}")
        if s.get("last_error"):
            print(f"               last_error: {s['last_error']}")
    return 0


def _parse_date(s: str | None) -> date:
    return datetime.now(UTC).date() if not s else date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cfp", description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="Wallet bootstrap (one-time)")
    p.add_argument("--check", action="store_true", help="Status check only")
    p.add_argument("--rederive", action="store_true", help="Re-derive API creds")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("discover", help="Show today's tradeable markets")
    p.add_argument("--date", default=None)
    p.add_argument("--consensus-max", type=float, default=3.0)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("run", help="Start the live trading node")
    p.add_argument("--max-no-price", type=float, default=0.92)
    p.add_argument("--shares-per-market", type=int, default=110)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("daemon", help="Start data watchers")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("watchers", help="Show watcher status")
    p.set_defaults(func=cmd_watchers)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as e:
        sys.stderr.write(f"\nError: {e}\n")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        return 130
    except Exception as e:
        sys.stderr.write(f"\nUnexpected error: {type(e).__name__}: {e}\n")
        if args.verbose:
            import traceback
            traceback.print_exc()
        else:
            sys.stderr.write("(run with --verbose for full traceback)\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())

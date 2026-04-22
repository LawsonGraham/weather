"""Consensus-Fade +1 — CLI.

Seven subcommands, grouped by purpose:

  --- Setup (one time) ---
  cfp setup [--check]
      Wallet bootstrap (allowances + L2 API creds). Safe to re-run.

  --- Data ingestion ---
  cfp daemon
      Run all 6 data watchers concurrently. Probes every 10s, fetches
      only when upstream has new data. Ctrl+C to stop cleanly.

  cfp watch <name>
      Run a SINGLE watcher in the foreground for testing. Same probe
      cadence; timestamped output. name ∈ {nbs, gfs, hrrr, metar,
      markets, features}.

  cfp watchers
      Show last-probe + last-fetch state of each watcher (reads state
      files; no network calls).

  --- Trading ---
  cfp discover [--date YYYY-MM-DD]
      Show today's tradeable markets (no orders placed).

  cfp run [--max-no-price 0.92] [--shares-per-market 110]
      Start the live trading node. One resting NO-buy per tradeable
      market; Polymarket matches against arriving asks ≤ our price.
      Persists: orders → cfp_ledger/, book snapshots every 10min →
      cfp_book_snapshots/. Ctrl+C cancels unfilled orders cleanly.

Typical operator flow:
  1. cfp setup                  # one time ever
  2. cfp daemon &               # background — keeps weather+markets fresh
  3. cfp watch metar            # optional — eyeball one watcher live
  4. cfp discover               # sanity check what's tradeable today
  5. cfp run                    # foreground — places orders, records fills
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
    return run(
        max_no_price=args.max_no_price,
        shares_per_market=args.shares_per_market,
        lookahead_days=args.lookahead_days,
        max_submissions=args.max_submissions,
        min_entry_hour_local=args.min_entry_hour_local,
        max_yes_ask=args.max_yes_ask,
        entry_window_minutes=args.entry_window_minutes,
        max_usd_per_market=args.max_usd_per_market,
    )


def cmd_daemon(args: argparse.Namespace) -> int:
    import asyncio

    from lib.watchers import (
        FeaturesWatcher,
        GFSWatcher,
        HRRRWatcher,
        MarketsWatcher,
        METARWatcher,
        NBSWatcher,
        run_watchers,
    )
    print("[cfp daemon] starting all watchers. Ctrl+C to stop cleanly.")
    asyncio.run(run_watchers([
        NBSWatcher(), GFSWatcher(), HRRRWatcher(),
        METARWatcher(), MarketsWatcher(), FeaturesWatcher(),
    ]))
    return 0


# Maps the CLI name to the watcher class. Keep as simple dict so adding
# a new watcher is one line.
_WATCHERS = {
    "nbs": "NBSWatcher",
    "gfs": "GFSWatcher",
    "hrrr": "HRRRWatcher",
    "metar": "METARWatcher",
    "markets": "MarketsWatcher",
    "features": "FeaturesWatcher",
}


def cmd_watch(args: argparse.Namespace) -> int:
    """Run ONE watcher in the foreground. Great for smoke-testing a source."""
    import asyncio
    import importlib
    cls_name = _WATCHERS[args.name]
    mod = importlib.import_module("lib.watchers")
    watcher = getattr(mod, cls_name)()
    from lib.watchers.base import run_watchers
    print(f"[cfp watch] starting {args.name} "
          f"(probe every {watcher.interval}s, Ctrl+C to stop)")
    print(f"[cfp watch] state file: data/processed/watchers/{args.name}.state.json")
    asyncio.run(run_watchers([watcher]))
    return 0


def cmd_watchers(args: argparse.Namespace) -> int:
    """Print last-probe and last-fetch stats for each watcher."""
    state_dir = REPO_ROOT / "data" / "processed" / "watchers"
    if not state_dir.exists():
        print("No watcher state yet — run `cfp daemon` or `cfp watch <name>` first.")
        return 0
    files = sorted(state_dir.glob("*.state.json"))
    if not files:
        print("No watchers have ticked yet.")
        return 0
    for f in files:
        s = json.loads(f.read_text())
        name = s.get("name", f.stem)
        last_probe = s.get("last_probe_at") or "never"
        last_fetch = s.get("last_fetch_success_at") or "never"
        probes = s.get("total_probes", 0)
        fetches = s.get("total_fetch_successes", 0)
        fails = s.get("consecutive_failures", 0)
        print(f"  {name:<10}  last_probe={last_probe}")
        print(f"              last_fetch_ok={last_fetch}")
        print(f"              probes={probes}  fetches_ok={fetches}  "
              f"consec_fails={fails}")
        if s.get("last_error"):
            print(f"              last_error: {s['last_error']}")
        if s.get("last_detail"):
            print(f"              last_detail: {s['last_detail']}")
    return 0


def _parse_date(s: str | None) -> date:
    return datetime.now(UTC).date() if not s else date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cfp", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
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
    p.add_argument("--max-no-price", type=float, default=0.99,
                  help="Hard ceiling on NO buy price. Default 0.99 lets "
                       "the market-wisdom cap do the real filtering.")
    p.add_argument("--shares-per-market", type=int, default=110)
    p.add_argument("--lookahead-days", type=int, default=1,
                  help="Subscribe to today + N days ahead (default 1)")
    p.add_argument("--max-submissions", type=int, default=None,
                  help="Hard cap on IOC submissions this session — strategy "
                       "flips the circuit breaker after N submissions "
                       "regardless of outcome. Default: unlimited.")
    p.add_argument("--min-entry-hour-local", type=int, default=16,
                  help="Minimum city-LOCAL hour (0-23) before each market "
                       "may fire. Evaluated per-instrument against the "
                       "airport's tz. Earlier floors collapse OOS (13 local "
                       "t=+1.06, 16 local t=+3.67/+7.70). Default 16. "
                       "Set to 0 to disable.")
    p.add_argument("--max-yes-ask", type=float, default=0.22,
                  help="Market-wisdom cap. Only trade when the current "
                       "best YES ask <= this threshold (via "
                       "best_no_bid >= 1 - cap). 0.22 is canonical "
                       "(100%% hit in backtest); 0.50 is the looser "
                       "v1-style variant (98.7%% hit, larger per-trade "
                       "edge). Set to 1.0 to disable. Default 0.22.")
    p.add_argument("--entry-window-minutes", type=int, default=30,
                  help="Bounded window after a market first becomes "
                       "eligible (all gates pass). Strategy keeps "
                       "lifting liquidity within this window, then "
                       "stops submitting IOCs even if gates are still "
                       "passing. Prevents edge decay from late fills. "
                       "Set to 1440 (24h) to effectively disable. "
                       "Default 30.")
    p.add_argument("--max-usd-per-market", type=float, default=30.0,
                  help="Hard per-market-per-day USD cap on cumulative "
                       "fill notional. Accumulates qty * fill_price as "
                       "fills arrive; once spent >= cap, strategy "
                       "blocks further IOCs on that market. Each "
                       "market_date has a distinct instrument_id so the "
                       "cap resets per market per day automatically. "
                       "Caveat: in-memory state — a strategy restart "
                       "mid-day currently resets the counter. "
                       "Default $30.")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("daemon", help="Start all data watchers")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("watch",
                      help="Run ONE watcher in foreground (for testing)")
    p.add_argument("name", choices=sorted(_WATCHERS.keys()),
                  help="Watcher to run")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("watchers", help="Show watcher state")
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

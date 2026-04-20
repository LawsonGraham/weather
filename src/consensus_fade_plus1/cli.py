"""Consensus-Fade +1 Offset — command-line interface.

Subcommands:
    cfp setup [--check] [--rederive]
        One-time wallet setup: USDC + CTF allowances + L2 API cred derivation.
        Idempotent — safe to re-run. --check reads status without submitting tx.

    cfp recommend [--date YYYY-MM-DD] [--consensus-max 3.0]
        Show today's Consensus-Fade recommendations (no order placement).
        Pulls live YES bid/ask from CLOB to estimate actual edge.

    cfp submit [--date YYYY-MM-DD] [--stake-usd 20] [--dry-run]
                [--consensus-max 3.0] [--yes-min 0.005] [--yes-max 0.5]
        Place live BUY-NO limit orders for each recommendation.
        --dry-run prints what would be submitted but doesn't call the API.
        Persists an append-only ledger to data/processed/cfp_ledger.jsonl.

    cfp cancel-all
        Cancel every open order on this account (use pre-resolution).

    cfp status
        Show open orders, recent fills, wallet balance.

Usage:
    uv run cfp setup
    uv run cfp recommend --date 2026-04-16
    uv run cfp submit --dry-run --stake-usd 20
    uv run cfp submit --stake-usd 20
    uv run cfp cancel-all
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "data" / "processed" / "cfp_ledger.jsonl"


def cmd_setup(args: argparse.Namespace) -> int:
    from lib.polymarket.setup import check_setup, run_setup
    if args.check:
        s = check_setup()
        _print_status(s)
        return 0
    print("[setup] starting Polymarket wallet setup (idempotent)")
    print("[setup] this will:")
    print("  1. Check/approve USDC allowances on 3 exchange contracts")
    print("  2. Check/approve ConditionalTokens allowances on 3 contracts")
    print("  3. Derive L2 API credentials and write to .env")
    print()
    print("[setup] Polygon gas cost: ~$0.50-2 depending on network")
    print("[setup] proceeding...")
    s = run_setup(force_rederive=args.rederive)
    print()
    _print_status(s)
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    from consensus_fade_plus1.strategy import apply_live_prices, build_recommendations
    from lib.polymarket.client import load_client_from_env

    target = _parse_date(args.date)
    print(f"Consensus-Fade +1 Offset — Recommendations for {target}")
    print(f"Filter: consensus_spread ≤ {args.consensus_max}°F")
    print("=" * 100)

    recs = build_recommendations(target, consensus_max=args.consensus_max)
    print(f"[recommend] {len(recs)} candidate markets after consensus filter")

    if args.no_live:
        # Use stored market parquet prices only
        _print_recs(recs)
        return 0

    try:
        client = load_client_from_env()
    except RuntimeError as e:
        print(f"[recommend] Cannot load CLOB client: {e}")
        print("[recommend] Run `uv run cfp setup` first. Showing without live prices.")
        _print_recs(recs)
        return 0

    recs_priced = apply_live_prices(
        client, recs, min_yes_price=args.yes_min, max_yes_price=args.yes_max,
    )
    print(f"[recommend] {len(recs_priced)} tradeable after price filter "
          f"[{args.yes_min}, {args.yes_max}]")
    print()
    _print_recs(recs_priced, with_prices=True)
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    from consensus_fade_plus1.strategy import apply_live_prices, build_recommendations
    from lib.polymarket.client import load_client_from_env
    from lib.polymarket.orders import place_limit_buy

    target = _parse_date(args.date)
    print(f"Consensus-Fade +1 Offset — Submit orders for {target}")
    if args.dry_run:
        print("[submit] DRY-RUN mode — no orders will be placed")
    print("=" * 100)

    try:
        client = load_client_from_env()
    except RuntimeError as e:
        print(f"[submit] {e}")
        return 2

    recs = build_recommendations(target, consensus_max=args.consensus_max)
    print(f"[submit] {len(recs)} candidate markets pass consensus filter")
    recs = apply_live_prices(client, recs,
                              min_yes_price=args.yes_min, max_yes_price=args.yes_max)
    print(f"[submit] {len(recs)} tradeable after live-price filter")
    if not recs:
        print("[submit] nothing to submit.")
        return 0

    ledger_rows = []
    for r in recs:
        # BUY NO at best ask (= 1 - best_yes_bid). We place slightly aggressive:
        # 1 tick worse than (1 - best_yes_bid) to be post-only on the NO book.
        no_ask = r.no_ask_estimate or 0.9
        no_limit_price = no_ask  # can tune — start at the ask

        # Size in shares
        shares = args.stake_usd / no_limit_price
        print(f"\n[submit] {r.city} +1 bucket ({r.plus1_bucket.bucket_title}):")
        print(f"  YES={r.yes_price_estimate:.4f}  NO={no_ask:.4f}  "
              f"stake=${args.stake_usd} → {shares:.1f} shares @ {no_limit_price:.4f}")
        print(f"  est. edge: +{r.est_edge_pp():.1f}pp  "
              f"est. PnL: ${shares * (0.97 - no_limit_price - 0.05 * no_limit_price * (1-no_limit_price)):+.2f}")

        result = place_limit_buy(
            client,
            token_id=r.plus1_bucket.no_token_id,
            price=no_limit_price,
            size=shares,
            post_only=True,
            time_in_force="GTC",
            dry_run=args.dry_run,
        )
        if result.success:
            print(f"  ✓ order_id={result.order_id} status={result.status} "
                  f"matched={result.size_matched}")
        else:
            print(f"  ✗ FAILED: {result.error}")

        ledger_rows.append({
            "ts": datetime.now(UTC).isoformat(),
            "market_date": str(target),
            "city": r.city,
            "plus1_bucket": r.plus1_bucket.bucket_title,
            "slug": r.plus1_bucket.slug,
            "no_token_id": r.plus1_bucket.no_token_id,
            "consensus_spread": r.consensus_spread,
            "nbs_pred": r.nbs_pred,
            "no_limit_price": no_limit_price,
            "shares": shares,
            "stake_usd": args.stake_usd,
            "success": result.success,
            "order_id": result.order_id,
            "status": result.status,
            "size_matched": result.size_matched,
            "error": result.error,
            "dry_run": args.dry_run,
        })

    # Write ledger
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a") as f:
        for row in ledger_rows:
            f.write(json.dumps(row) + "\n")
    print(f"\n[submit] ledger appended to {LEDGER_PATH}")
    return 0


def cmd_cancel_all(args: argparse.Namespace) -> int:
    from lib.polymarket.client import load_client_from_env
    from lib.polymarket.orders import cancel_all
    client = load_client_from_env()
    print(f"[cancel-all] signer={client.address}")
    if not args.yes:
        sys.stderr.write("Pass --yes to confirm cancellation of all open orders.\n")
        return 2
    resp = cancel_all(client)
    print("[cancel-all] response:", json.dumps(resp, indent=2, default=str))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from lib.polymarket.client import load_client_from_env
    from lib.polymarket.orders import list_open_orders
    client = load_client_from_env()
    print(f"Signer: {client.address}")
    print()
    orders = list_open_orders(client)
    print(f"Open orders: {len(orders)}")
    for o in orders[:50]:
        print(f"  {o.get('id', '?')[:20]}  {o.get('market', '?')[:30]:<30}  "
              f"{o.get('side')} {o.get('original_size', 0)} @ {o.get('price')} "
              f"({o.get('size_matched', 0)} matched)")
    return 0


# ---- helpers ----

def _parse_date(s: str | None) -> date:
    if not s:
        return datetime.now(UTC).date()
    return date.fromisoformat(s)


def _print_status(s) -> None:
    print(f"Signer address: {s.address}")
    print(f"USDC allowances:  {s.usdc_allowances_ok}")
    print(f"CTF allowances:   {s.ctf_allowances_ok}")
    print(f"API creds present: {s.api_creds_present}")
    all_ok = (all(s.usdc_allowances_ok.values())
              and all(s.ctf_allowances_ok.values())
              and s.api_creds_present)
    print(f"\nStatus: {'READY' if all_ok else 'NOT READY'}")


def _print_recs(recs, *, with_prices: bool = False) -> None:
    if not recs:
        print("No recommendations for today. Either no cities pass consensus filter, "
              "or +1 offset buckets are priced outside [yes_min, yes_max].")
        return
    if with_prices:
        print(f"{'city':<16} {'cs':>5}  {'NBS/GFS/HRRR':>14}  {'fav':>8}  "
              f"{'+1 bucket':>11}  {'yes_mid':>8} {'no_ask':>7} {'edge':>7}")
    else:
        print(f"{'city':<16} {'cs':>5}  {'NBS/GFS/HRRR':>14}  {'fav':>8}  {'+1 bucket':>11}")
    print("-" * 100)
    for r in recs:
        hrrr_s = f"{r.hrrr_pred:.0f}" if r.hrrr_pred is not None else "—"
        fcasts = f"{r.nbs_pred:.0f}/{r.gfs_pred:.0f}/{hrrr_s}"
        if with_prices:
            yes_s = f"{r.yes_price_estimate:.3f}" if r.yes_price_estimate else "—"
            no_s = f"{r.no_ask_estimate:.3f}" if r.no_ask_estimate else "—"
            edge_s = f"{r.est_edge_pp():+.1f}pp" if r.est_edge_pp() is not None else "—"
            print(f"{r.city:<16} {r.consensus_spread:>4.1f}  {fcasts:>14}  "
                  f"{r.nbs_fav_bucket_title:>8}  {r.plus1_bucket.bucket_title:>11}  "
                  f"{yes_s:>8} {no_s:>7} {edge_s:>7}")
        else:
            print(f"{r.city:<16} {r.consensus_spread:>4.1f}  {fcasts:>14}  "
                  f"{r.nbs_fav_bucket_title:>8}  {r.plus1_bucket.bucket_title:>11}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cfp",
        description="Consensus-Fade +1 Offset — weather market fade strategy",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="One-time wallet setup")
    p_setup.add_argument("--check", action="store_true", help="Read-only status check (no tx)")
    p_setup.add_argument("--rederive", action="store_true", help="Re-derive API creds even if present")
    p_setup.set_defaults(func=cmd_setup)

    p_rec = sub.add_parser("recommend", help="Show today's recommendations")
    p_rec.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    p_rec.add_argument("--consensus-max", type=float, default=3.0)
    p_rec.add_argument("--yes-min", type=float, default=0.005)
    p_rec.add_argument("--yes-max", type=float, default=0.5)
    p_rec.add_argument("--no-live", action="store_true", help="Skip live-price fetch")
    p_rec.set_defaults(func=cmd_recommend)

    p_sub = sub.add_parser("submit", help="Place BUY-NO limit orders")
    p_sub.add_argument("--date", default=None)
    p_sub.add_argument("--stake-usd", type=float, default=20.0)
    p_sub.add_argument("--consensus-max", type=float, default=3.0)
    p_sub.add_argument("--yes-min", type=float, default=0.005)
    p_sub.add_argument("--yes-max", type=float, default=0.5)
    p_sub.add_argument("--dry-run", action="store_true")
    p_sub.set_defaults(func=cmd_submit)

    p_cancel = sub.add_parser("cancel-all", help="Cancel all open orders")
    p_cancel.add_argument("--yes", action="store_true", help="Confirm cancellation")
    p_cancel.set_defaults(func=cmd_cancel_all)

    p_status = sub.add_parser("status", help="Account + open orders")
    p_status.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as e:
        # Friendly errors (missing env, etc.) — no traceback needed
        sys.stderr.write(f"\nError: {e}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())

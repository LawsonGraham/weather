"""Live "what should I trade right now" recommender — direct API queries.

Bypasses the local processed parquet entirely. At runtime:
    1. Hits Gamma API for today's NYC daily-temperature ladder
    2. Hits IEM METAR API for the latest LGA observation
    3. Identifies the favorite + the +2 bucket
    4. Outputs a Strategy D V1 trade recommendation

Use this when you want a recommendation that's actually fresh, not based
on stale local snapshots. Suitable for the 16 EDT entry hour, which is the
recommended deployment time per exp25.

Usage:
    uv run python scripts/polymarket_weather/live_now.py
        [--bankroll 10000] [--kelly 0.02] [--date YYYY-MM-DD]
        [--check-skip]    # apply V5 skip rules from METAR (12 EDT data)

The output includes the Polymarket condition_id and CLOB token IDs so you
can copy-paste into the trading UI.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request

GAMMA_BASE = "https://gamma-api.polymarket.com"
IEM_METAR_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

FEE = 0.02
SPREAD_PADDING = 0.01


def fetch_json(url: str, timeout: float = 15.0) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "weather-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "weather-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_nyc_ladder(target_date: dt.date) -> list[dict]:
    """Hit Gamma API for today's NYC daily-temp markets.

    Filters by `103040` (Daily Temperature tag) and matches markets whose
    question text mentions the target date. Returns one dict per range strike
    with slug, strike, lo_f, hi_f, p_yes (last_trade_price), and clob_token_ids.
    """
    # Gamma supports tag filtering. Use the daily-temperature tag.
    # Pull all open markets in the tag and filter by the target_date question.
    base = f"{GAMMA_BASE}/markets"
    params = {
        "tag_id": "103040",
        "active": "true",
        "closed": "false",
        "limit": "200",
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"
    raw = fetch_json(url)
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected gamma response shape: {type(raw).__name__}")

    out: list[dict] = []
    target_iso = target_date.strftime("%B %-d") if sys.platform != "win32" else target_date.strftime("%B %#d")
    target_iso_zero = target_date.strftime("%B %d")
    for m in raw:
        question = m.get("question", "") or ""
        if not ("nyc" in question.lower() or "new york city" in question.lower()):
            continue
        if target_iso not in question and target_iso_zero not in question:
            continue
        title = m.get("groupItemTitle") or ""
        # Skip "or higher" / "or below" tail strikes — Strategy D uses range strikes only
        if "or" in title.lower():
            continue
        match = re.search(r"(-?\d+)-(-?\d+)", title)
        if not match:
            continue
        lo, hi = int(match.group(1)), int(match.group(2))
        outcome_prices = m.get("outcomePrices") or "[]"
        if isinstance(outcome_prices, str):
            try:
                op = json.loads(outcome_prices)
            except Exception:
                op = []
        else:
            op = outcome_prices
        try:
            p_yes = float(op[0]) if op else None
        except Exception:
            p_yes = None
        clob = m.get("clobTokenIds") or "[]"
        if isinstance(clob, str):
            try:
                clob_ids = json.loads(clob)
            except Exception:
                clob_ids = []
        else:
            clob_ids = clob
        out.append({
            "slug": m.get("slug"),
            "condition_id": m.get("conditionId"),
            "strike": title,
            "lo_f": lo,
            "hi_f": hi,
            "p_yes": p_yes,
            "best_ask": m.get("bestAsk"),
            "best_bid": m.get("bestBid"),
            "yes_token_id": clob_ids[0] if len(clob_ids) >= 1 else None,
            "no_token_id":  clob_ids[1] if len(clob_ids) >= 2 else None,
        })
    out.sort(key=lambda r: (r["lo_f"], r["hi_f"]))
    return out


def fetch_lga_metar_at(target_date: dt.date) -> dict | None:
    """Pull IEM ASOS hourly observations for KLGA on target_date and return
    the row closest to 16:00 UTC (12 EDT).
    """
    params = {
        "station": "LGA",
        "data": "tmpf,relh,skyc1",
        "year1": target_date.year, "month1": target_date.month, "day1": target_date.day,
        "year2": target_date.year, "month2": target_date.month, "day2": target_date.day,
        "tz": "Etc/UTC", "format": "onlycomma",
        "latlon": "no", "missing": "M", "trace": "T", "direct": "yes", "report_type": "3",
    }
    url = f"{IEM_METAR_URL}?{urllib.parse.urlencode(params)}"
    text = fetch_text(url)
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return None
    header = lines[0].split(",")
    rows = [dict(zip(header, line.split(","))) for line in lines[1:]]

    # Find row closest to 16:00 UTC = 12:00 EDT
    target_utc = dt.datetime(target_date.year, target_date.month, target_date.day, 16, 0)
    best = None
    best_dt = None
    for r in rows:
        try:
            ts = dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        delta = abs((ts - target_utc).total_seconds())
        if best_dt is None or delta < best_dt:
            best_dt = delta
            best = r
    if best is None:
        return None
    try:
        tmpf = float(best.get("tmpf", "M"))
    except Exception:
        tmpf = None
    try:
        relh = float(best.get("relh", "M"))
    except Exception:
        relh = None
    return {"valid_utc": best.get("valid"), "tmpf": tmpf, "relh": relh, "skyc1": best.get("skyc1")}


def recommend(ladder: list[dict], metar: dict | None, bankroll: float, kelly: float, check_skip: bool) -> str:
    if not ladder:
        return "(no NYC daily-temp range strikes found in Gamma API for target date)"
    # Find favorite (highest p_yes)
    fav = max(ladder, key=lambda r: r["p_yes"] or 0)
    target_lo = fav["lo_f"] + 2
    target = next((r for r in ladder if r["lo_f"] == target_lo), None)
    if target is None or target.get("p_yes") is None:
        return f"no +2 bucket (lo={target_lo}) in ladder"
    if target["p_yes"] < 0.02:
        return f"+2 bucket priced too low (${target['p_yes']:.4f} < $0.02), skip"

    # V5 skip check at 12 EDT
    skip_reason = None
    if check_skip and metar:
        rise_needed = fav["lo_f"] - (metar["tmpf"] or 0)
        if metar["relh"] is not None and metar["relh"] < 40:
            skip_reason = f"DRY (relh={metar['relh']:.0f}% < 40%) — V5 skip rule"
        elif rise_needed >= 6:
            skip_reason = f"high forecast rise (rise_needed={rise_needed:.0f}°F ≥ 6°F) — V5 skip rule"

    out: list[str] = []
    out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    out.append(f"  Strategy D LIVE — NYC Polymarket Daily-Temp")
    out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    out.append(f"  Ladder snapshot ({len(ladder)} range strikes):")
    for r in ladder:
        marker = "★" if r is fav else ("◆" if r is target else " ")
        bid = f"bid {r['best_bid']}" if r.get("best_bid") not in (None, "") else ""
        ask = f"ask {r['best_ask']}" if r.get("best_ask") not in (None, "") else ""
        out.append(f"    {marker} {r['strike']:<10}  ${r['p_yes']:.3f}  {bid} {ask}")
    out.append(f"")
    if metar:
        out.append(f"  METAR LGA at ~12 EDT: tmpf={metar['tmpf']}, relh={metar.get('relh')}, sky={metar.get('skyc1')}")
        out.append(f"  rise_needed (fav_lo - tmpf): {fav['lo_f'] - (metar['tmpf'] or 0):+.1f}°F")
    out.append(f"")
    if skip_reason:
        out.append(f"  STATUS: SKIP ({skip_reason})")
        return "\n".join(out)

    # Real ask is best_ask if present, else mid + spread
    real_ask = float(target["best_ask"]) if target.get("best_ask") not in (None, "") else target["p_yes"] + SPREAD_PADDING
    cost = real_ask * (1 + FEE)
    stake = bankroll * kelly
    shares = stake / cost
    profit_if_hit = stake * (1.0 / cost - 1.0)

    out.append(f"  ★ Favorite:    {fav['strike']}  @ ${fav['p_yes']:.3f}")
    out.append(f"  ◆ Target +2:   {target['strike']}  @ ${target['p_yes']:.3f}  (real ask ${real_ask:.4f})")
    out.append(f"")
    out.append(f"  Entry cost (with {FEE*100:.0f}% fee): ${cost:.4f}")
    out.append(f"  Stake ({kelly*100:.1f}% Kelly): ${stake:,.2f}")
    out.append(f"  Shares: {shares:.2f}")
    out.append(f"  Payoff if hit: ${stake * (1.0 / cost):,.2f}  → profit ${profit_if_hit:+,.2f}")
    out.append(f"  Loss if miss:  -${stake:,.2f}")
    out.append(f"")
    out.append(f"  ACTION: BUY {shares:.1f} shares YES on:")
    out.append(f"    slug:           {target['slug']}")
    out.append(f"    yes_token_id:   {target['yes_token_id']}")
    out.append(f"    condition_id:   {target['condition_id']}")
    out.append(f"    limit price ≤ ${cost:.4f}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None)
    ap.add_argument("--bankroll", type=float, default=10_000.0)
    ap.add_argument("--kelly", type=float, default=0.02)
    ap.add_argument("--check-skip", action="store_true", help="apply V5 skip rules using METAR")
    args = ap.parse_args()
    target_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    print(f"\n== Strategy D LIVE recommender — {target_date} ==", file=sys.stderr)
    print(f"   bankroll ${args.bankroll:,.0f}, Kelly {args.kelly*100:.1f}%", file=sys.stderr)
    print(file=sys.stderr)

    print("→ fetching NYC daily-temp ladder from Gamma API ...", file=sys.stderr)
    ladder = fetch_nyc_ladder(target_date)
    print(f"  got {len(ladder)} range strikes", file=sys.stderr)

    metar = None
    if args.check_skip:
        print("→ fetching IEM METAR for KLGA at 12 EDT ...", file=sys.stderr)
        try:
            metar = fetch_lga_metar_at(target_date)
        except Exception as e:
            print(f"  metar fetch failed: {e}", file=sys.stderr)

    print(recommend(ladder, metar, args.bankroll, args.kelly, args.check_skip))
    print()


if __name__ == "__main__":
    main()

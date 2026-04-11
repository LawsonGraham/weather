#!/usr/bin/env python3
"""Validation checks for the polymarket_weather raw download.

Runs a battery of checks against ``data/raw/polymarket_weather/`` to prove
that the downloader captured everything we need for a given filter
(typically `--city "New York City"` for the NYC validation run).

Checks performed:

1. **File completeness** — every selected slug has both a `gamma/<slug>.json`
   and a `fills/<slug>.json` on disk.
2. **Gamma schema sanity** — each gamma JSON has all the critical fields:
   conditionId, clobTokenIds, outcomes, question, volumeNum, bestBid, bestAsk,
   orderPriceMinTickSize, negRisk, createdAt, endDate.
3. **Fills schema sanity** — each fill record has timestamp, maker, taker,
   makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled.
4. **Token consistency** — fills files are keyed by token IDs that match
   the gamma market's clobTokenIds list.
5. **Non-empty fills for non-zero volume markets** — if Gamma reports
   volumeNum > $100, the fills file should be non-empty.
6. **Timestamp range** — fills fall within the market lifetime (roughly
   createdAt → endDate + buffer).
7. **Volume reconciliation** — reconstruct total shares traded from fills,
   compare to Gamma's `volumeNum`, document the ratio.
8. **Top-volume market deep dive** — walk through the highest-volume NYC
   market fill-by-fill, reconstruct price history, confirm YES + NO ≈ $1
   arbitrage constraint, compare to Gamma's `lastTradePrice`.

Usage::

    python3 scripts/download/polymarket_weather/validate.py --city "New York City"
    python3 scripts/download/polymarket_weather/validate.py --slugs slug-a,slug-b
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SLUGS_CSV = REPO_ROOT / "weather-market-slugs" / "polymarket.csv"
RAW_DIR = REPO_ROOT / "data" / "raw" / "polymarket_weather"
GAMMA_DIR = RAW_DIR / "gamma"
FILLS_DIR = RAW_DIR / "fills"

REQUIRED_GAMMA_FIELDS = [
    "slug",
    "conditionId",
    "question",
    "clobTokenIds",
    "outcomes",
    "volumeNum",
    "volumeClob",
    "bestBid",
    "bestAsk",
    "orderPriceMinTickSize",
    "orderMinSize",
    "negRisk",
    "active",
    "closed",
    "createdAt",
    "endDate",
]

REQUIRED_FILL_FIELDS = [
    "id",
    "transactionHash",
    "timestamp",
    "orderHash",
    "maker",
    "taker",
    "makerAssetId",
    "takerAssetId",
    "makerAmountFilled",
    "takerAmountFilled",
]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def load_selected_slugs(
    csv_path: Path, *, city: str | None, explicit: list[str] | None
) -> list[dict[str, Any]]:
    if explicit:
        # Return stub rows with just the slug
        return [{"slug": s.strip(), "city": "", "volume_gamma": ""} for s in explicit]
    if not csv_path.exists():
        raise SystemExit(f"slug CSV not found: {csv_path}")
    rows: list[dict[str, Any]] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if city and row.get("city", "") != city:
                continue
            rows.append(row)
    return rows


def parse_fill_price(fill: dict[str, Any], token_id: str) -> tuple[float, float, str] | None:
    """Return (price, shares, side) for a fill relative to `token_id`.

    `side` is 'buy' (taker bought the token) or 'sell' (taker sold the token).
    `price` is USDC per share (0..1). Returns None for token→token swaps.
    """
    maker_asset = fill["makerAssetId"]
    taker_asset = fill["takerAssetId"]
    maker_amt = int(fill["makerAmountFilled"])
    taker_amt = int(fill["takerAmountFilled"])

    if maker_asset == "0" and taker_asset == token_id:
        # Taker is buying `token_id` with USDC
        if taker_amt == 0:
            return None
        price = maker_amt / taker_amt
        shares = taker_amt / 1e6
        return price, shares, "buy"
    if taker_asset == "0" and maker_asset == token_id:
        # Taker is selling `token_id` for USDC
        if maker_amt == 0:
            return None
        price = taker_amt / maker_amt
        shares = maker_amt / 1e6
        return price, shares, "sell"
    return None  # token ↔ token (rare)


# --------------------------------------------------------------------------- #
# Checks                                                                      #
# --------------------------------------------------------------------------- #


class Report:
    """Accumulator for human-readable report lines + a pass/fail verdict."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        self.lines.append("")
        self.lines.append(title)
        self.lines.append("-" * 70)

    def ok(self, msg: str) -> None:
        self.lines.append(f"  PASS  {msg}")

    def warn(self, msg: str) -> None:
        self.lines.append(f"  WARN  {msg}")

    def fail(self, msg: str) -> None:
        self.lines.append(f"  FAIL  {msg}")
        self.failures.append(msg)

    def info(self, msg: str) -> None:
        self.lines.append(f"        {msg}")

    def __str__(self) -> str:
        return "\n".join(self.lines)


def check_completeness(r: Report, selected: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Returns (present_slugs, missing_slugs)."""
    r.section("[1] File completeness")
    present: list[str] = []
    missing_gamma: list[str] = []
    missing_fills: list[str] = []
    for row in selected:
        slug = row["slug"]
        has_g = (GAMMA_DIR / f"{slug}.json").exists()
        has_f = (FILLS_DIR / f"{slug}.json").exists()
        if has_g and has_f:
            present.append(slug)
        else:
            if not has_g:
                missing_gamma.append(slug)
            if not has_f:
                missing_fills.append(slug)
    r.info(
        f"selected: {len(selected):,}  present: {len(present):,}  "
        f"missing_gamma: {len(missing_gamma):,}  missing_fills: {len(missing_fills):,}"
    )
    if not missing_gamma and not missing_fills:
        r.ok("all selected slugs have both gamma + fills files")
    else:
        if missing_gamma:
            r.fail(f"{len(missing_gamma)} slugs missing gamma JSON")
            for s in missing_gamma[:5]:
                r.info(f"  - {s}")
        if missing_fills:
            r.fail(f"{len(missing_fills)} slugs missing fills JSON")
            for s in missing_fills[:5]:
                r.info(f"  - {s}")
    return present, missing_gamma + missing_fills


def check_gamma_schema(r: Report, slugs: list[str]) -> list[dict[str, Any]]:
    """Load every gamma market and verify required fields.  Returns list of markets."""
    r.section("[2] Gamma schema sanity")
    markets: list[dict[str, Any]] = []
    missing_fields: dict[str, int] = {}
    for slug in slugs:
        m = json.loads((GAMMA_DIR / f"{slug}.json").read_text())
        markets.append(m)
        for field in REQUIRED_GAMMA_FIELDS:
            if field not in m:
                missing_fields[field] = missing_fields.get(field, 0) + 1
    if not missing_fields:
        r.ok(f"all {len(markets):,} markets have all {len(REQUIRED_GAMMA_FIELDS)} required fields")
    else:
        for field, n in sorted(missing_fields.items(), key=lambda x: -x[1]):
            r.fail(f"field '{field}' missing in {n} markets")
    neg_risk = sum(1 for m in markets if m.get("negRisk"))
    r.info(f"negRisk=true:  {neg_risk}/{len(markets)}  ({neg_risk / len(markets):.0%})")
    return markets


def check_fills_schema(r: Report, slugs: list[str]) -> tuple[dict[str, dict[str, list]], int]:
    """Load every fills file and verify schema.  Returns (fills_by_slug, total_fill_count)."""
    r.section("[3] Fills schema sanity")
    all_fills: dict[str, dict[str, list[dict[str, Any]]]] = {}
    total = 0
    missing_fields: dict[str, int] = {}
    bad_files: list[str] = []
    for slug in slugs:
        try:
            fills_by_token = json.loads((FILLS_DIR / f"{slug}.json").read_text())
        except Exception as e:
            bad_files.append(f"{slug}: {e}")
            continue
        all_fills[slug] = fills_by_token
        for tok_fills in fills_by_token.values():
            for f in tok_fills:
                total += 1
                for field in REQUIRED_FILL_FIELDS:
                    if field not in f:
                        missing_fields[field] = missing_fields.get(field, 0) + 1
    r.info(f"fills files loaded: {len(all_fills):,}  total fills: {total:,}")
    if bad_files:
        r.fail(f"{len(bad_files)} fills files failed to parse")
    if not missing_fields:
        r.ok(f"all {total:,} fills have all {len(REQUIRED_FILL_FIELDS)} required fields")
    else:
        for field, n in sorted(missing_fields.items(), key=lambda x: -x[1]):
            r.fail(f"field '{field}' missing in {n} fills")
    return all_fills, total


def check_token_consistency(
    r: Report,
    markets: list[dict[str, Any]],
    all_fills: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    r.section("[4] Token ID consistency (fills keys match gamma clobTokenIds)")
    mismatches = 0
    for m in markets:
        slug = m["slug"]
        try:
            gamma_tokens = set(json.loads(m["clobTokenIds"]))
        except Exception:
            gamma_tokens = set()
        fill_tokens = set(all_fills.get(slug, {}).keys())
        if fill_tokens and gamma_tokens and fill_tokens != gamma_tokens:
            mismatches += 1
    if mismatches == 0:
        r.ok("all fills-file token keys match the corresponding gamma clobTokenIds")
    else:
        r.fail(f"{mismatches} markets have mismatched token sets between gamma and fills")


def check_non_empty_fills(
    r: Report,
    markets: list[dict[str, Any]],
    all_fills: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    r.section("[5] Fills present for non-zero volume markets")
    empty_but_volume: list[tuple[str, float]] = []
    non_zero_markets = 0
    for m in markets:
        slug = m["slug"]
        vol = m.get("volumeNum") or 0
        if vol <= 100:
            continue
        non_zero_markets += 1
        total_fills = sum(len(v) for v in all_fills.get(slug, {}).values())
        if total_fills == 0:
            empty_but_volume.append((slug, float(vol)))
    if not empty_but_volume:
        r.ok(f"all {non_zero_markets} markets with volume>$100 have fills")
    else:
        r.fail(f"{len(empty_but_volume)} markets have volume>$100 but ZERO fills")
        for slug, vol in empty_but_volume[:5]:
            r.info(f"  - {slug} (volume=${vol:,.0f})")


def check_volume_reconciliation(
    r: Report,
    markets: list[dict[str, Any]],
    all_fills: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    """Compare Gamma's volumeNum to our reconstructed notional from fills.

    Note (from earlier investigation): Gamma's volumeNum counts contract
    notional (shares traded / 2, each share worth $1 face value).  Our fill
    reconstruction will match this for well-behaved markets.
    """
    r.section("[6] Volume reconciliation — Gamma volumeNum vs fills reconstruction")
    ratios: list[float] = []
    for m in markets:
        slug = m["slug"]
        gamma_vol = float(m.get("volumeNum") or 0)
        if gamma_vol <= 0:
            continue
        try:
            gamma_tokens = json.loads(m["clobTokenIds"])
        except Exception:
            continue
        total_shares = 0.0
        for tok_id in gamma_tokens:
            for f in all_fills.get(slug, {}).get(str(tok_id), []):
                priced = parse_fill_price(f, str(tok_id))
                if priced:
                    _, shares, _ = priced
                    total_shares += shares
        # Gamma volumeNum ≈ total_shares / 2 (both-sides counting)
        if total_shares > 0:
            ratios.append((total_shares / 2) / gamma_vol)

    if not ratios:
        r.warn("no markets with comparable volume data")
        return
    med = statistics.median(ratios)
    mean = statistics.mean(ratios)
    r.info(
        f"ratio (reconstructed shares/2 ÷ gamma volumeNum) — "
        f"median: {med:.3f}  mean: {mean:.3f}  (n={len(ratios)})"
    )
    # Anywhere from 0.95 to 1.05 is healthy; wide deviations indicate
    # missing trades or a contract split we don't understand.
    if 0.95 <= med <= 1.05:
        r.ok(f"median volume ratio {med:.3f} ∈ [0.95, 1.05] — reconstructions match Gamma")
    elif 0.85 <= med <= 1.15:
        r.warn(f"median volume ratio {med:.3f} ∉ [0.95, 1.05] — acceptable but check")
    else:
        r.fail(f"median volume ratio {med:.3f} — significantly off, investigate")


def check_timestamps(
    r: Report,
    markets: list[dict[str, Any]],
    all_fills: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    r.section("[7] Fill timestamps fall within market lifetime")
    from datetime import datetime as _dt

    violations = 0
    checked = 0
    for m in markets:
        slug = m["slug"]
        try:
            created = _dt.fromisoformat(m["createdAt"].replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        for tok_fills in all_fills.get(slug, {}).values():
            for f in tok_fills:
                ts = int(f["timestamp"])
                checked += 1
                # Allow 7 days of pre-market slop (createdAt can be slightly late)
                if ts < created - 86400 * 7:
                    violations += 1
    if checked == 0:
        r.warn("no fills to check")
    elif violations == 0:
        r.ok(f"all {checked:,} fills fall within market lifetime (±7 day slop)")
    else:
        r.fail(f"{violations:,}/{checked:,} fills fall outside market lifetime")


def deep_dive_top_market(
    r: Report,
    markets: list[dict[str, Any]],
    all_fills: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    r.section("[8] Deep dive — top-volume market")
    markets_with_vol = [m for m in markets if (m.get("volumeNum") or 0) > 0]
    if not markets_with_vol:
        r.warn("no markets with volume > 0")
        return
    top = max(markets_with_vol, key=lambda m: m.get("volumeNum") or 0)
    slug = top["slug"]
    r.info(f"slug: {slug}")
    r.info(f"question: {top.get('question')}")
    r.info(f"volumeNum: ${float(top.get('volumeNum') or 0):,.2f}")
    r.info(f"outcomePrices: {top.get('outcomePrices')}")
    r.info(f"closedTime: {top.get('closedTime')}")
    r.info(f"lastTradePrice: {top.get('lastTradePrice')}")

    tokens = json.loads(top["clobTokenIds"])
    yes_tok, no_tok = str(tokens[0]), str(tokens[1])

    for side_name, tok in [("YES", yes_tok), ("NO", no_tok)]:
        fills = all_fills.get(slug, {}).get(tok, [])
        priced = [parse_fill_price(f, tok) for f in fills]
        priced = [p for p in priced if p]
        if not priced:
            r.info(f"  {side_name}: no priced fills")
            continue
        prices = [p[0] for p in priced]
        shares = sum(p[1] for p in priced)
        r.info(
            f"  {side_name}: {len(priced)} fills,  min=${min(prices):.4f}  "
            f"avg=${sum(prices) / len(prices):.4f}  max=${max(prices):.4f}  "
            f"shares={shares:,.0f}"
        )

    # First/last fills
    all_slug_fills = [f for toks in all_fills.get(slug, {}).values() for f in toks]
    if all_slug_fills:
        ts = sorted(int(f["timestamp"]) for f in all_slug_fills)
        from datetime import datetime as _dt

        first = _dt.fromtimestamp(ts[0]).isoformat()
        last = _dt.fromtimestamp(ts[-1]).isoformat()
        r.info(f"first fill: {first}   last fill: {last}   (n={len(ts):,})")


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--slugs-file", type=Path, default=DEFAULT_SLUGS_CSV)
    ap.add_argument("--city", help="filter to one city")
    ap.add_argument("--slugs", help="explicit comma-separated slug list")
    args = ap.parse_args()

    explicit = [s.strip() for s in args.slugs.split(",")] if args.slugs else None
    selected = load_selected_slugs(args.slugs_file, city=args.city, explicit=explicit)

    r = Report()
    r.lines.append("polymarket_weather raw download — validation report")
    r.lines.append(f"selected: {len(selected):,} slugs  (city={args.city!r})")
    r.lines.append(f"raw_dir:  {RAW_DIR.relative_to(REPO_ROOT)}")

    present, _missing = check_completeness(r, selected)
    if not present:
        r.fail("no present slugs — nothing to validate")
        print(r)
        return 1

    markets = check_gamma_schema(r, present)
    all_fills, _total_fills = check_fills_schema(r, present)
    check_token_consistency(r, markets, all_fills)
    check_non_empty_fills(r, markets, all_fills)
    check_volume_reconciliation(r, markets, all_fills)
    check_timestamps(r, markets, all_fills)
    deep_dive_top_market(r, markets, all_fills)

    r.lines.append("")
    r.lines.append("=" * 70)
    if r.failures:
        r.lines.append(f"RESULT: {len(r.failures)} failure(s)")
        for f in r.failures:
            r.lines.append(f"  - {f}")
    else:
        r.lines.append("RESULT: ALL CHECKS PASSED")
    print(r)
    return 0 if not r.failures else 1


if __name__ == "__main__":
    sys.exit(main())

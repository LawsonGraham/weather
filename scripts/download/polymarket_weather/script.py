#!/usr/bin/env python3
"""Download Polymarket weather market raw data (Gamma metadata + subgraph fills).

Reads the slug catalog at ``weather-market-slugs/polymarket.csv`` and, for
each selected slug, pulls:

1. Full Gamma API market JSON  → ``data/raw/polymarket_weather/gamma/<slug>.json``
2. Goldsky subgraph OrderFilled events (per CLOB token, paginated)
                                 → ``data/raw/polymarket_weather/fills/<slug>.json``

Usage::

    python3 scripts/download/polymarket_weather/script.py --city "New York City" --limit 5
    python3 scripts/download/polymarket_weather/script.py --city "New York City"
    python3 scripts/download/polymarket_weather/script.py --slugs slug-a,slug-b
    python3 scripts/download/polymarket_weather/script.py --force

See scripts/download/polymarket_weather/README.md for flags and contract.

This script is self-contained — no shared utility module.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "polymarket_weather"
DESCRIPTION = (
    "Polymarket weather markets — Gamma API market metadata + Goldsky "
    "orderbook subgraph OrderFilled events, keyed by slug."
)
SCRIPT_VERSION = 1

GAMMA_BASE = "https://gamma-api.polymarket.com"
SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/orderbook-subgraph/0.0.1/gn"
)

REQUEST_TIMEOUT_S = 30
SUBGRAPH_PAGE_SIZE = 1000
SUBGRAPH_DELAY_S = 0.1
GAMMA_DELAY_S = 0.1

# --- paths ----------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SLUGS_CSV = REPO_ROOT / "weather-market-slugs" / "polymarket.csv"
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
GAMMA_DIR = RAW_DIR / "gamma"
FILLS_DIR = RAW_DIR / "fills"
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
LOG_PATH = RAW_DIR / "download.log"
SCRIPT_REL = f"scripts/download/{SOURCE_NAME}/script.py"
TARGET_REL = f"data/raw/{SOURCE_NAME}"

log = logging.getLogger(SOURCE_NAME)


# --------------------------------------------------------------------------- #
# Logging + manifest (inlined)                                                #
# --------------------------------------------------------------------------- #

def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    log.propagate = False

    class _Fmt(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            return datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    fmt = _Fmt("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def die(msg: str) -> None:
    log.error(msg)
    raise SystemExit(1)


def read_manifest() -> dict[str, Any] | None:
    if not MANIFEST_PATH.exists():
        return None
    return json.loads(MANIFEST_PATH.read_text())


def write_manifest(doc: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(doc, indent=2, default=str) + "\n")


def initial_manifest() -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "source_name": SOURCE_NAME,
        "description": DESCRIPTION,
        "upstream": {
            "gamma_api": GAMMA_BASE,
            "subgraph":  SUBGRAPH_URL,
        },
        "script": {"path": SCRIPT_REL, "version": SCRIPT_VERSION},
        "download": {
            "started_at": utc_now(),
            "completed_at": None,
            "status": "in_progress",
            "slugs_attempted": 0,
            "slugs_succeeded": 0,
            "slugs_skipped_cached": 0,
            "slugs_failed": 0,
            "total_fills": 0,
        },
        "target": {"raw_dir": TARGET_REL},
        "notes": "",
    }


# --------------------------------------------------------------------------- #
# HTTP                                                                        #
# --------------------------------------------------------------------------- #

def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "weather-research/1.0"})
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                wait = 2 ** attempt
                log.warning("HTTP 429 on %s — sleeping %ds", url[:80], wait)
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < 5:
                wait = 2 ** attempt
                log.warning("network error on %s (%s) — retry in %ds", url[:80], e, wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"exhausted retries on {url}")


def _http_post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "weather-research/1.0"},
    )
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                wait = 2 ** attempt
                log.warning("subgraph 429 — sleeping %ds", wait)
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < 5:
                wait = 2 ** attempt
                log.warning("subgraph network error (%s) — retry in %ds", e, wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"exhausted retries on {url}")


# --------------------------------------------------------------------------- #
# Gamma                                                                       #
# --------------------------------------------------------------------------- #

def fetch_gamma_market(slug: str) -> dict[str, Any] | None:
    """Look up a single market by slug. Returns the full Gamma object or None."""
    # Try closed=true first (covers resolved markets), fall back to closed=false.
    for closed in ("true", "false"):
        url = f"{GAMMA_BASE}/markets?{urllib.parse.urlencode({'slug': slug, 'closed': closed})}"
        body = _http_get(url)
        data = json.loads(body)
        if isinstance(data, list) and data:
            return data[0]
    return None


# --------------------------------------------------------------------------- #
# Subgraph                                                                    #
# --------------------------------------------------------------------------- #

_SUBGRAPH_QUERY = """
query Fills($tokenId: String!, $first: Int!, $skip: Int!) {
  orderFilledEvents(
    where: { or: [ { makerAssetId: $tokenId }, { takerAssetId: $tokenId } ] }
    orderBy: timestamp
    orderDirection: asc
    first: $first
    skip: $skip
  ) {
    id
    transactionHash
    timestamp
    orderHash
    maker
    taker
    makerAssetId
    takerAssetId
    makerAmountFilled
    takerAmountFilled
    fee
  }
}
"""


def fetch_subgraph_fills(token_id: str) -> list[dict[str, Any]]:
    """Paginate all OrderFilled events for a single token ID."""
    out: list[dict[str, Any]] = []
    skip = 0
    while True:
        resp = _http_post_json(
            SUBGRAPH_URL,
            {
                "query": _SUBGRAPH_QUERY,
                "variables": {"tokenId": token_id, "first": SUBGRAPH_PAGE_SIZE, "skip": skip},
            },
        )
        if "errors" in resp:
            raise RuntimeError(f"subgraph errors: {resp['errors']}")
        batch = resp.get("data", {}).get("orderFilledEvents", []) or []
        if not batch:
            break
        out.extend(batch)
        if len(batch) < SUBGRAPH_PAGE_SIZE:
            break
        skip += SUBGRAPH_PAGE_SIZE
        time.sleep(SUBGRAPH_DELAY_S)
    return out


# --------------------------------------------------------------------------- #
# Per-slug worker                                                             #
# --------------------------------------------------------------------------- #

def process_slug(slug: str, *, force: bool) -> tuple[str, int]:
    """Download one market's Gamma + fills. Returns (status, fill_count).

    Status is one of: 'ok', 'skipped', 'no_gamma', 'no_tokens'.
    """
    gamma_path = GAMMA_DIR / f"{slug}.json"
    fills_path = FILLS_DIR / f"{slug}.json"

    if not force and gamma_path.exists() and fills_path.exists():
        return "skipped", 0

    market = fetch_gamma_market(slug)
    if market is None:
        log.warning("slug not found in Gamma: %s", slug)
        return "no_gamma", 0

    gamma_path.parent.mkdir(parents=True, exist_ok=True)
    gamma_path.write_text(json.dumps(market, indent=2) + "\n")

    # Extract clob token IDs — they come back as a JSON-encoded string
    clob_ids_raw = market.get("clobTokenIds")
    if isinstance(clob_ids_raw, str):
        try:
            token_ids = json.loads(clob_ids_raw)
        except json.JSONDecodeError:
            token_ids = []
    elif isinstance(clob_ids_raw, list):
        token_ids = clob_ids_raw
    else:
        token_ids = []
    token_ids = [str(t) for t in token_ids if t]

    if not token_ids:
        log.warning("no clob_token_ids for slug: %s", slug)
        fills_path.parent.mkdir(parents=True, exist_ok=True)
        fills_path.write_text(json.dumps({}) + "\n")
        return "no_tokens", 0

    fills_by_token: dict[str, list[dict[str, Any]]] = {}
    total_fills = 0
    for token_id in token_ids:
        fills = fetch_subgraph_fills(token_id)
        fills_by_token[token_id] = fills
        total_fills += len(fills)
        time.sleep(GAMMA_DELAY_S)

    fills_path.parent.mkdir(parents=True, exist_ok=True)
    fills_path.write_text(json.dumps(fills_by_token) + "\n")
    return "ok", total_fills


# --------------------------------------------------------------------------- #
# Slug selection                                                              #
# --------------------------------------------------------------------------- #

def load_slugs(slugs_file: Path, *, city: str | None, explicit: list[str] | None,
               limit: int | None) -> list[str]:
    if explicit:
        return explicit

    if not slugs_file.exists():
        die(f"slugs file not found: {slugs_file}")

    selected: list[str] = []
    with open(slugs_file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if city and row.get("city", "") != city:
                continue
            slug = row.get("slug", "").strip()
            if slug:
                selected.append(slug)
            if limit is not None and len(selected) >= limit:
                break
    return selected


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--slugs-file", type=Path, default=DEFAULT_SLUGS_CSV,
                   help=f"CSV with 'slug' column (default: {DEFAULT_SLUGS_CSV.relative_to(REPO_ROOT)})")
    p.add_argument("--city", help="filter slugs by exact city match")
    p.add_argument("--slugs", help="explicit comma-separated slug list (overrides --slugs-file)")
    p.add_argument("--limit", type=int, help="only process the first N selected slugs")
    p.add_argument("--force", action="store_true", help="re-download even if cached")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    explicit = [s.strip() for s in args.slugs.split(",")] if args.slugs else None

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    GAMMA_DIR.mkdir(parents=True, exist_ok=True)
    FILLS_DIR.mkdir(parents=True, exist_ok=True)
    configure_logging()

    slugs = load_slugs(args.slugs_file, city=args.city, explicit=explicit, limit=args.limit)
    log.info("source:  %s", args.slugs_file.relative_to(REPO_ROOT) if args.slugs_file else "(explicit)")
    log.info("filter:  city=%r  limit=%s  force=%s", args.city, args.limit, args.force)
    log.info("selected: %d slugs", len(slugs))

    if not slugs:
        die("no slugs selected")

    doc = initial_manifest()
    doc["inputs"] = {
        "slugs_file": str(args.slugs_file.relative_to(REPO_ROOT)) if args.slugs_file else None,
        "city": args.city,
        "limit": args.limit,
        "force": args.force,
        "selected_count": len(slugs),
    }
    write_manifest(doc)

    status_counts = {"ok": 0, "skipped": 0, "no_gamma": 0, "no_tokens": 0, "failed": 0}
    total_fills = 0

    try:
        for i, slug in enumerate(slugs, 1):
            try:
                status, n_fills = process_slug(slug, force=args.force)
            except Exception as e:
                log.exception("slug failed: %s (%s)", slug, e)
                status, n_fills = "failed", 0
            status_counts[status] += 1
            total_fills += n_fills

            # Progress every 10 slugs or on each slug for small runs
            if len(slugs) <= 25 or i % 10 == 0 or i == len(slugs):
                log.info(
                    "[%d/%d] %s → %s (%d fills)  totals: ok=%d skipped=%d no_gamma=%d no_tokens=%d failed=%d fills=%d",
                    i, len(slugs), slug[:60], status, n_fills,
                    status_counts["ok"], status_counts["skipped"],
                    status_counts["no_gamma"], status_counts["no_tokens"],
                    status_counts["failed"], total_fills,
                )

        doc = read_manifest() or initial_manifest()
        doc["download"]["completed_at"] = utc_now()
        doc["download"]["status"] = "complete" if status_counts["failed"] == 0 else "complete_with_errors"
        doc["download"]["slugs_attempted"] = len(slugs)
        doc["download"]["slugs_succeeded"] = status_counts["ok"]
        doc["download"]["slugs_skipped_cached"] = status_counts["skipped"]
        doc["download"]["slugs_failed"] = status_counts["failed"] + status_counts["no_gamma"]
        doc["download"]["slugs_no_tokens"] = status_counts["no_tokens"]
        doc["download"]["total_fills"] = total_fills
        write_manifest(doc)
        log.info("done: %s (%d fills across %d markets)", status_counts, total_fills, len(slugs))

    except BaseException as e:
        doc = read_manifest() or initial_manifest()
        doc["download"]["status"] = "failed"
        doc["download"]["completed_at"] = utc_now()
        doc["notes"] = (doc.get("notes") or "") + f"\nfailed: {type(e).__name__}: {e}"
        write_manifest(doc)
        raise

    return 0


if __name__ == "__main__":
    sys.exit(main())

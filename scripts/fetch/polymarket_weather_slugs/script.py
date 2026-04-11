#!/usr/bin/env python3
"""Fetch all Polymarket weather market slugs via the Gamma API.

Queries 6 weather tag IDs (Daily Temperature, climate & weather, Hurricane
Season, Hurricanes, Flood, Snow Storm), pages through closed + open markets,
dedupes by condition_id, extracts a city field from the question text, and
writes the result to ``weather-market-slugs/polymarket.csv`` at the repo root.

See scripts/fetch/polymarket_weather_slugs/README.md for the full schema
and the known historical-coverage limitation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #

SCRIPT_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_CSV = REPO_ROOT / "weather-market-slugs" / "polymarket.csv"
CACHE_DIR = REPO_ROOT / "data" / "interim" / "polymarket_weather_slugs" / "raw_gamma"
MANIFEST_PATH = REPO_ROOT / "data" / "interim" / "polymarket_weather_slugs" / "MANIFEST.json"

GAMMA_BASE = "https://gamma-api.polymarket.com"
PAGE_SIZE = 500
MAX_PAGES = 200
PAGE_DELAY_S = 0.2

WEATHER_TAGS: dict[str, str] = {
    "103040": "Daily Temperature",
    "1474":   "climate & weather",
    "102186": "Hurricane Season",
    "85":     "Hurricanes",
    "102239": "Flood",
    "103235": "Snow Storm",
}

# Extract the city/location from the question text.  Handles the dominant
# Polymarket phrasing: "Will the highest temperature in <CITY> be ... on ...".
_CITY_RE = re.compile(
    r"(?:highest\s+temperature|high\s+temperature|temperature)\s+in\s+"
    r"([A-Za-z][A-Za-z\s'\.\-]+?)\s+(?:be\b|on\b|exceed\b|reach\b|hit\b|above\b|below\b|this\b)",
    re.IGNORECASE,
)

# Normalize common variants so `--city "New York"` catches both NYC and New York City.
_CITY_ALIASES = {
    "nyc": "New York City",
    "ny": "New York City",
    "new york": "New York City",
    "la": "Los Angeles",
    "d.c.": "Washington D.C.",
    "dc": "Washington D.C.",
    "sf": "San Francisco",
}

COLS = [
    "slug", "condition_id", "question", "city", "weather_tags",
    "volume_gamma", "liquidity_gamma",
    "best_bid", "best_ask", "spread", "last_trade_price",
    "order_price_min_tick_size", "order_min_size",
    "neg_risk", "active", "closed",
    "created_at", "end_date",
    "resolution_source", "group_item_title",
    "clob_token_ids", "outcomes",
]


# --------------------------------------------------------------------------- #
# HTTP                                                                        #
# --------------------------------------------------------------------------- #

def gamma_get(path: str, **params: Any) -> list[dict[str, Any]]:
    url = f"{GAMMA_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "weather-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_tag(tag_id: str, *, refresh: bool) -> list[dict[str, Any]]:
    """Paginate closed + open markets for a tag, caching the combined result."""
    cache = CACHE_DIR / f"tag_{tag_id}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())

    out: list[dict[str, Any]] = []
    for closed_flag in ("true", "false"):
        for page in range(MAX_PAGES):
            batch = gamma_get(
                "/markets",
                tag_id=tag_id, closed=closed_flag,
                limit=PAGE_SIZE, offset=page * PAGE_SIZE,
            )
            if not batch:
                break
            out.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            time.sleep(PAGE_DELAY_S)

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


# --------------------------------------------------------------------------- #
# Row shaping                                                                 #
# --------------------------------------------------------------------------- #

def _pick(m: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if m.get(k) is not None:
            return m[k]
    return None


def _city(question: str | None) -> str:
    if not question:
        return ""
    match = _CITY_RE.search(question)
    if not match:
        return ""
    raw = match.group(1).strip().rstrip(".").strip()
    return _CITY_ALIASES.get(raw.lower(), raw)


def _row(m: dict[str, Any], tag_labels: list[str]) -> dict[str, Any]:
    return {
        "slug":                        m.get("slug"),
        "condition_id":                m.get("conditionId"),
        "question":                    m.get("question"),
        "city":                        _city(m.get("question")),
        "weather_tags":                ",".join(sorted(set(tag_labels))),
        "volume_gamma":                _pick(m, "volumeNum", "volume"),
        "liquidity_gamma":             _pick(m, "liquidityNum", "liquidity"),
        "best_bid":                    m.get("bestBid"),
        "best_ask":                    m.get("bestAsk"),
        "spread":                      m.get("spread"),
        "last_trade_price":            m.get("lastTradePrice"),
        "order_price_min_tick_size":   m.get("orderPriceMinTickSize"),
        "order_min_size":              m.get("orderMinSize"),
        "neg_risk":                    m.get("negRisk"),
        "active":                      m.get("active"),
        "closed":                      m.get("closed"),
        "created_at":                  m.get("createdAt"),
        "end_date":                    _pick(m, "endDate", "endDateIso"),
        "resolution_source":           m.get("resolutionSource"),
        "group_item_title":            m.get("groupItemTitle"),
        "clob_token_ids":              m.get("clobTokenIds"),
        "outcomes":                    m.get("outcomes"),
    }


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="bypass cache, re-hit the Gamma API")
    args = ap.parse_args()

    started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"polymarket_weather_slugs v{SCRIPT_VERSION} — {started_at}")
    print(f"output:  {OUT_CSV.relative_to(REPO_ROOT)}")
    print(f"tags:    {len(WEATHER_TAGS)}")

    # 1. Fetch per tag
    tag_raw: dict[str, list[dict[str, Any]]] = {}
    for tid, label in WEATHER_TAGS.items():
        tag_raw[tid] = fetch_tag(tid, refresh=args.refresh)
        print(f"  {tid:<8} {label:<22} {len(tag_raw[tid]):>6,}")

    # 2. Dedup by condition_id; pool all tag labels for each market
    by_cid: dict[str, dict[str, Any]] = {}
    labels_for: dict[str, list[str]] = {}
    for tid, markets in tag_raw.items():
        label = WEATHER_TAGS[tid]
        for m in markets:
            cid = m.get("conditionId")
            if not cid:
                continue
            by_cid.setdefault(cid, m)
            labels_for.setdefault(cid, []).append(label)

    df = pd.DataFrame([_row(m, labels_for[cid]) for cid, m in by_cid.items()], columns=COLS)
    print(f"\nunique markets: {len(df):,}")

    # 3. Sort: by combined volume descending; NaN volumes last
    df["_sort"] = pd.to_numeric(df["volume_gamma"], errors="coerce").fillna(0)
    df = df.sort_values("_sort", ascending=False).drop(columns=["_sort"])[COLS]

    # 4. Write CSV
    df.to_csv(OUT_CSV, index=False)
    print(f"wrote {OUT_CSV.relative_to(REPO_ROOT)} ({len(df):,} rows)")

    # 5. Stats by city
    print("\ntop 15 cities by market count:")
    for city, n in df[df["city"] != ""]["city"].value_counts().head(15).items():
        print(f"  {city:<24} {n:>6,}")
    blank_city = int((df["city"] == "").sum())
    print(f"  (blank city)            {blank_city:>6,}")

    # 6. Manifest
    manifest = {
        "manifest_version": 1,
        "step": "polymarket_weather_slugs",
        "script": {
            "path": "scripts/fetch/polymarket_weather_slugs/script.py",
            "version": SCRIPT_VERSION,
        },
        "source": {
            "api": GAMMA_BASE,
            "tag_ids": WEATHER_TAGS,
        },
        "output": {
            "csv": str(OUT_CSV.relative_to(REPO_ROOT)),
            "row_count": int(len(df)),
        },
        "stats": {
            "tags_queried": len(WEATHER_TAGS),
            "raw_markets_total": sum(len(v) for v in tag_raw.values()),
            "unique_markets": int(len(df)),
            "cities_distinct": int(df[df["city"] != ""]["city"].nunique()),
        },
        "generated_at": started_at,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    print(f"wrote {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

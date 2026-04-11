"""Download Polymarket CLOB /prices-history time series for weather slugs.

For each NYC daily-temperature slug (closed + open), pulls the price history
from the Polymarket CLOB API and writes one JSON per slug under
``data/raw/polymarket_prices_history/<slug>.json``.

The CLOB API behavior we discovered:
    • Closed markets retain `interval=max fidelity=60` (hourly, full lifetime)
      with ~30-110 points per slug. Higher fidelities return 0 points or 400.
    • Open markets accept `interval=1d fidelity=1` (1-min for 24h) plus
      `interval=max fidelity=60` (hourly for full lifetime).

Strategy: pull `interval=max fidelity=60` for every slug. Open markets
also get a `interval=1d fidelity=1` pull for 1-min granularity over the
last 24 hours.

Output:
    data/raw/polymarket_prices_history/MANIFEST.json
    data/raw/polymarket_prices_history/download.log
    data/raw/polymarket_prices_history/<slug>.json
        {
          "slug": "...",
          "condition_id": "...",
          "yes_token_id": "...",
          "fetched_at": "...",
          "history_max_h60":  [{t, p}, ...],
          "history_1d_min1":  [{t, p}, ...]    # only for open markets
        }

Usage:
    uv run python scripts/polymarket_prices_history/download.py
        [--limit N]      # cap number of slugs (testing)
        [--force]        # re-download even if file exists
        [--slugs a,b,c]  # only specific slugs
        [--city "New York City"]  # default; restricts to one city
"""
from __future__ import annotations

import argparse
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

import duckdb

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "polymarket_prices_history"
DESCRIPTION = (
    "Polymarket CLOB /prices-history time series per slug. Hourly fidelity "
    "for the full market lifetime; 1-min fidelity for active markets' last "
    "24h. One JSON per slug under data/raw/polymarket_prices_history/."
)
SCRIPT_VERSION = 1

CLOB_BASE = "https://clob.polymarket.com"
DEFAULT_CITY = "New York City"
DEFAULT_TAG = "Daily Temperature"
USER_AGENT = "weather-prices-history/1.0"
REQUEST_DELAY_S = 0.20  # be polite to clob api
MAX_RETRIES = 3

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
TARGET_REL = f"data/raw/{SOURCE_NAME}"

# --- logging --------------------------------------------------------------- #

log = logging.getLogger(SOURCE_NAME)


def _setup_logging() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RAW_DIR / "download.log"
    fmt = "%(asctime)sZ [%(levelname)s] %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")
    file_h = logging.FileHandler(log_path, mode="a")
    file_h.setFormatter(formatter)
    stream_h = logging.StreamHandler(sys.stdout)
    stream_h.setFormatter(formatter)
    log.handlers.clear()
    log.addHandler(file_h)
    log.addHandler(stream_h)
    log.setLevel(logging.INFO)


# --- http ------------------------------------------------------------------ #


def _http_get_json(url: str, timeout: float = 30.0) -> Any:
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400:
                # Bad request — typically means "no data for this combination".
                # Don't retry; return empty.
                return {"history": []}
            if e.code == 429:
                wait = 5 * attempt
                log.warning(f"  429 rate-limited, sleeping {wait}s")
                time.sleep(wait)
                last_err = e
                continue
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(2 * attempt)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} retries: {url} :: {last_err}")


# --- catalog --------------------------------------------------------------- #


def load_slugs(city: str, slugs_filter: list[str] | None, limit: int | None) -> list[dict]:
    """Read NYC daily-temp slugs (or filtered set) from local processed parquet."""
    con = duckdb.connect()
    where = ["weather_tags ILIKE '%Daily Temperature%'"]
    if city:
        where.append(f"city = '{city}'")
    if slugs_filter:
        slist = ",".join(f"'{s}'" for s in slugs_filter)
        where.append(f"slug IN ({slist})")
    where_sql = " AND ".join(where)
    rows = con.execute(f"""
        SELECT slug, condition_id, yes_token_id, closed, end_date
        FROM 'data/processed/polymarket_weather/markets.parquet'
        WHERE {where_sql} AND yes_token_id IS NOT NULL
        ORDER BY end_date DESC
        {"LIMIT " + str(limit) if limit else ""}
    """).fetchall()
    return [
        {
            "slug": r[0],
            "condition_id": r[1],
            "yes_token_id": r[2],
            "closed": r[3],
            "end_date": r[4],
        }
        for r in rows
    ]


# --- prices_history fetch -------------------------------------------------- #


def fetch_history_max_h60(token_id: str) -> list[dict]:
    url = f"{CLOB_BASE}/prices-history?market={token_id}&interval=max&fidelity=60"
    resp = _http_get_json(url)
    return resp.get("history", []) or []


def fetch_history_1d_min1(token_id: str) -> list[dict]:
    url = f"{CLOB_BASE}/prices-history?market={token_id}&interval=1d&fidelity=1"
    resp = _http_get_json(url)
    return resp.get("history", []) or []


# --- manifest -------------------------------------------------------------- #


def init_manifest(n_slugs: int) -> Path:
    manifest_path = RAW_DIR / "MANIFEST.json"
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "manifest_version": 1,
        "source_name": SOURCE_NAME,
        "description": DESCRIPTION,
        "upstream": {
            "url": f"{CLOB_BASE}/prices-history",
            "docs": "https://docs.polymarket.com/#timeseries-data",
        },
        "script": {
            "path": f"scripts/{SOURCE_NAME}/download.py",
            "version": SCRIPT_VERSION,
        },
        "download": {
            "started_at": started,
            "completed_at": None,
            "status": "in_progress",
            "n_slugs_planned": n_slugs,
            "n_slugs_done": 0,
            "n_slugs_empty": 0,
            "n_slugs_failed": 0,
        },
        "target": {"raw_dir": TARGET_REL},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path


def update_manifest(manifest_path: Path, **fields: Any) -> None:
    manifest = json.loads(manifest_path.read_text())
    manifest["download"].update(fields)
    manifest_path.write_text(json.dumps(manifest, indent=2))


def finalize_manifest(manifest_path: Path, status: str = "ok") -> None:
    manifest = json.loads(manifest_path.read_text())
    manifest["download"]["completed_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["download"]["status"] = status
    manifest_path.write_text(json.dumps(manifest, indent=2))


# --- main downloader ------------------------------------------------------- #


def download_slug(slug_row: dict, force: bool) -> tuple[str, int]:
    """Returns (status, n_points)."""
    slug = slug_row["slug"]
    out = RAW_DIR / f"{slug}.json"
    if out.exists() and not force:
        try:
            existing = json.loads(out.read_text())
            n = len(existing.get("history_max_h60", []))
            return ("skip", n)
        except Exception:
            pass

    token = slug_row["yes_token_id"]
    history_h60 = fetch_history_max_h60(token)
    time.sleep(REQUEST_DELAY_S)

    history_1d_min1: list[dict] = []
    if not slug_row["closed"]:
        history_1d_min1 = fetch_history_1d_min1(token)
        time.sleep(REQUEST_DELAY_S)

    record = {
        "slug": slug,
        "condition_id": slug_row["condition_id"],
        "yes_token_id": token,
        "closed": bool(slug_row["closed"]),
        "end_date": str(slug_row["end_date"]),
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "history_max_h60": history_h60,
        "history_1d_min1": history_1d_min1,
    }
    out.write_text(json.dumps(record))
    n = len(history_h60)
    return ("ok" if n > 0 else "empty", n)


def main() -> int:
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--city", default=DEFAULT_CITY)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--slugs", type=str, default=None,
                    help="comma-separated subset")
    args = ap.parse_args()

    _setup_logging()
    log.info(f"starting {SOURCE_NAME} download")
    log.info(f"city={args.city}  limit={args.limit}  force={args.force}")

    slugs_filter = args.slugs.split(",") if args.slugs else None
    rows = load_slugs(args.city, slugs_filter, args.limit)
    log.info(f"loaded {len(rows)} slugs from local catalog")

    manifest_path = init_manifest(len(rows))

    n_done = 0
    n_empty = 0
    n_skip = 0
    n_failed = 0
    t0 = time.time()
    for i, row in enumerate(rows, start=1):
        slug = row["slug"]
        try:
            status, n_pts = download_slug(row, args.force)
        except Exception as e:
            log.error(f"  [{i}/{len(rows)}] {slug}: ERROR {e}")
            n_failed += 1
            continue
        if status == "skip":
            n_skip += 1
        elif status == "empty":
            n_empty += 1
            n_done += 1
            if i % 25 == 0 or i == len(rows):
                log.info(f"  [{i}/{len(rows)}] {slug}: empty (no history)")
        else:
            n_done += 1
            if i % 25 == 0 or i == len(rows):
                log.info(f"  [{i}/{len(rows)}] {slug}: {n_pts} points")
        if i % 50 == 0:
            update_manifest(manifest_path,
                            n_slugs_done=n_done, n_slugs_empty=n_empty, n_slugs_failed=n_failed)

    elapsed = time.time() - t0
    log.info(f"done in {elapsed:.0f}s: {n_done} ok ({n_empty} empty), "
             f"{n_skip} skipped, {n_failed} failed")
    update_manifest(manifest_path,
                    n_slugs_done=n_done, n_slugs_empty=n_empty, n_slugs_failed=n_failed)
    finalize_manifest(manifest_path, status="ok" if n_failed == 0 else "ok_with_errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())

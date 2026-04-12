#!/usr/bin/env python3
"""Download IEM MOS/NBM text products for US airport stations.

Pulls GFS MOS and NBS (NBM station text) from the IEM MOS archive
for a set of stations over a date range. One CSV per (station, model)
under ``data/raw/iem_mos/``.

Upstream CGI:
    https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py

Models pulled:
    GFS — classic GFS MOS, 4 cycles/day (00/06/12/18Z), ~84h horizon
    NBS — NBM station text, 4 cycles/day (01/07/13/19Z), 72h horizon
          Includes ensemble spread fields (tsd, xnd)

Output layout::

    data/raw/iem_mos/
    ├── MANIFEST.json
    ├── download.log
    ├── GFS/
    │   ├── KLGA.csv
    │   ├── KATL.csv
    │   └── ...
    └── NBS/
        ├── KLGA.csv
        └── ...

Usage::

    uv run python scripts/iem_mos/download.py \\
        --stations LGA ATL DAL SEA ORD MIA LAX SFO HOU AUS DEN \\
        --start 2025-12-01 --end 2026-04-12
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
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Any

SOURCE_NAME = "iem_mos"
DESCRIPTION = (
    "IEM MOS/NBM text products (GFS MOS + NBS) for US airport stations. "
    "Station-level calibrated temperature forecasts with ensemble spread."
)
SCRIPT_VERSION = 1

UPSTREAM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
MODELS = ["GFS", "NBS"]
USER_AGENT = "weather-mos/1.0"
MAX_RETRIES = 3

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME

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


def normalize_station(s: str) -> str:
    up = s.upper().strip()
    if len(up) == 3:
        return f"K{up}"
    return up


def fetch_mos(station_icao: str, model: str, start: str, end: str) -> bytes:
    params = [
        ("station", station_icao),
        ("model", model),
        ("sts", f"{start}T00:00Z"),
        ("ets", f"{end}T00:00Z"),
        ("format", "csv"),
    ]
    url = UPSTREAM_URL + "?" + urllib.parse.urlencode(params)
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            wait = 5 * attempt
            log.warning(f"  attempt {attempt}/{MAX_RETRIES} failed: {e} (sleeping {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"failed to fetch {station_icao}/{model}: {last_err}")


def write_manifest(stations: list[str], models: list[str], start: str, end: str,
                   status: str, n_files: int, total_bytes: int) -> None:
    manifest = {
        "manifest_version": 1,
        "source_name": SOURCE_NAME,
        "description": DESCRIPTION,
        "upstream": {"url": UPSTREAM_URL},
        "script": {"path": f"scripts/{SOURCE_NAME}/download.py", "version": SCRIPT_VERSION},
        "download": {
            "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": status,
            "stations": stations,
            "models": models,
            "start": start,
            "end": end,
            "n_files": n_files,
            "total_bytes": total_bytes,
        },
    }
    (RAW_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))


def main() -> int:
    p = argparse.ArgumentParser(description=DESCRIPTION)
    p.add_argument("--stations", nargs="+", required=True,
                   help="Station IDs (3-char FAA or 4-char ICAO)")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--models", nargs="+", default=MODELS,
                   help=f"MOS models to pull (default: {MODELS})")
    p.add_argument("--force", action="store_true", help="Re-download existing files")
    args = p.parse_args()

    _setup_logging()
    stations = [normalize_station(s) for s in args.stations]
    log.info(f"starting {SOURCE_NAME} download")
    log.info(f"stations: {' '.join(stations)}")
    log.info(f"models:   {' '.join(args.models)}")
    log.info(f"range:    {args.start} → {args.end}")

    n_files = 0
    total_bytes = 0
    n_failed = 0

    for model in args.models:
        model_dir = RAW_DIR / model
        model_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"--- model: {model} ---")

        for station in stations:
            out_path = model_dir / f"{station}.csv"
            if out_path.exists() and not args.force:
                size = out_path.stat().st_size
                log.info(f"  {station}/{model}: skip (exists, {size:,} bytes)")
                n_files += 1
                total_bytes += size
                continue

            try:
                body = fetch_mos(station, model, args.start, args.end)
            except Exception as e:
                log.error(f"  {station}/{model}: FAILED {e}")
                n_failed += 1
                continue

            out_path.write_bytes(body)
            size = len(body)
            lines = body.count(b"\n")
            log.info(f"  {station}/{model}: {lines:,} lines, {size:,} bytes")
            n_files += 1
            total_bytes += size
            time.sleep(0.5)

    status = "ok" if n_failed == 0 else "ok_with_errors"
    write_manifest(stations, args.models, args.start, args.end, status, n_files, total_bytes)
    log.info(f"done: {n_files} files, {total_bytes:,} bytes, {n_failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

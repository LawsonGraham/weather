"""Shared helpers for the NBS and GFS MOS watchers.

The IEM MOS archive stores one CSV per (station, model). A full re-pull of
11 stations x ~5 months is ~40MB and ~30s. This module does incremental
fetches instead:

  1. GET a small recent window (default: last 3 days) per station, in parallel
  2. Merge response rows into the existing CSV, deduping by (runtime, ftime)
  3. Rewrite the per-station CSV

Fetch cost: ~1MB / ~2s vs the full-pull's ~40MB / ~30s. Historical rows
are preserved (no --force needed).
"""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

IEM_MOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
IEM_USER_AGENT = "weather-mos/1.0"

# 3 days covers the publishing latency of any NBS/GFS cycle (max 1-2h) and
# protects against missing rows if a probe flickers or a fetch is skipped.
DEFAULT_WINDOW_DAYS = 3

# Serialize transform.py invocations across NBS + GFS watchers. scripts/iem_mos
# /transform.py processes BOTH models internally on every run, so two watchers
# calling it concurrently race on the same output parquets — a reader globbing
# data/processed/iem_mos/<model>/*.parquet mid-write picks up a partial tmp
# file. The lock guarantees one transform at a time. Scoped to the process
# that imports this module (both watchers run in the same daemon process).
transform_lock = asyncio.Lock()


async def fetch_and_merge_mos(
    model: str,
    stations: list[str],
    raw_dir: Path,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """Fetch recent MOS rows for each station and merge into existing CSVs.

    Returns a summary dict suitable for logging. Raises on HTTP errors.
    """
    today = datetime.now(UTC).date()
    start = today - timedelta(days=window_days)
    sts = f"{start.isoformat()}T00:00Z"
    ets = f"{today.isoformat()}T23:59Z"

    model_dir = raw_dir / model
    model_dir.mkdir(parents=True, exist_ok=True)

    headers = {"User-Agent": IEM_USER_AGENT}
    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        tasks = [
            _fetch_and_merge_one(client, model, station, sts, ets, model_dir)
            for station in stations
        ]
        results = await asyncio.gather(*tasks)

    return {
        "model": model,
        "stations": len(stations),
        "window_start": start.isoformat(),
        "window_end": today.isoformat(),
        "new_rows_merged": sum(r["new_rows"] for r in results),
        "total_rows_after": sum(r["total_rows"] for r in results),
    }


async def _fetch_and_merge_one(
    client: httpx.AsyncClient,
    model: str,
    station: str,
    sts: str,
    ets: str,
    model_dir: Path,
) -> dict:
    """Fetch one station's recent window, merge into `<model_dir>/<station>.csv`."""
    params = {
        "station": station,
        "model": model,
        "sts": sts,
        "ets": ets,
        "format": "csv",
    }
    r = await client.get(IEM_MOS_URL, params=params)
    r.raise_for_status()

    csv_path = model_dir / f"{station}.csv"
    merged_text, stats = _merge_csv(csv_path, r.text)
    csv_path.write_text(merged_text)
    return stats


def _merge_csv(existing_path: Path, new_csv_text: str) -> tuple[str, dict]:
    """Merge new CSV rows into existing CSV, dedupe by (runtime, ftime).

    Keys rows by (col[0], col[1]) = (runtime, ftime). Newer rows overwrite
    older ones at the same key. Output is sorted ascending by (runtime, ftime).
    """
    rows: dict[tuple[str, str], list[str]] = {}
    header: list[str] | None = None

    # Read existing
    if existing_path.exists():
        with existing_path.open() as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    rows[(row[0], row[1])] = row
    existing_count = len(rows)

    # Merge in new (overwriting if same key)
    reader = csv.reader(io.StringIO(new_csv_text))
    new_header = next(reader, None)
    if header is None:
        header = new_header
    for row in reader:
        if len(row) >= 2:
            rows[(row[0], row[1])] = row

    # Serialize sorted
    sorted_rows = sorted(rows.values(), key=lambda r: (r[0], r[1]))
    out = io.StringIO()
    writer = csv.writer(out)
    if header:
        writer.writerow(header)
    writer.writerows(sorted_rows)
    return out.getvalue(), {
        "new_rows": len(rows) - existing_count,
        "total_rows": len(rows),
    }

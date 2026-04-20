"""HRRR (High-Resolution Rapid Refresh) forecast watcher.

HRRR publishes hourly to NOAA's public S3 bucket with ~1.5-2h latency.
For our fxx=6 (6h ahead) product we assume a conservative 2h latency.

CHEAP PROBE: HEAD the expected latest-cycle `.idx` on S3. If 200 AND
we haven't fetched that cycle yet, trigger.

HEAVY FETCH: run scripts/hrrr/download.py with a 2-day rolling window.
The downloader is idempotent (reads existing parquet, skips present
cycles) so unchanged cycles cost nothing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from lib.watchers.base import Watcher, run_subprocess

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN", "KNYC"]
ROLLING_DAYS = 2
HRRR_S3 = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HRRR_LATENCY_HOURS = 2  # buffer so we probe cycles that are definitely published


class HRRRWatcher(Watcher):
    def __init__(self, interval_seconds: int = 10):
        super().__init__(name="hrrr", interval_seconds=interval_seconds,
                        jitter_seconds=5)

    async def has_new_data(self) -> bool:
        cycle = self._expected_latest_cycle()
        idx_key = (f"hrrr.{cycle.strftime('%Y%m%d')}/conus/"
                   f"hrrr.t{cycle.strftime('%H')}z.wrfsfcf06.grib2.idx")
        url = f"{HRRR_S3}/{idx_key}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.head(url)
        if r.status_code != 200:
            return False
        last_cycle = self.state.last_detail.get("latest_cycle")
        return last_cycle != cycle.isoformat()

    async def fetch_new_data(self) -> dict:
        import asyncio
        today = datetime.now(UTC).date()
        start = today - timedelta(days=ROLLING_DAYS)

        loop = asyncio.get_running_loop()
        cmd = [
            "uv", "run", "python", "scripts/hrrr/download.py",
            "--stations", *STATIONS,
            "--start", start.isoformat(),
            "--end", today.isoformat(),
            "--fxx", "6",
            "--parallel", "10",
        ]
        rc, _, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=600),
        )
        if rc != 0:
            raise RuntimeError(f"hrrr download failed: {err[-500:]}")

        latest = self._expected_latest_cycle()
        return {
            "window_start": start.isoformat(),
            "window_end": today.isoformat(),
            "stations": len(STATIONS),
            "latest_cycle": latest.isoformat(),
        }

    def _expected_latest_cycle(self) -> datetime:
        """Hour we expect to be available on S3 (now - latency, floored to hour)."""
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        return now - timedelta(hours=HRRR_LATENCY_HOURS)

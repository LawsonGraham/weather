"""HRRR (High-Resolution Rapid Refresh) forecast watcher.

HRRR runs hourly with ~1.5h latency to AWS. We poll every 10 min with a
rolling 2-day window. The downloader is idempotent: it reads existing parquet
and skips cycles already present.

Cycle latency matters: if we poll at 20:30 UTC, HRRR 19z is almost certainly
available but 20z may not be. That's fine — the downloader picks up whatever's
ready and skips the rest.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lib.watchers.base import Watcher, run_subprocess

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN", "KNYC"]
ROLLING_DAYS = 2


class HRRRWatcher(Watcher):
    def __init__(self, interval_seconds: int = 600):
        super().__init__(name="hrrr", interval_seconds=interval_seconds,
                        jitter_seconds=120)

    async def poll(self) -> dict:
        import asyncio

        today = datetime.now(UTC).date()
        start = today - timedelta(days=ROLLING_DAYS)
        end = today

        loop = asyncio.get_running_loop()
        # HRRR downloader is async + heavy — give it 10 minutes
        cmd = [
            "uv", "run", "python", "scripts/hrrr/download.py",
            "--stations", *STATIONS,
            "--start", start.isoformat(),
            "--end", end.isoformat(),
            "--fxx", "6",  # 6-hour-ahead forecast covers afternoon peaks well
            "--parallel", "10",
        ]
        rc, out, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=600)
        )
        if rc != 0:
            raise RuntimeError(f"hrrr download failed: {err[-500:]}")

        return {
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "stations": len(STATIONS),
        }

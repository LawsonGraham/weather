"""METAR observations watcher.

METAR reports come in ~hourly at top-of-hour plus SPECI (unscheduled) reports
when conditions change. Poll every 5 minutes — cheap, and catches SPECIs soon
after publication.

The existing download script is idempotent for past months and always re-fetches
the current month (partial). So we just rerun it with today as the end.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lib.watchers.base import Watcher, run_subprocess

# Station IDs are IEM's 3-char form (no K prefix for this script)
STATIONS = ["LGA", "ATL", "DAL", "SEA", "ORD", "MIA",
            "LAX", "SFO", "HOU", "AUS", "DEN", "NYC"]


class METARWatcher(Watcher):
    def __init__(self, interval_seconds: int = 300):
        super().__init__(name="metar", interval_seconds=interval_seconds,
                        jitter_seconds=15)

    async def poll(self) -> dict:
        import asyncio

        today = datetime.now(UTC).date()
        # Pull current month only (downloader auto re-fetches "today's month")
        start = today.replace(day=1)

        loop = asyncio.get_running_loop()
        cmd = [
            "uv", "run", "python", "scripts/iem_metar/download.py",
            "--stations", *STATIONS,
            "--start", start.isoformat(),
            "--end", today.isoformat(),
        ]
        rc, out, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=180)
        )
        if rc != 0:
            raise RuntimeError(f"iem_metar download failed: {err[-500:]}")

        # Transform
        cmd2 = ["uv", "run", "python", "scripts/iem_metar/transform.py"]
        rc2, _, err2 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd2, timeout=120)
        )
        if rc2 != 0:
            raise RuntimeError(f"iem_metar transform failed: {err2[-500:]}")

        return {"month": start.isoformat(), "through": today.isoformat(),
                "stations": len(STATIONS)}

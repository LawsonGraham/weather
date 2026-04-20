"""GFS MOS watcher — same pattern as NBS, pulls GFS model.

GFS MOS runs 4x/day (00/06/12/18 UTC). See nbs.py for why we pull full
history on every poll.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from lib.watchers.base import Watcher, run_subprocess

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN"]
GFS_HISTORY_START = date(2025, 11, 30)


class GFSWatcher(Watcher):
    def __init__(self, interval_seconds: int = 600):
        super().__init__(name="gfs", interval_seconds=interval_seconds, jitter_seconds=60)

    async def poll(self) -> dict:
        import asyncio

        today = datetime.now(UTC).date()

        loop = asyncio.get_running_loop()
        cmd = [
            "uv", "run", "python", "scripts/iem_mos/download.py",
            "--start", GFS_HISTORY_START.isoformat(),
            "--end", today.isoformat(),
            "--stations", *STATIONS,
            "--models", "GFS",
            "--force",
        ]
        rc, _, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=300)
        )
        if rc != 0:
            raise RuntimeError(f"iem_mos download (GFS) failed: {err[-500:]}")

        cmd2 = ["uv", "run", "python", "scripts/iem_mos/transform.py"]
        rc2, _, err2 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd2, timeout=120)
        )
        if rc2 != 0:
            raise RuntimeError(f"iem_mos transform failed: {err2[-500:]}")

        return {"through": today.isoformat(), "stations": len(STATIONS)}

"""GFS MOS watcher — same pattern as NBS but for the GFS model.

GFS MOS publishes 4x/day (00 / 06 / 12 / 18 UTC). See nbs.py for why
we re-pull full history per fetch.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from lib.watchers.base import Watcher, run_subprocess
from lib.watchers.nbs import _parse_max_first_col

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN"]
GFS_HISTORY_START = date(2025, 11, 30)

PROBE_STATION = "KLGA"
IEM_MOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"


class GFSWatcher(Watcher):
    def __init__(self, interval_seconds: int = 10):
        super().__init__(name="gfs", interval_seconds=interval_seconds,
                        jitter_seconds=8)
        self._last_probed_max: datetime | None = None

    async def has_new_data(self) -> bool:
        today = datetime.now(UTC).date()
        self._last_probed_max = await self._probe_upstream_max(today)
        if self._last_probed_max is None:
            return False
        local_max_str = self.state.last_detail.get("max_runtime")
        if not local_max_str:
            return True
        local_max = datetime.fromisoformat(local_max_str)
        return self._last_probed_max > local_max

    async def fetch_new_data(self) -> dict:
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
            None, lambda: run_subprocess(cmd, timeout=300),
        )
        if rc != 0:
            raise RuntimeError(f"iem_mos download (GFS) failed: {err[-500:]}")

        cmd2 = ["uv", "run", "python", "scripts/iem_mos/transform.py"]
        rc2, _, err2 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd2, timeout=120),
        )
        if rc2 != 0:
            raise RuntimeError(f"iem_mos transform failed: {err2[-500:]}")

        return {
            "through": today.isoformat(),
            "stations": len(STATIONS),
            "max_runtime": self._last_probed_max.isoformat() if self._last_probed_max else None,
        }

    async def _probe_upstream_max(self, today: date) -> datetime | None:
        params = {
            "station": PROBE_STATION,
            "model": "GFS",
            "sts": f"{today.isoformat()}T00:00Z",
            "ets": f"{today.isoformat()}T23:59Z",
            "format": "csv",
        }
        headers = {"User-Agent": "weather-mos/1.0"}
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            r = await client.get(IEM_MOS_URL, params=params)
            r.raise_for_status()
        return _parse_max_first_col(r.text)

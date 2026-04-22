"""GFS MOS watcher — same pattern as NBS but for the GFS model.

GFS MOS publishes 4x/day (00 / 06 / 12 / 18 UTC). See nbs.py for the
probe/fetch shape; only the model string differs.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from lib.watchers._iem_mos_helpers import (
    IEM_MOS_URL,
    IEM_USER_AGENT,
    fetch_and_merge_mos,
    transform_lock,
)
from lib.watchers.base import REPO_ROOT, Watcher, run_subprocess
from lib.watchers.nbs import _parse_max_first_col

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN"]
PROBE_STATION = "KLGA"
RAW_DIR = REPO_ROOT / "data" / "raw" / "iem_mos"


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
        return self._last_probed_max > datetime.fromisoformat(local_max_str)

    async def fetch_new_data(self) -> dict:
        import asyncio
        summary = await fetch_and_merge_mos("GFS", STATIONS, RAW_DIR)

        # Lock: serialize with NBS's transform call (see nbs.py).
        loop = asyncio.get_running_loop()
        async with transform_lock:
            cmd = ["uv", "run", "python", "scripts/iem_mos/transform.py"]
            rc, _, err = await loop.run_in_executor(
                None, lambda: run_subprocess(cmd, timeout=120),
            )
        if rc != 0:
            raise RuntimeError(f"iem_mos transform failed: {err[-500:]}")

        return {
            **summary,
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
        headers = {"User-Agent": IEM_USER_AGENT}
        # 30s tolerates slow routes (e.g. VPN → US-central IEM) without
        # falsely reporting "no new data" on transient latency spikes.
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            r = await client.get(IEM_MOS_URL, params=params)
            r.raise_for_status()
        return _parse_max_first_col(r.text)

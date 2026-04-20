"""METAR observations watcher.

METAR publishes ~hourly at top-of-hour, plus SPECIs (unscheduled reports
on rapid weather changes) anytime.

CHEAP PROBE: GET 1 station x today from IEM ASOS CGI with `tmpf` only
(~1-2KB). Parse max `valid` timestamp, compare to stored max.

HEAVY FETCH: rerun iem_metar download.py for the current month. The
downloader is idempotent for past months and always re-fetches the
current month (which is partial).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from lib.watchers.base import Watcher, run_subprocess

STATIONS = ["LGA", "ATL", "DAL", "SEA", "ORD", "MIA",
            "LAX", "SFO", "HOU", "AUS", "DEN", "NYC"]
PROBE_STATION = "LGA"
IEM_ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"


class METARWatcher(Watcher):
    def __init__(self, interval_seconds: int = 10):
        super().__init__(name="metar", interval_seconds=interval_seconds,
                        jitter_seconds=5)
        self._last_probed_max: datetime | None = None

    async def has_new_data(self) -> bool:
        today = datetime.now(UTC).date()
        self._last_probed_max = await self._probe_upstream_max(today)
        if self._last_probed_max is None:
            return False
        local_max_str = self.state.last_detail.get("max_valid")
        if not local_max_str:
            return True
        local_max = datetime.fromisoformat(local_max_str)
        return self._last_probed_max > local_max

    async def fetch_new_data(self) -> dict:
        import asyncio
        today = datetime.now(UTC).date()
        start = today.replace(day=1)

        loop = asyncio.get_running_loop()
        cmd = [
            "uv", "run", "python", "scripts/iem_metar/download.py",
            "--stations", *STATIONS,
            "--start", start.isoformat(),
            "--end", today.isoformat(),
        ]
        rc, _, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=180),
        )
        if rc != 0:
            raise RuntimeError(f"iem_metar download failed: {err[-500:]}")

        cmd2 = ["uv", "run", "python", "scripts/iem_metar/transform.py"]
        rc2, _, err2 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd2, timeout=120),
        )
        if rc2 != 0:
            raise RuntimeError(f"iem_metar transform failed: {err2[-500:]}")

        return {
            "month": start.isoformat(),
            "through": today.isoformat(),
            "stations": len(STATIONS),
            "max_valid": self._last_probed_max.isoformat() if self._last_probed_max else None,
        }

    async def _probe_upstream_max(self, today: date) -> datetime | None:
        params = {
            "station": PROBE_STATION,
            "year1": today.year, "month1": today.month, "day1": today.day,
            "year2": today.year, "month2": today.month, "day2": today.day,
            "data": "tmpf",
            "tz": "Etc/UTC",
            "format": "onlycomma",
            "missing": "empty",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(IEM_ASOS_URL, params=params)
            r.raise_for_status()
        return _parse_max_second_col(r.text)


def _parse_max_second_col(csv_text: str) -> datetime | None:
    """Parse IEM ASOS CSV where col 2 is the `valid` timestamp. Return max."""
    lines = csv_text.strip().splitlines()
    if len(lines) < 2:
        return None
    max_dt = None
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            dt = datetime.fromisoformat(parts[1]).replace(tzinfo=UTC)
        except ValueError:
            continue
        if max_dt is None or dt > max_dt:
            max_dt = dt
    return max_dt

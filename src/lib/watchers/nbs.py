"""NBS (NBM station text) watcher.

NBM forecasts publish 4x/day — IEM ingests runs at ~01 / 07 / 13 / 19 UTC
(with ~1h publishing lag). New runs are visible via the IEM MOS CGI.

CHEAP PROBE: GET one station (KLGA) x today only from IEM — returns a
~5-20KB CSV. Parse max `runtime`, compare to the last runtime we fetched.
Any newer upstream runtime → trigger fetch.

HEAVY FETCH: pull full history (NBS_HISTORY_START → today, all stations,
--force). Necessary because the iem_mos downloader writes ONE CSV per
(station, model) and --force with a narrow window wipes history. ~40MB
per fetch, ~30s.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from lib.watchers.base import Watcher, run_subprocess

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN"]
NBS_HISTORY_START = date(2025, 11, 30)

PROBE_STATION = "KLGA"
IEM_MOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"


class NBSWatcher(Watcher):
    def __init__(self, interval_seconds: int = 10):
        super().__init__(name="nbs", interval_seconds=interval_seconds,
                        jitter_seconds=5)
        # Populated by has_new_data() and read by fetch_new_data() so we can
        # record the new max_runtime in state.last_detail after a fetch.
        self._last_probed_max: datetime | None = None

    async def has_new_data(self) -> bool:
        today = datetime.now(UTC).date()
        self._last_probed_max = await self._probe_upstream_max(today)
        if self._last_probed_max is None:
            return False  # upstream has nothing for today yet
        local_max_str = self.state.last_detail.get("max_runtime")
        if not local_max_str:
            return True  # never fetched
        local_max = datetime.fromisoformat(local_max_str)
        return self._last_probed_max > local_max

    async def fetch_new_data(self) -> dict:
        import asyncio
        today = datetime.now(UTC).date()
        loop = asyncio.get_running_loop()

        cmd = [
            "uv", "run", "python", "scripts/iem_mos/download.py",
            "--start", NBS_HISTORY_START.isoformat(),
            "--end", today.isoformat(),
            "--stations", *STATIONS,
            "--models", "NBS",
            "--force",
        ]
        rc, _, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=300),
        )
        if rc != 0:
            raise RuntimeError(f"iem_mos download (NBS) failed: {err[-500:]}")

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
        """Fetch 1 station x today from IEM CGI, parse max runtime."""
        # Param shape must match scripts/iem_mos/download.py (same CGI).
        params = {
            "station": PROBE_STATION,
            "model": "NBS",
            "sts": f"{today.isoformat()}T00:00Z",
            "ets": f"{today.isoformat()}T23:59Z",
            "format": "csv",
        }
        headers = {"User-Agent": "weather-mos/1.0"}
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            r = await client.get(IEM_MOS_URL, params=params)
            r.raise_for_status()
        return _parse_max_first_col(r.text)


def _parse_max_first_col(csv_text: str) -> datetime | None:
    """Parse CSV where first column after header is a UTC timestamp. Return max."""
    lines = csv_text.strip().splitlines()
    if len(lines) < 2:
        return None
    max_dt = None
    for line in lines[1:]:
        parts = line.split(",", 1)
        if not parts:
            continue
        try:
            dt = datetime.fromisoformat(parts[0]).replace(tzinfo=UTC)
        except ValueError:
            continue
        if max_dt is None or dt > max_dt:
            max_dt = dt
    return max_dt

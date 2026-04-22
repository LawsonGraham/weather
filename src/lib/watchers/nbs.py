"""NBS (NBM station text) watcher.

NBM forecasts publish 4x/day — IEM ingests runs at ~01 / 07 / 13 / 19 UTC
(~1h publishing lag). We probe IEM every 10s for a new max runtime and,
when we see one, fetch just the last 3 days of data per station and
merge-dedupe into our existing CSVs.

CHEAP PROBE: GET one station (KLGA) x today only from IEM (~5-20KB).
HEAVY FETCH: parallel GET last 3 days for all stations, merge-dedupe
into <raw>/NBS/<station>.csv, then rerun the transform to rebuild
the parquet. ~1MB / ~2s total.
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

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN"]
PROBE_STATION = "KLGA"
RAW_DIR = REPO_ROOT / "data" / "raw" / "iem_mos"


class NBSWatcher(Watcher):
    def __init__(self, interval_seconds: int = 10):
        super().__init__(name="nbs", interval_seconds=interval_seconds,
                        jitter_seconds=5)
        # Set by has_new_data(), read by fetch_new_data() to record the new
        # max_runtime in state.last_detail after a successful fetch.
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
        # Incremental: pull last 3 days, merge into per-station CSVs.
        summary = await fetch_and_merge_mos("NBS", STATIONS, RAW_DIR)

        # Rebuild parquet from the (now-updated) per-station CSVs.
        # Lock: transform.py processes BOTH NBS + GFS in one run, so concurrent
        # calls from NBS and GFS watchers race on the output parquets.
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
        """Fetch 1 station x today from IEM CGI, parse max runtime."""
        params = {
            "station": PROBE_STATION,
            "model": "NBS",
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

"""NBS (NBM station text) watcher.

NBM runs 4x/day — IEM ingests them ~01z / 07z / 13z / 19z UTC (1h lag
after NWS publishes). We poll every 10 minutes.

IMPORTANT: the iem_mos downloader writes ONE CSV per (station, model)
covering the entire requested date range — passing `--force` with a
narrow window WIPES historical data. So we always pass the full history
range (NBS_HISTORY_START → today). It's a ~40 MB re-pull from IEM's cache,
costing ~30 seconds; the bandwidth is negligible and correctness wins.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from lib.watchers.base import Watcher, run_subprocess

STATIONS = ["KLGA", "KATL", "KDAL", "KSEA", "KORD", "KMIA",
            "KLAX", "KSFO", "KHOU", "KAUS", "KDEN"]

# Start of our historical coverage — pull full range every time to avoid
# --force overwriting historical data.
NBS_HISTORY_START = date(2025, 11, 30)


class NBSWatcher(Watcher):
    def __init__(self, interval_seconds: int = 600):
        super().__init__(name="nbs", interval_seconds=interval_seconds, jitter_seconds=30)

    async def poll(self) -> dict:
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
            None, lambda: run_subprocess(cmd, timeout=300)
        )
        if rc != 0:
            raise RuntimeError(f"iem_mos download (NBS) failed: {err[-500:]}")

        cmd2 = ["uv", "run", "python", "scripts/iem_mos/transform.py"]
        rc2, _, err2 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd2, timeout=120)
        )
        if rc2 != 0:
            raise RuntimeError(f"iem_mos transform failed: {err2[-500:]}")

        return {"through": today.isoformat(), "stations": len(STATIONS)}

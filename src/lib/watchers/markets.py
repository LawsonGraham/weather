"""Polymarket markets catalog watcher.

New daily-temperature markets are created ~once per day on Polymarket.
The download itself is multi-step and takes 1-2 minutes, so probing
every 60s would waste bandwidth. Instead we use a time-based throttle:
fetch if >FETCH_INTERVAL_MIN since the last successful fetch.

CHEAP PROBE: check `state.last_fetch_success_at` — no network call.

HEAVY FETCH: slugs catalog → per-slug Gamma/Goldsky pulls → transform
to parquet.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lib.watchers.base import Watcher, run_subprocess

FETCH_INTERVAL_MIN = 60  # rebuild catalog at most once per hour


class MarketsWatcher(Watcher):
    def __init__(self, interval_seconds: int = 10):
        super().__init__(name="markets", interval_seconds=interval_seconds,
                        jitter_seconds=3)

    async def has_new_data(self) -> bool:
        last_ok = self.state.last_fetch_success_at
        if not last_ok:
            return True  # never fetched
        elapsed = datetime.now(UTC) - datetime.fromisoformat(last_ok)
        return elapsed > timedelta(minutes=FETCH_INTERVAL_MIN)

    async def fetch_new_data(self) -> dict:
        import asyncio
        loop = asyncio.get_running_loop()

        steps = [
            ("slugs", ["uv", "run", "python",
                       "scripts/polymarket_weather_slugs/download.py"], 120),
            ("markets", ["uv", "run", "python",
                         "scripts/polymarket_weather/download.py"], 300),
            ("transform", ["uv", "run", "python",
                           "scripts/polymarket_weather/transform.py"], 180),
        ]
        for label, cmd, timeout in steps:
            rc, _, err = await loop.run_in_executor(
                None, lambda c=cmd, t=timeout: run_subprocess(c, timeout=t),
            )
            if rc != 0:
                raise RuntimeError(f"markets {label} failed: {err[-500:]}")

        return {"stages_run": [s[0] for s in steps]}

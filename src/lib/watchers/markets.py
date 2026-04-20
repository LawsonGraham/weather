"""Polymarket markets catalog watcher.

Polymarket creates new daily-temperature markets every day (one city-day per
11-bucket ladder). We need the fresh markets.parquet so the strategy can find
the current (city, market_date) → slug / yes_token_id / no_token_id mappings.

Polls every 15 minutes. Re-runs the existing download + transform scripts
which pull from Gamma API and walk the slug catalog.
"""
from __future__ import annotations

from lib.watchers.base import Watcher, run_subprocess


class MarketsWatcher(Watcher):
    def __init__(self, interval_seconds: int = 900):
        super().__init__(name="markets", interval_seconds=interval_seconds,
                        jitter_seconds=30)

    async def poll(self) -> dict:
        import asyncio

        loop = asyncio.get_running_loop()

        # 1. Refresh slug catalog (Polymarket slug metadata)
        cmd1 = ["uv", "run", "python", "scripts/polymarket_weather_slugs/download.py"]
        rc1, _, err1 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd1, timeout=120)
        )
        if rc1 != 0:
            raise RuntimeError(f"polymarket_weather_slugs download failed: {err1[-500:]}")

        # 2. Per-slug Gamma + Goldsky pulls
        cmd2 = ["uv", "run", "python", "scripts/polymarket_weather/download.py"]
        rc2, _, err2 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd2, timeout=300)
        )
        if rc2 != 0:
            raise RuntimeError(f"polymarket_weather download failed: {err2[-500:]}")

        # 3. Transform to parquet
        cmd3 = ["uv", "run", "python", "scripts/polymarket_weather/transform.py"]
        rc3, _, err3 = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd3, timeout=180)
        )
        if rc3 != 0:
            raise RuntimeError(f"polymarket_weather transform failed: {err3[-500:]}")

        return {"stage": "catalog+markets+transform"}

"""Features-rebuild watcher.

Runs after weather/market data has been refreshed to rebuild
`data/processed/backtest_v3/features.parquet` — the unified per-(station,date)
feature matrix the strategy reads.

The rebuild is fast (seconds), so we schedule it every 10 minutes. Runs in
lockstep with the weather watchers so it picks up their latest outputs.
"""
from __future__ import annotations

from lib.watchers.base import Watcher, run_subprocess


class FeaturesWatcher(Watcher):
    def __init__(self, interval_seconds: int = 600):
        super().__init__(name="features", interval_seconds=interval_seconds,
                        jitter_seconds=180)

    async def poll(self) -> dict:
        import asyncio
        loop = asyncio.get_running_loop()

        cmd = ["uv", "run", "python", "notebooks/experiments/backtest-v3/build_features.py"]
        rc, out, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=300)
        )
        if rc != 0:
            raise RuntimeError(f"build_features failed: {err[-500:]}")

        # Parse stats from stdout if possible
        for line in out.splitlines()[-10:]:
            if "IS:" in line or "OOS:" in line:
                break
        return {"rebuilt": True}

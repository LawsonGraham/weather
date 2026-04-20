"""Features-rebuild watcher.

Rebuilds `data/processed/backtest_v3/features.parquet` when any input
parquet becomes newer than the features output.

CHEAP PROBE: compare mtimes. Zero network calls, effectively free.

HEAVY FETCH: run notebooks/experiments/backtest-v3/build_features.py
(~seconds on our data sizes).
"""
from __future__ import annotations

from lib.watchers.base import REPO_ROOT, Watcher, run_subprocess

FEATURES_PATH = REPO_ROOT / "data" / "processed" / "backtest_v3" / "features.parquet"

# Upstream source directories that feed features.parquet. Any parquet under
# these that's newer than features.parquet → rebuild.
INPUT_DIRS = [
    REPO_ROOT / "data" / "processed" / "iem_mos",
    REPO_ROOT / "data" / "raw" / "hrrr",
    REPO_ROOT / "data" / "processed" / "iem_metar",
    REPO_ROOT / "data" / "processed" / "polymarket_weather",
]


class FeaturesWatcher(Watcher):
    def __init__(self, interval_seconds: int = 10):
        super().__init__(name="features", interval_seconds=interval_seconds,
                        jitter_seconds=7)

    async def has_new_data(self) -> bool:
        if not FEATURES_PATH.exists():
            return True
        features_mtime = FEATURES_PATH.stat().st_mtime
        for d in INPUT_DIRS:
            if not d.exists():
                continue
            for p in d.rglob("*.parquet"):
                if p.stat().st_mtime > features_mtime:
                    return True
        return False

    async def fetch_new_data(self) -> dict:
        import asyncio
        loop = asyncio.get_running_loop()
        cmd = ["uv", "run", "python",
               "notebooks/experiments/backtest-v3/build_features.py"]
        rc, _, err = await loop.run_in_executor(
            None, lambda: run_subprocess(cmd, timeout=300),
        )
        if rc != 0:
            raise RuntimeError(f"build_features failed: {err[-500:]}")
        return {"rebuilt": True}

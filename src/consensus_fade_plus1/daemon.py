"""Strategy daemon — runs all watchers in parallel + (future) strategy engine.

Today:
    - Starts all weather + market watchers so data/processed/... stays fresh
    - Prints structured status per watcher on each poll
    - Graceful shutdown on SIGINT / SIGTERM

Planned (not implemented yet — see STRATEGY.md §TODO):
    - Live order book subscription (WSS)
    - Continuous order placement / replacement as retail flow hits
    - User-channel fill notifications
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from lib.watchers import (
    FeaturesWatcher,
    GFSWatcher,
    HRRRWatcher,
    MarketsWatcher,
    METARWatcher,
    NBSWatcher,
    run_watchers,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_default_watchers() -> list:
    """Return the default set of watchers for live operation."""
    return [
        NBSWatcher(interval_seconds=600),      # 10 min
        GFSWatcher(interval_seconds=600),      # 10 min
        HRRRWatcher(interval_seconds=600),     # 10 min (HRRR cycle 1.5h behind)
        METARWatcher(interval_seconds=300),    # 5 min (catches SPECIs)
        MarketsWatcher(interval_seconds=900),  # 15 min (Polymarket markets)
        FeaturesWatcher(interval_seconds=600), # 10 min (rebuilds features.parquet)
    ]


async def run() -> int:
    watchers = build_default_watchers()
    await run_watchers(watchers)
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    import sys
    sys.exit(main())

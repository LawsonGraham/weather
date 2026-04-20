"""Weather + market data watchers — run as persistent background tasks.

Each watcher polls an upstream source on a fixed cadence, detects new data,
persists it to the canonical parquet location, and records state so the
strategy engine can tell how fresh the data is.

Watchers are composable: start them individually for testing, or run them
all together via `cfp daemon`.

Public API:
    - Watcher: base class (subclass to implement poll logic)
    - WatcherState: serializable state (last run, next run, last success, errors)
    - NBSWatcher, GFSWatcher, HRRRWatcher, METARWatcher, MarketsWatcher
    - run_watchers(watchers, *, stop_event): asyncio orchestrator
"""
from lib.watchers.base import Watcher, WatcherState, run_watchers
from lib.watchers.features import FeaturesWatcher
from lib.watchers.gfs import GFSWatcher
from lib.watchers.hrrr import HRRRWatcher
from lib.watchers.markets import MarketsWatcher
from lib.watchers.metar import METARWatcher
from lib.watchers.nbs import NBSWatcher

__all__ = [
    "FeaturesWatcher",
    "GFSWatcher",
    "HRRRWatcher",
    "METARWatcher",
    "MarketsWatcher",
    "NBSWatcher",
    "Watcher",
    "WatcherState",
    "run_watchers",
]

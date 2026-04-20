"""Polymarket markets catalog watcher.

New daily-temperature markets are created ~once per day on Polymarket.
We fetch if >FETCH_INTERVAL_MIN since the last successful fetch.

CHEAP PROBE: check `state.last_fetch_success_at` — no network call.

HEAVY FETCH: slugs catalog → per-slug Gamma/Goldsky pulls → transform
to parquet. Filtered to our 12 CONUS cities — without that filter the
downloader walks all ~15K global daily-temperature slugs (Tel Aviv,
Seoul, London, etc.), taking hours even with cache hits. The trading
strategy only cares about US cities, so we skip the rest at the slug
selection stage.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lib.watchers.base import Watcher, run_subprocess

FETCH_INTERVAL_MIN = 60  # rebuild catalog at most once per hour

# Cities matching our 12 airport stations (see NBSWatcher.STATIONS etc).
# Polymarket's slug-catalog city names are title-case with spaces.
CITIES = [
    "New York City", "Atlanta", "Dallas", "Seattle", "Chicago", "Miami",
    "Los Angeles", "San Francisco", "Houston", "Austin", "Denver",
]


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

        cities_arg = ",".join(CITIES)
        # Generous per-step timeouts: the FIRST run after a long gap walks
        # thousands of new slugs at ~0.5-2s each and legitimately takes 10+
        # minutes. Steady-state runs are seconds. Our watcher's
        # time-based throttle (FETCH_INTERVAL_MIN=60) already prevents
        # excessive refreshes, so the timeout is a fail-safe, not a normal
        # operating bound.
        steps = [
            # --refresh bypasses the 10-day gamma-response cache in the slug
            # downloader. Without it, the watcher happily re-uses stale
            # cached responses from weeks ago, producing a markets.parquet
            # that silently lacks any newly-listed days' markets. This was
            # the root cause of an "all 0 tradeable markets" bug.
            ("slugs", ["uv", "run", "python",
                       "scripts/polymarket_weather_slugs/download.py",
                       "--refresh"], 300),
            ("markets", ["uv", "run", "python",
                         "scripts/polymarket_weather/download.py",
                         "--cities", cities_arg], 1200),
            # Force transform to rebuild; without --force it no-ops based on
            # its own manifest even when markets.parquet is stale relative
            # to raw Gamma data.
            ("transform", ["uv", "run", "python",
                           "scripts/polymarket_weather/transform.py",
                           "--force"], 300),
        ]
        for label, cmd, timeout in steps:
            rc, _, err = await loop.run_in_executor(
                None, lambda c=cmd, t=timeout: run_subprocess(c, timeout=t),
            )
            if rc != 0:
                raise RuntimeError(f"markets {label} failed: {err[-500:]}")

        return {"stages_run": [s[0] for s in steps], "cities": len(CITIES)}

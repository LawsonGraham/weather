#!/usr/bin/env python3
"""Pull polymarket_weather + polymarket_prices_history for all 10 new US cities.

Runs sequentially (Goldsky rate limits recommend serial). Skips NYC
which is already pulled.

Phase 1: polymarket_weather/download.py --city <C> for each city
Phase 2: polymarket_weather/transform.py (once, picks up all cities)
Phase 3: polymarket_prices_history/download.py --city <C> for each city
Phase 4: polymarket_prices_history/transform.py (once)

Usage:
    uv run python scripts/pull_all_us_cities.py
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import UTC, datetime

CITIES = [
    "Atlanta",
    "Dallas",
    "Seattle",
    "Chicago",
    "Miami",
    "Los Angeles",
    "San Francisco",
    "Houston",
    "Austin",
    "Denver",
]

log = logging.getLogger("pull_all")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)sZ [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def run(cmd: list[str], label: str) -> int:
    log.info(f">>> {label}: {' '.join(cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.time() - t0
    log.info(f"<<< {label}: exit={result.returncode} elapsed={elapsed:.0f}s")
    return result.returncode


def main() -> int:
    t0_global = time.time()

    # Phase 1: polymarket_weather for each city
    log.info("=" * 60)
    log.info("PHASE 1: polymarket_weather downloads (10 cities)")
    log.info("=" * 60)
    for i, city in enumerate(CITIES, 1):
        log.info(f"--- [{i}/{len(CITIES)}] {city} ---")
        rc = run(
            ["uv", "run", "python", "scripts/polymarket_weather/download.py",
             "--city", city],
            f"polymarket_weather/{city}",
        )
        if rc != 0:
            log.error(f"polymarket_weather FAILED for {city} (exit {rc}), continuing...")

    # Phase 2: transform all polymarket_weather at once
    log.info("=" * 60)
    log.info("PHASE 2: polymarket_weather transform (all cities)")
    log.info("=" * 60)
    run(
        ["uv", "run", "python", "scripts/polymarket_weather/transform.py", "--force"],
        "polymarket_weather/transform",
    )

    # Phase 3: polymarket_prices_history for each city
    log.info("=" * 60)
    log.info("PHASE 3: polymarket_prices_history downloads (10 cities)")
    log.info("=" * 60)
    for i, city in enumerate(CITIES, 1):
        log.info(f"--- [{i}/{len(CITIES)}] {city} ---")
        rc = run(
            ["uv", "run", "python", "scripts/polymarket_prices_history/download.py",
             "--city", city],
            f"polymarket_prices_history/{city}",
        )
        if rc != 0:
            log.error(f"polymarket_prices_history FAILED for {city} (exit {rc}), continuing...")

    # Phase 4: transform all polymarket_prices_history at once
    log.info("=" * 60)
    log.info("PHASE 4: polymarket_prices_history transform (all cities)")
    log.info("=" * 60)
    run(
        ["uv", "run", "python", "scripts/polymarket_prices_history/transform.py"],
        "polymarket_prices_history/transform",
    )

    elapsed = time.time() - t0_global
    log.info(f"ALL DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Base class + orchestrator for data watchers.

Watchers split their work into two methods:

    has_new_data()   — cheap check (HEAD / tiny GET / local mtime)
    fetch_new_data() — heavy pull (full download + transform)

The orchestrator calls `has_new_data()` on a fast cadence (default 60s) and
only invokes `fetch_new_data()` when the probe says upstream has moved.
That keeps the daemon responsive (seconds of staleness, not minutes) while
avoiding the ~30-40MB / 30s re-pull each time on unchanged sources.

Every log line is prefixed with a UTC ISO timestamp so you can trace
timing of probes and fetches. State lives at
`data/processed/watchers/<name>.state.json` and persists across restarts.
"""
from __future__ import annotations

import asyncio
import json
import signal
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = REPO_ROOT / "data" / "processed" / "watchers"


def log(component: str, msg: str) -> None:
    """Timestamped stdout log. Used by watchers + orchestrator + CLI."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{component}] {msg}", flush=True)


@dataclass
class WatcherState:
    """Persisted per-watcher state.

    Split into probe-level (every tick) and fetch-level (only on change).
    """
    name: str

    # Probe — runs every `interval` seconds.
    last_probe_at: str | None = None
    last_probe_result: bool | None = None   # True = had new data, False = skip
    total_probes: int = 0

    # Fetch — runs only when probe says there's new data.
    last_fetch_at: str | None = None
    last_fetch_success_at: str | None = None
    total_fetches: int = 0
    total_fetch_successes: int = 0
    total_fetch_failures: int = 0

    # Error bookkeeping.
    last_error: str | None = None
    last_error_at: str | None = None
    consecutive_failures: int = 0

    # Free-form detail from last successful fetch (e.g. through_date, stations).
    last_detail: dict = field(default_factory=dict)

    # Backwards-compat alias used by `cfp watchers` — points at last_fetch_success_at.
    @property
    def last_success_at(self) -> str | None:
        return self.last_fetch_success_at

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, default=str))

    @classmethod
    def load_or_new(cls, name: str, path: Path) -> WatcherState:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                # Tolerate old schema — drop unknown keys instead of crashing.
                valid = {f for f in cls.__dataclass_fields__}
                return cls(**{k: v for k, v in data.items() if k in valid})
            except Exception:
                pass
        return cls(name=name)


class Watcher(ABC):
    """Subclass and implement the two methods.

    Parameters:
        name: identifier for logging + state file
        interval_seconds: probe cadence (default 60s)
        jitter_seconds: randomize start (prevents all watchers firing at once)
    """

    def __init__(self, name: str, interval_seconds: int = 60,
                 jitter_seconds: int = 0):
        self.name = name
        self.interval = interval_seconds
        self.jitter = jitter_seconds
        self.state_path = STATE_DIR / f"{name}.state.json"
        self.state = WatcherState.load_or_new(name, self.state_path)

    @abstractmethod
    async def has_new_data(self) -> bool:
        """Cheap probe: did upstream change since last fetch? No side effects.

        Should take <5s. Typical implementations:
          - HEAD request to a known URL
          - Tiny GET of 1 station / 1 day, parse max timestamp, compare to local
          - `os.path.getmtime` comparison on local files
        """
        ...

    @abstractmethod
    async def fetch_new_data(self) -> dict:
        """Heavy fetch: download + transform. Returns a detail dict (logged).

        Raise any exception to mark this fetch as failed.
        """
        ...

    async def run(self, stop_event: asyncio.Event) -> None:
        """Main loop. Probes every `interval` seconds, fetches on change."""
        import random
        if self.jitter:
            await asyncio.sleep(random.uniform(0, self.jitter))

        while not stop_event.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval)
                return  # stop_event fired
            except TimeoutError:
                pass  # normal — next tick

    async def _tick(self) -> None:
        """One probe + optional fetch cycle. Never raises."""
        self.state.last_probe_at = datetime.now(UTC).isoformat()
        self.state.total_probes += 1

        probe_start = time.monotonic()
        try:
            has_new = await self.has_new_data()
        except Exception as e:
            self._record_error(e, during="probe")
            self.state.save(self.state_path)
            return
        probe_dur = time.monotonic() - probe_start

        self.state.last_probe_result = has_new
        if not has_new:
            log(self.name, f"probe: no new data ({probe_dur:.2f}s)")
            self.state.save(self.state_path)
            return

        log(self.name, f"probe: NEW DATA — fetching ({probe_dur:.2f}s)")
        fetch_start = time.monotonic()
        self.state.last_fetch_at = datetime.now(UTC).isoformat()
        self.state.total_fetches += 1
        try:
            detail = await self.fetch_new_data()
            fetch_dur = time.monotonic() - fetch_start
            self.state.last_fetch_success_at = datetime.now(UTC).isoformat()
            self.state.total_fetch_successes += 1
            self.state.consecutive_failures = 0
            self.state.last_error = None
            self.state.last_error_at = None
            self.state.last_detail = detail or {}
            log(self.name, f"fetch OK in {fetch_dur:.1f}s: {detail}")
        except Exception as e:
            self._record_error(e, during="fetch")
        finally:
            self.state.save(self.state_path)

    def _record_error(self, e: Exception, *, during: str) -> None:
        self.state.last_error = f"[{during}] {type(e).__name__}: {e}"
        self.state.last_error_at = datetime.now(UTC).isoformat()
        self.state.consecutive_failures += 1
        if during == "fetch":
            self.state.total_fetch_failures += 1
        log(self.name, f"{during.upper()} FAIL "
            f"(#{self.state.consecutive_failures}): {e}")
        if self.state.consecutive_failures <= 3:
            traceback.print_exc()


async def run_watchers(watchers: list[Watcher],
                       stop_event: asyncio.Event | None = None) -> None:
    """Run all watchers concurrently. SIGINT/SIGTERM triggers clean shutdown."""
    if stop_event is None:
        stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _signal_handler():
        log("daemon", "shutdown signal received, stopping watchers...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _signal_handler())

    log("daemon", f"starting {len(watchers)} watcher(s) "
        f"(probe every ~{watchers[0].interval}s, fetch on change): "
        f"{[w.name for w in watchers]}")

    tasks = [asyncio.create_task(w.run(stop_event), name=w.name) for w in watchers]

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        log("daemon", "all watchers stopped")


def run_subprocess(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    import subprocess
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=REPO_ROOT,
    )
    return result.returncode, result.stdout, result.stderr

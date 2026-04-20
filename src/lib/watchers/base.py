"""Base class + orchestrator for data watchers.

Each watcher runs on a fixed poll interval. On each tick it calls `poll()`
(subclass-implemented), catches any exception, records the outcome in its
state, and sleeps until the next tick.

State lives at `data/processed/watchers/<watcher_name>.state.json` and
persists across restarts so a freshly-started daemon knows when the last
successful run was.
"""
from __future__ import annotations

import asyncio
import json
import signal
import traceback
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = REPO_ROOT / "data" / "processed" / "watchers"


@dataclass
class WatcherState:
    """Persisted per-watcher state."""
    name: str
    last_poll_at: str | None = None  # ISO UTC
    last_success_at: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    consecutive_failures: int = 0
    total_polls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_detail: dict = field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, default=str))

    @classmethod
    def load_or_new(cls, name: str, path: Path) -> WatcherState:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return cls(**data)
            except Exception:
                pass
        return cls(name=name)


class Watcher(ABC):
    """Subclass and implement `poll()`.

    Parameters:
        name: identifier for logging + state file
        interval_seconds: how often to call poll()
        jitter_seconds: randomize start (prevents all watchers firing at once)
    """

    def __init__(self, name: str, interval_seconds: int, jitter_seconds: int = 0):
        self.name = name
        self.interval = interval_seconds
        self.jitter = jitter_seconds
        self.state_path = STATE_DIR / f"{name}.state.json"
        self.state = WatcherState.load_or_new(name, self.state_path)

    @abstractmethod
    async def poll(self) -> dict:
        """Perform one poll cycle. Return a detail dict (logged + persisted).

        Raise any exception to mark this poll as failed. The orchestrator
        handles retries (delay until next interval); subclasses don't need
        to implement backoff.
        """
        ...

    async def run(self, stop_event: asyncio.Event) -> None:
        """Main loop. Polls every `interval` seconds until stop_event is set."""
        import random
        if self.jitter:
            await asyncio.sleep(random.uniform(0, self.jitter))  # noqa: S311

        while not stop_event.is_set():
            poll_start = datetime.now(UTC)
            self.state.last_poll_at = poll_start.isoformat()
            self.state.total_polls += 1

            try:
                detail = await self.poll()
                self.state.last_success_at = datetime.now(UTC).isoformat()
                self.state.consecutive_failures = 0
                self.state.total_successes += 1
                self.state.last_error = None
                self.state.last_error_at = None
                self.state.last_detail = detail or {}
                print(f"[{self.name}] ok: {detail}")
            except Exception as e:
                self.state.last_error = f"{type(e).__name__}: {e}"
                self.state.last_error_at = datetime.now(UTC).isoformat()
                self.state.consecutive_failures += 1
                self.state.total_failures += 1
                print(f"[{self.name}] FAIL (#{self.state.consecutive_failures}): {e}")
                if self.state.consecutive_failures <= 3:
                    traceback.print_exc()

            self.state.save(self.state_path)

            # Sleep, but wake if stop_event is set
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval)
                return
            except asyncio.TimeoutError:
                pass  # normal — time for next poll


async def run_watchers(watchers: list[Watcher],
                       stop_event: asyncio.Event | None = None) -> None:
    """Run all watchers concurrently. SIGINT/SIGTERM triggers clean shutdown."""
    if stop_event is None:
        stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _signal_handler():
        print("\n[daemon] shutdown signal received, stopping watchers...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _signal_handler())

    print(f"[daemon] starting {len(watchers)} watchers: "
          f"{[w.name for w in watchers]}")

    tasks = [asyncio.create_task(w.run(stop_event), name=w.name) for w in watchers]

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        print("[daemon] all watchers stopped")


def run_subprocess(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    import subprocess
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=REPO_ROOT,
    )
    return result.returncode, result.stdout, result.stderr

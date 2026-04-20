"""Persistence for the Consensus-Fade +1 strategy.

Two append-only JSONL writers, rotated daily at UTC midnight:

  LedgerWriter        → data/processed/cfp_ledger/YYYY-MM-DD.jsonl
  BookSnapshotWriter  → data/processed/cfp_book_snapshots/YYYY-MM-DD.jsonl

Why JSONL: trivial to read (tail, jq, pandas.read_json(lines=True)),
append-only (crash-safe — partial lines can be skipped by readers),
and human-inspectable when debugging.

Both writers open files lazily — they're cheap to instantiate in the
strategy's `__init__`, and won't touch disk until something is written.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = REPO_ROOT / "data" / "processed" / "cfp_ledger"
BOOK_DIR = REPO_ROOT / "data" / "processed" / "cfp_book_snapshots"


class _DailyJSONLWriter:
    """Append JSONL to `<dir>/YYYY-MM-DD.jsonl`, rotating at UTC midnight."""

    def __init__(self, dir_path: Path):
        self.dir_path = dir_path
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self._current_date: date | None = None
        self._fh = None

    def write(self, record: dict[str, Any]) -> None:
        today = datetime.now(UTC).date()
        if self._current_date != today:
            self._rotate(today)
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def _rotate(self, new_date: date) -> None:
        if self._fh is not None:
            self._fh.close()
        path = self.dir_path / f"{new_date.isoformat()}.jsonl"
        self._fh = path.open("a", encoding="utf-8")
        self._current_date = new_date

    @property
    def path(self) -> Path | None:
        """Path to the current day's file (after first write)."""
        if self._current_date is None:
            return None
        return self.dir_path / f"{self._current_date.isoformat()}.jsonl"

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class LedgerWriter(_DailyJSONLWriter):
    """Logs every order-lifecycle event the strategy sees.

    One line per event. Fields vary by event_type, but every record has:
      ts_utc       ISO 8601 UTC timestamp
      event_type   submitted|accepted|filled|canceled|rejected|session_*
    """

    def __init__(self):
        super().__init__(LEDGER_DIR)

    def log(self, event_type: str, **fields: Any) -> None:
        record = {
            "ts_utc": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            **fields,
        }
        self.write(record)


class BookSnapshotWriter(_DailyJSONLWriter):
    """Writes periodic L2 order-book snapshots for subscribed instruments.

    One line per (snapshot_time x instrument). Fields:
      ts_utc         ISO 8601 UTC timestamp
      instrument_id  Polymarket instrument string
      bids           [[price, size], ...] top-N levels
      asks           [[price, size], ...] top-N levels
    """

    def __init__(self):
        super().__init__(BOOK_DIR)

    def snapshot(self, instrument_id: str,
                 bids: list[tuple[float, float]],
                 asks: list[tuple[float, float]]) -> None:
        record = {
            "ts_utc": datetime.now(UTC).isoformat(),
            "instrument_id": instrument_id,
            "bids": [[float(p), float(s)] for p, s in bids],
            "asks": [[float(p), float(s)] for p, s in asks],
        }
        self.write(record)

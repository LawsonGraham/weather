"""Live order book recorder for Polymarket via the CLOB WebSocket.

Subscribes to Polymarket's market WebSocket for the NYC daily-temperature
slugs (or other configured weather slugs), and warehouses every received
message to disk as JSONL partitioned by slug and hour.

Output layout:
    data/raw/polymarket_book/MANIFEST.json
    data/raw/polymarket_book/recorder.log
    data/raw/polymarket_book/<slug>/YYYY-MM-DD-HH.jsonl
        — append-only JSONL, one msg per line, msg type: book / price_change /
          tick_size_change / last_trade_price

Why JSONL not parquet at this stage:
    Stream-write append is trivial with JSONL. We can transform to parquet
    later (transform.py) once enough data accumulates.

WebSocket protocol (Polymarket CLOB):
    URL: wss://ws-subscriptions-clob.polymarket.com/ws/market
    Subscribe message: {"type": "MARKET", "assets_ids": ["yes_tid", "no_tid", ...]}
    Receives JSON messages of types:
      - "book"             — full L2 snapshot {market, asset_id, bids[], asks[], timestamp}
      - "price_change"     — incremental update
      - "tick_size_change" — minimum price increment changed
      - "last_trade_price" — a trade just happened

Reconnect strategy: exponential backoff (1s → 60s) with full re-subscribe.

Usage:
    uv run python scripts/polymarket_book/download.py
        [--city "New York City"]   # default
        [--include-closed]         # also subscribe to closed markets (won't update)
        [--max-slugs N]            # cap subscription set
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import websockets

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "polymarket_book"
DESCRIPTION = (
    "Polymarket CLOB WebSocket book + last_trade_price + price_change "
    "stream, persisted as JSONL partitioned by slug + hour. Live recording "
    "only — Polymarket does not publish historical book snapshots."
)
SCRIPT_VERSION = 1

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DEFAULT_CITY = "New York City"
DEFAULT_TAG = "Daily Temperature"

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME

# --- logging --------------------------------------------------------------- #

log = logging.getLogger(SOURCE_NAME)


def _setup_logging() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RAW_DIR / "recorder.log"
    fmt = "%(asctime)sZ [%(levelname)s] %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")
    file_h = logging.FileHandler(log_path, mode="a")
    file_h.setFormatter(formatter)
    stream_h = logging.StreamHandler(sys.stdout)
    stream_h.setFormatter(formatter)
    log.handlers.clear()
    log.addHandler(file_h)
    log.addHandler(stream_h)
    log.setLevel(logging.INFO)


# --- catalog --------------------------------------------------------------- #


def load_open_slugs(city: str | None, include_closed: bool, max_slugs: int | None) -> list[dict]:
    """Read open daily-temp slugs from the local processed parquet.

    If city is None or 'all', loads slugs for ALL cities.
    """
    con = duckdb.connect()
    where = ["weather_tags ILIKE '%Daily Temperature%'",
             "yes_token_id IS NOT NULL", "no_token_id IS NOT NULL"]
    if city and city.lower() != "all":
        where.append(f"city = '{city}'")
    if not include_closed:
        where.append("closed = false")
    where_sql = " AND ".join(where)
    # Subscribe to near-resolution slugs first (today / tomorrow) — those are
    # the most actively traded. ASC returns future slugs with ~zero activity.
    today_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    rows = con.execute(f"""
        SELECT slug, condition_id, yes_token_id, no_token_id, end_date
        FROM 'data/processed/polymarket_weather/markets.parquet'
        WHERE {where_sql}
        ORDER BY ABS(DATE_DIFF('day', CAST('{today_iso}' AS DATE), CAST(end_date AS DATE))) ASC,
                 end_date ASC
        {"LIMIT " + str(max_slugs) if max_slugs else ""}
    """).fetchall()
    return [
        {"slug": r[0], "condition_id": r[1], "yes_token_id": r[2],
         "no_token_id": r[3], "end_date": str(r[4])}
        for r in rows
    ]


# --- file rotation --------------------------------------------------------- #


def _hour_path(slug: str, ts: datetime) -> Path:
    slug_dir = RAW_DIR / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    return slug_dir / ts.strftime("%Y-%m-%d-%H.jsonl")


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


# --- websocket subscription ------------------------------------------------ #


class BookRecorder:
    def __init__(self, slugs: list[dict]):
        self.slugs = slugs
        # token_id -> slug map for routing inbound messages
        self.token_to_slug: dict[str, str] = {}
        for s in slugs:
            self.token_to_slug[s["yes_token_id"]] = s["slug"]
            self.token_to_slug[s["no_token_id"]] = s["slug"]
        self.n_msgs = 0
        self.n_msgs_by_type: dict[str, int] = {}
        self.last_msg_time: datetime | None = None
        self.start_time = datetime.now(UTC)
        self.shutdown = False

    def all_token_ids(self) -> list[str]:
        return list(self.token_to_slug.keys())

    async def run(self) -> None:
        backoff = 1.0
        while not self.shutdown:
            try:
                log.info(f"connecting to {WS_URL} (subscription set: {len(self.slugs)} slugs / {len(self.token_to_slug)} tokens)")
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    sub = {"type": "MARKET", "assets_ids": self.all_token_ids()}
                    await ws.send(json.dumps(sub))
                    log.info(f"subscribed to {len(self.token_to_slug)} tokens, listening...")
                    backoff = 1.0  # reset on successful connect
                    async for raw in ws:
                        if self.shutdown:
                            break
                        await self._handle_message(raw)
            except websockets.exceptions.ConnectionClosed as e:
                log.warning(f"connection closed: {e}; reconnecting in {backoff:.0f}s")
            except asyncio.TimeoutError:
                log.warning(f"timeout; reconnecting in {backoff:.0f}s")
            except Exception as e:
                log.error(f"unexpected error: {e}; reconnecting in {backoff:.0f}s")
            if self.shutdown:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _handle_message(self, raw: Any) -> None:
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except Exception as e:
            log.warning(f"failed to parse message: {e}")
            return
        # Polymarket sometimes sends batches
        if isinstance(msg, list):
            for m in msg:
                self._persist(m)
        elif isinstance(msg, dict):
            self._persist(msg)

    def _persist(self, m: dict) -> None:
        # Route by asset_id. book/last_trade_price have it top-level; price_change
        # nests it inside price_changes[].asset_id (the top-level "market" is the
        # condition_id, which maps to two tokens so we can't route by it alone).
        token = m.get("asset_id") or ""
        if not token:
            pcs = m.get("price_changes") or []
            if pcs and isinstance(pcs, list) and isinstance(pcs[0], dict):
                token = pcs[0].get("asset_id") or ""
        slug = self.token_to_slug.get(token, "_unknown")
        now = datetime.now(UTC)
        # tag the record with the wall-clock receive time
        record = {"_received_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), **m}
        path = _hour_path(slug, now)
        _append_jsonl(path, record)
        self.n_msgs += 1
        msg_type = m.get("event_type") or m.get("type") or "_unknown"
        self.n_msgs_by_type[msg_type] = self.n_msgs_by_type.get(msg_type, 0) + 1
        self.last_msg_time = now
        if self.n_msgs % 100 == 0:
            log.info(f"  +{self.n_msgs} msgs received; types={self.n_msgs_by_type}")

    def stop(self) -> None:
        self.shutdown = True
        log.info("shutdown requested")


# --- manifest -------------------------------------------------------------- #


def write_manifest(slugs: list[dict]) -> None:
    manifest = {
        "manifest_version": 1,
        "source_name": SOURCE_NAME,
        "description": DESCRIPTION,
        "upstream": {"url": WS_URL,
                     "docs": "https://docs.polymarket.com/#websocket-api"},
        "script": {"path": f"scripts/{SOURCE_NAME}/download.py", "version": SCRIPT_VERSION},
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_slugs_subscribed": len(slugs),
        "slugs": [s["slug"] for s in slugs],
    }
    (RAW_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))


# --- main ------------------------------------------------------------------ #


def main() -> int:
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--city", default="all",
                    help="City filter, or 'all' for all US cities (default: all)")
    ap.add_argument("--include-closed", action="store_true")
    ap.add_argument("--max-slugs", type=int, default=None)
    args = ap.parse_args()

    _setup_logging()
    log.info(f"starting {SOURCE_NAME} recorder")

    slugs = load_open_slugs(args.city, args.include_closed, args.max_slugs)
    if not slugs:
        log.error("no open slugs to subscribe to; exiting")
        return 2
    log.info(f"loaded {len(slugs)} open slug(s) for {args.city}")
    write_manifest(slugs)

    recorder = BookRecorder(slugs)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal_handler(*_a):
        recorder.stop()
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            signal.signal(sig, _signal_handler)

    try:
        loop.run_until_complete(recorder.run())
    except (asyncio.CancelledError, KeyboardInterrupt):
        log.info("interrupted")
    finally:
        log.info(f"final stats: total={recorder.n_msgs}  by_type={recorder.n_msgs_by_type}")
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

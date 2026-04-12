"""Live ladder-BID arbitrage watchman.

Runs as a long-lived process alongside the recorder. Subscribes to the
Polymarket CLOB WebSocket, maintains a per-slug top-of-book state,
groups slugs by market-date, and whenever sum(live bids across one
date's ladder) exceeds 1.005, emits an alert to stdout + log.

Purely observational — no trades. Purpose:

  1. Validate the rate and persistence of ladder-BID arb events
     predicted by exp I (≈20/hour during the final 2h of resolution).
  2. Measure the latency from the triggering book change to the alert
     (how fast can we observe the arb?).
  3. Catch arbs that disappear quickly — if the sum reverts to < 1
     within 500 ms of being observed, someone else is executing.

Output:
    data/processed/polymarket_book_watchman/alerts.jsonl — one alert
      record per (event time + event slug + post-change sum)
    data/processed/polymarket_book_watchman/watchman.log

Design: single WS connection, same subscription set as the recorder
(open NYC daily-temp slugs ordered by proximity to today). For each
price_change / book message, update the (slug → best_bid) map, then
re-evaluate sum(live_bids) per market-date. If the sum crossed 1.005,
emit an alert.

The watchman is deliberately stateless across restarts — the live stream
is the source of truth; no backfill.
"""
from __future__ import annotations

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

SOURCE_NAME = "polymarket_book_watchman"

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DEFAULT_CITY = "New York City"
ALERT_THRESHOLD = 1.005

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data" / "processed" / SOURCE_NAME

log = logging.getLogger(SOURCE_NAME)


def _setup_logging() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / "watchman.log"
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


def load_open_nyc_slugs(city: str = DEFAULT_CITY) -> list[dict]:
    """Read open NYC daily-temp slugs + YES token IDs."""
    con = duckdb.connect()
    today_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    rows = con.execute(f"""
        SELECT slug, yes_token_id, no_token_id, end_date,
               regexp_extract(slug, 'nyc-on-([a-z]+-[0-9]+-[0-9]+)', 1) AS md
        FROM 'data/processed/polymarket_weather/markets.parquet'
        WHERE city = '{city}'
          AND weather_tags ILIKE '%Daily Temperature%'
          AND closed = false
          AND yes_token_id IS NOT NULL
          AND no_token_id IS NOT NULL
        ORDER BY ABS(DATE_DIFF('day', CAST('{today_iso}' AS DATE), CAST(end_date AS DATE))) ASC
    """).fetchall()
    return [{"slug": r[0], "yes_token_id": r[1], "no_token_id": r[2],
             "end_date": str(r[3]), "md": r[4]} for r in rows]


class Watchman:
    def __init__(self, slugs: list[dict]) -> None:
        self.slugs = slugs
        # Map token_id → (slug, md, side)
        self.token_map: dict[str, tuple[str, str, str]] = {}
        for s in slugs:
            self.token_map[s["yes_token_id"]] = (s["slug"], s["md"], "YES")
            self.token_map[s["no_token_id"]] = (s["slug"], s["md"], "NO")

        # YES-token top-of-book state: yes_token_id → (best_bid, best_ask, last_update)
        self.yes_state: dict[str, tuple[float, float, datetime]] = {}
        # Map slug → yes_token_id for faster ladder lookups
        self.slug_to_yes: dict[str, str] = {
            s["slug"]: s["yes_token_id"] for s in slugs
        }
        # Group slugs by market-date
        self.md_slugs: dict[str, list[str]] = {}
        for s in slugs:
            self.md_slugs.setdefault(s["md"], []).append(s["slug"])

        # Alert de-dup: md → last_alert_ts (avoid spamming the same sub-sec event)
        self.last_alert_at: dict[str, datetime] = {}
        self.alerts_file = OUT_DIR / "alerts.jsonl"
        self.n_alerts = 0
        self.n_msgs = 0
        self.start_time = datetime.now(UTC)
        self.shutdown = False

    def all_token_ids(self) -> list[str]:
        return list(self.token_map.keys())

    async def run(self) -> None:
        backoff = 1.0
        while not self.shutdown:
            try:
                log.info(f"connecting to WS (watching {len(self.slugs)} slugs "
                         f"across {len(self.md_slugs)} market-dates)")
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({
                        "type": "MARKET",
                        "assets_ids": self.all_token_ids()
                    }))
                    log.info(f"subscribed; threshold={ALERT_THRESHOLD}")
                    backoff = 1.0
                    async for raw in ws:
                        if self.shutdown:
                            break
                        await self._handle(raw)
            except websockets.exceptions.ConnectionClosed as e:
                log.warning(f"conn closed: {e}; reconnect in {backoff:.0f}s")
            except asyncio.TimeoutError:
                log.warning(f"timeout; reconnect in {backoff:.0f}s")
            except Exception as e:
                log.error(f"error: {e}; reconnect in {backoff:.0f}s")
            if self.shutdown:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _handle(self, raw: Any) -> None:
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except Exception:
            return
        if isinstance(msg, list):
            for m in msg:
                self._process(m)
        elif isinstance(msg, dict):
            self._process(msg)

    def _process(self, m: dict) -> None:
        self.n_msgs += 1
        et = m.get("event_type") or m.get("type") or ""
        now = datetime.now(UTC)

        affected_mds: set[str] = set()

        if et == "book":
            token = m.get("asset_id") or ""
            info = self.token_map.get(token)
            if not info or info[2] != "YES":
                return
            bids = m.get("bids") or []
            asks = m.get("asks") or []
            bid_prices = [float(b["price"]) for b in bids if "price" in b]
            ask_prices = [float(a["price"]) for a in asks if "price" in a]
            best_bid = max(bid_prices) if bid_prices else 0.0
            best_ask = min(ask_prices) if ask_prices else 1.0
            self.yes_state[token] = (best_bid, best_ask, now)
            affected_mds.add(info[1])

        elif et == "price_change":
            for pc in m.get("price_changes", []) or []:
                token = pc.get("asset_id") or ""
                info = self.token_map.get(token)
                if not info or info[2] != "YES":
                    continue
                bid = float(pc.get("best_bid") or 0)
                ask = float(pc.get("best_ask") or 1)
                self.yes_state[token] = (bid, ask, now)
                affected_mds.add(info[1])

        if not affected_mds:
            return

        for md in affected_mds:
            self._check_md(md, now)

        if self.n_msgs % 10000 == 0:
            log.info(f"  processed {self.n_msgs:,} msgs, {self.n_alerts} alerts")

    def _check_md(self, md: str, now: datetime) -> None:
        slugs = self.md_slugs.get(md, [])
        if len(slugs) < 10:
            return
        total_bid = 0.0
        n_live = 0
        stale = False
        for slug in slugs:
            yes_tok = self.slug_to_yes.get(slug)
            if not yes_tok:
                return
            state = self.yes_state.get(yes_tok)
            if state is None:
                # Missing at least one slug → can't form a complete snapshot
                return
            bid, _, updated_at = state
            if (now - updated_at).total_seconds() > 30:
                stale = True
            total_bid += bid
            if bid > 0.0001:
                n_live += 1

        if stale or total_bid < ALERT_THRESHOLD:
            return

        last_alert = self.last_alert_at.get(md)
        if last_alert and (now - last_alert).total_seconds() < 1.0:
            return  # dedupe sub-second chatter

        self.last_alert_at[md] = now
        self.n_alerts += 1

        per_bucket = [
            (slug,
             round(self.yes_state[self.slug_to_yes[slug]][0], 4),
             round(self.yes_state[self.slug_to_yes[slug]][1], 4))
            for slug in slugs
        ]
        alert = {
            "ts": now.isoformat().replace("+00:00", "Z"),
            "md": md,
            "n_buckets": len(slugs),
            "n_live": n_live,
            "sum_bid": round(total_bid, 4),
            "per_bucket": per_bucket,
        }
        with self.alerts_file.open("a") as f:
            f.write(json.dumps(alert) + "\n")
        log.info(f"  ALERT {md} sum_bid={total_bid:.4f} n_live={n_live} "
                 f"n_buckets={len(slugs)} (total alerts: {self.n_alerts})")

    def stop(self) -> None:
        self.shutdown = True
        log.info("shutdown requested")


def main() -> int:
    _setup_logging()
    log.info(f"starting {SOURCE_NAME}")
    slugs = load_open_nyc_slugs()
    if not slugs:
        log.error("no open slugs; exiting")
        return 2
    log.info(f"loaded {len(slugs)} open NYC slugs "
             f"(market-dates: {sorted(set(s['md'] for s in slugs))})")

    wm = Watchman(slugs)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handler(*_a):
        wm.stop()
        for t in asyncio.all_tasks(loop):
            t.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler)
        except NotImplementedError:
            signal.signal(sig, _handler)

    try:
        loop.run_until_complete(wm.run())
    except (asyncio.CancelledError, KeyboardInterrupt):
        log.info("interrupted")
    finally:
        log.info(f"final: {wm.n_msgs:,} msgs, {wm.n_alerts} alerts")
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

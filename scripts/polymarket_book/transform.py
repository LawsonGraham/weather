"""Transform polymarket_book JSONL stream → top-of-book parquet.

Reads every ``data/raw/polymarket_book/<slug>/*.jsonl`` and emits a
single parquet dataset of top-of-book quotes, one row per upstream
message that carries a best_bid / best_ask.

**Shortcut**: price_change messages already include ``best_bid`` and
``best_ask`` in every ``price_changes[]`` entry, so we don't need to
maintain full order-book state. For book snapshots we compute max(bids)
and min(asks) ourselves.

Output:
    data/processed/polymarket_book/tob/year=/month=/day=/part-0.parquet

Schema:
    received_at       TIMESTAMPTZ (our wall-clock)
    server_ts         TIMESTAMPTZ (upstream "timestamp" field, ms→tz)
    slug              VARCHAR
    asset_id          VARCHAR
    event_type        VARCHAR  -- 'book' | 'price_change'
    best_bid          DOUBLE
    best_ask          DOUBLE
    mid               DOUBLE   -- (bid+ask)/2 when both present
    spread            DOUBLE   -- ask - bid when both present
    n_bid_levels      INT      -- full depth count (NULL for price_change)
    n_ask_levels      INT      -- full depth count (NULL for price_change)

Idempotent: re-running overwrites the partitions.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

SOURCE_NAME = "polymarket_book"
SCRIPT_VERSION = 1

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
PROC_DIR = REPO_ROOT / "data" / "processed" / SOURCE_NAME

log = logging.getLogger(SOURCE_NAME)


def _setup_logging() -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PROC_DIR / "transform.log"
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


def _to_f(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_book(m: dict, slug: str) -> list[dict]:
    bids = m.get("bids") or []
    asks = m.get("asks") or []
    bid_prices = [_to_f(b.get("price")) for b in bids if _to_f(b.get("price")) is not None]
    ask_prices = [_to_f(a.get("price")) for a in asks if _to_f(a.get("price")) is not None]
    best_bid = max(bid_prices) if bid_prices else None
    best_ask = min(ask_prices) if ask_prices else None
    mid = (best_bid + best_ask) / 2 if (best_bid is not None and best_ask is not None) else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
    return [{
        "received_at": m.get("_received_at"),
        "server_ts_ms": _to_f(m.get("timestamp")),
        "slug": slug,
        "asset_id": m.get("asset_id") or "",
        "event_type": "book",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": spread,
        "n_bid_levels": len(bid_prices),
        "n_ask_levels": len(ask_prices),
    }]


def _parse_price_change(m: dict, slug: str) -> list[dict]:
    rows = []
    for pc in m.get("price_changes", []) or []:
        best_bid = _to_f(pc.get("best_bid"))
        best_ask = _to_f(pc.get("best_ask"))
        mid = (best_bid + best_ask) / 2 if (best_bid is not None and best_ask is not None) else None
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
        rows.append({
            "received_at": m.get("_received_at"),
            "server_ts_ms": _to_f(m.get("timestamp")),
            "slug": slug,
            "asset_id": pc.get("asset_id") or "",
            "event_type": "price_change",
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
            "spread": spread,
            "n_bid_levels": None,
            "n_ask_levels": None,
        })
    return rows


def collect_rows() -> list[dict]:
    rows: list[dict] = []
    slug_dirs = sorted([p for p in RAW_DIR.iterdir() if p.is_dir()])
    log.info(f"scanning {len(slug_dirs)} slug dirs")
    for sd in slug_dirs:
        slug = sd.name
        n_slug = 0
        for jf in sorted(sd.glob("*.jsonl")):
            with jf.open() as fh:
                for line in fh:
                    try:
                        m = json.loads(line)
                    except Exception:
                        continue
                    et = m.get("event_type") or m.get("type") or ""
                    if et == "book":
                        rows.extend(_parse_book(m, slug))
                        n_slug += 1
                    elif et == "price_change":
                        rows.extend(_parse_price_change(m, slug))
                        n_slug += 1
                    # last_trade_price and tick_size_change handled in future pass
        if n_slug:
            log.info(f"  {slug}: {n_slug:,} messages parsed")
    return rows


def write_partitioned(rows: list[dict]) -> None:
    if not rows:
        log.info("no rows to write")
        return
    out_dir = PROC_DIR / "tob"
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.register("rows_df", pa.Table.from_pylist(rows))
    con.execute("""
        CREATE OR REPLACE TEMP TABLE staged AS
        SELECT
            CAST(received_at AS TIMESTAMP WITH TIME ZONE) AS received_at,
            to_timestamp(server_ts_ms / 1000.0) AS server_ts,
            slug, asset_id, event_type,
            best_bid, best_ask, mid, spread,
            n_bid_levels, n_ask_levels,
            EXTRACT(year FROM CAST(received_at AS TIMESTAMP WITH TIME ZONE))::INT AS year,
            EXTRACT(month FROM CAST(received_at AS TIMESTAMP WITH TIME ZONE))::INT AS month,
            EXTRACT(day FROM CAST(received_at AS TIMESTAMP WITH TIME ZONE))::INT AS day
        FROM rows_df
    """)
    n = con.execute("SELECT COUNT(*) FROM staged").fetchone()[0]
    log.info(f"staged {n:,} rows")
    con.execute(f"""
        COPY (SELECT * FROM staged ORDER BY slug, received_at)
        TO '{out_dir.as_posix()}'
        (FORMAT PARQUET, PARTITION_BY (year, month, day), OVERWRITE_OR_IGNORE)
    """)
    log.info(f"wrote partitioned parquet to {out_dir.relative_to(REPO_ROOT)}")


def main() -> int:
    _setup_logging()
    log.info(f"transform {SOURCE_NAME} starting")
    t0 = datetime.now(UTC)
    rows = collect_rows()
    log.info(f"collected {len(rows):,} top-of-book records from JSONL stream")
    write_partitioned(rows)
    elapsed = (datetime.now(UTC) - t0).total_seconds()
    log.info(f"done in {elapsed:.1f}s: {len(rows):,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

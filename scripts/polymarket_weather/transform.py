#!/usr/bin/env python3
"""Transform polymarket_weather raw JSON to Parquet (markets + fills + prices).

Reads ``data/raw/polymarket_weather/{gamma,fills}/<slug>.json`` for every slug
selected from ``weather-market-slugs/polymarket.csv`` and produces three
artifacts under ``data/processed/polymarket_weather/``:

1. ``markets.parquet`` — flat, one row per market, ~80 columns.
2. ``fills/year=YYYY/month=MM/part-0.parquet`` — Hive-partitioned on the
   fill's UTC trade timestamp.  One row per OrderFilled event, with derived
   ``price`` / ``shares`` / ``usd`` / ``side`` / ``outcome`` columns.
3. ``prices/year=YYYY/month=MM/part-0.parquet`` — Hive-partitioned time-series.
   Dense forward-fill at ``--resolution-seconds`` granularity of
   ``yes_price`` / ``no_price`` plus cumulative fill counts and USD volume.

Usage:

    uv run python scripts/polymarket_weather/transform.py \\
        --city "New York City" --resolution-seconds 1

Flags:

    Standard (data-script contract):
        --force                 bypass "already complete" check
        --fresh                 wipe partial state (implies --force)
        --dry-run               print plan; do not mutate
        --verbose / -v          DEBUG log level

    Source-specific:
        --city NAME             filter slugs by exact city match (default: all)
        --slugs-file PATH       slug catalog CSV (default: weather-market-slugs/polymarket.csv)
        --slugs A,B,C           explicit comma-separated slug list (overrides --slugs-file)
        --limit N               process only the first N selected slugs
        --resolution-seconds N  prices table bucket size (default: 60)
        --skip-markets          skip markets.parquet stage
        --skip-fills            skip fills/ stage
        --skip-prices           skip prices/ stage

Self-contained: all helpers inlined, no shared utility module.  Follows the
data-script skill contract.
"""

# pandas + pyright false-positive pattern: type narrowing through filtered
# Series / DataFrame / groupby-reindex gives spurious union-type errors.
# Silenced for this file only; the code is correct at runtime.
# pyright: reportAttributeAccessIssue=false, reportReturnType=false, reportArgumentType=false

from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# --- metadata -------------------------------------------------------------- #

STEP_NAME = "polymarket_weather_parquet"
SOURCE_NAME = "polymarket_weather"  # raw input source
SCRIPT_VERSION = 1
DESCRIPTION = "Transform polymarket_weather raw JSON (gamma + subgraph fills) into Parquet (markets + fills + prices)."
REQUIRED_DISK_GIB = 5

# --- paths ----------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
GAMMA_DIR = RAW_DIR / "gamma"
FILLS_DIR = RAW_DIR / "fills"
PROCESSED_DIR = REPO_ROOT / "data" / "processed" / SOURCE_NAME
MARKETS_PATH = PROCESSED_DIR / "markets.parquet"
FILLS_ROOT = PROCESSED_DIR / "fills"
PRICES_ROOT = PROCESSED_DIR / "prices"
MANIFEST_PATH = PROCESSED_DIR / "MANIFEST.json"
LOG_PATH = PROCESSED_DIR / "transform.log"
DEFAULT_SLUGS_CSV = REPO_ROOT / "weather-market-slugs" / "polymarket.csv"
TARGET_REL = f"data/processed/{SOURCE_NAME}"


# --- logging --------------------------------------------------------------- #


class _UtcFormatter(logging.Formatter):
    def formatTime(  # noqa: N802 — override stdlib
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        return datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure_logging(log_path: Path, *, verbose: bool = False) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(STEP_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    fmt = _UtcFormatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- preconditions --------------------------------------------------------- #


def check_preconditions(log: logging.Logger) -> None:
    if not GAMMA_DIR.exists() or not FILLS_DIR.exists():
        raise SystemExit(
            f"raw input missing: {RAW_DIR}. run scripts/polymarket_weather/download.py first."
        )
    if not DEFAULT_SLUGS_CSV.exists():
        raise SystemExit(f"slug catalog missing: {DEFAULT_SLUGS_CSV}")
    usage = shutil.disk_usage(REPO_ROOT)
    avail_gib = usage.free / (1024**3)
    if avail_gib < REQUIRED_DISK_GIB:
        raise SystemExit(
            f"insufficient disk: need {REQUIRED_DISK_GIB} GiB, have {avail_gib:.1f} GiB"
        )
    log.info("disk ok: %.1f GiB free", avail_gib)


# --- manifest lifecycle ---------------------------------------------------- #


class TransformManifest(AbstractContextManager):
    def __init__(self, *, started_at: str, args: argparse.Namespace) -> None:
        self.started_at = started_at
        self.args = args
        self._completed = False
        self._log = logging.getLogger(STEP_NAME)

    @staticmethod
    def check_already_complete(path: Path, *, force: bool) -> bool:
        if not path.exists() or force:
            return False
        doc = json.loads(path.read_text())
        status = doc.get("transform", {}).get("status")
        if status == "complete":
            return True
        if status == "in_progress":
            raise SystemExit(
                f"manifest status 'in_progress' at {path}. another run may be active, or the "
                f"previous run crashed. investigate, then re-run with --force."
            )
        if status == "failed":
            raise SystemExit(
                f"previous run failed (see {path.parent}/transform.log). re-run with --force."
            )
        raise SystemExit(f"manifest at {path} has unexpected status: {status!r}")

    def _initial(self) -> dict[str, Any]:
        return {
            "manifest_version": 1,
            "source_name": STEP_NAME,
            "description": DESCRIPTION,
            "upstream": {"raw_dir": f"data/raw/{SOURCE_NAME}"},
            "script": {
                "path": "scripts/polymarket_weather/transform.py",
                "version": SCRIPT_VERSION,
            },
            "transform": {
                "started_at": self.started_at,
                "completed_at": None,
                "status": "in_progress",
                "inputs": {
                    "city": self.args.city,
                    "limit": self.args.limit,
                    "slugs_file": str(self.args.slugs_file.relative_to(REPO_ROOT))
                    if self.args.slugs_file
                    else None,
                    "explicit_slugs": bool(self.args.slugs),
                    "resolution_seconds": self.args.resolution_seconds,
                    "skip_markets": self.args.skip_markets,
                    "skip_fills": self.args.skip_fills,
                    "skip_prices": self.args.skip_prices,
                },
                "stats": {},
            },
            "target": {"raw_dir": TARGET_REL, "contents": []},
            "notes": "",
        }

    def _read(self) -> dict[str, Any]:
        return json.loads(MANIFEST_PATH.read_text())

    def _write(self, doc: dict[str, Any]) -> None:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(doc, indent=2, default=str) + "\n")

    def set_stat(self, key: str, value: Any) -> None:
        doc = self._read()
        doc["transform"]["stats"][key] = value
        self._write(doc)

    def complete(self, *, stats: dict[str, Any] | None = None) -> None:
        doc = self._read()
        doc["transform"]["completed_at"] = utc_now()
        doc["transform"]["status"] = "complete"
        if stats:
            doc["transform"]["stats"].update(stats)
        self._write(doc)
        self._completed = True

    def __enter__(self) -> TransformManifest:
        self._write(self._initial())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self._flip_failed(reason=f"{exc_type.__name__}: {exc_val}")
            return False
        if not self._completed:
            self._flip_failed(reason="complete() never called")
        return False

    def _flip_failed(self, *, reason: str) -> None:
        try:
            doc = self._read()
            doc["transform"]["status"] = "failed"
            doc["transform"]["completed_at"] = utc_now()
            doc["notes"] = (doc.get("notes") or "") + f"\nfailed: {reason}"
            self._write(doc)
            self._log.error("manifest marked failed: %s", reason)
        except Exception:
            pass


# --- slug selection -------------------------------------------------------- #


def select_slugs(
    slugs_file: Path, *, city: str | None, explicit: list[str] | None, limit: int | None
) -> list[str]:
    if explicit:
        return explicit[:limit] if limit else explicit
    rows: list[str] = []
    with open(slugs_file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if city and row.get("city", "") != city:
                continue
            slug = row.get("slug", "").strip()
            if slug:
                rows.append(slug)
            if limit is not None and len(rows) >= limit:
                break
    return rows


# --- helpers shared across stages ----------------------------------------- #


def parse_json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def load_gamma(slug: str) -> dict[str, Any]:
    return json.loads((GAMMA_DIR / f"{slug}.json").read_text())


def load_fills(slug: str) -> dict[str, list[dict[str, Any]]]:
    path = FILLS_DIR / f"{slug}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def parse_ts(s: Any) -> pd.Timestamp | None:
    if s is None or s == "":
        return None
    try:
        return pd.to_datetime(s, utc=True)
    except (ValueError, TypeError):
        return None


# --- stage 1: markets.parquet --------------------------------------------- #


_MARKETS_SCHEMA = pa.schema(
    [
        ("slug", pa.string()),
        ("condition_id", pa.string()),
        ("question", pa.string()),
        ("description", pa.string()),
        ("city", pa.string()),
        ("weather_tags", pa.string()),
        ("outcomes", pa.list_(pa.string())),
        ("outcome_prices", pa.list_(pa.float64())),
        ("clob_token_ids", pa.list_(pa.string())),
        ("yes_token_id", pa.string()),
        ("no_token_id", pa.string()),
        ("volume_num", pa.float64()),
        ("volume_clob", pa.float64()),
        ("liquidity_num", pa.float64()),
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("spread", pa.float64()),
        ("last_trade_price", pa.float64()),
        ("order_price_min_tick_size", pa.float64()),
        ("order_min_size", pa.float64()),
        ("neg_risk", pa.bool_()),
        ("active", pa.bool_()),
        ("closed", pa.bool_()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("end_date", pa.timestamp("us", tz="UTC")),
        ("closed_time", pa.timestamp("us", tz="UTC")),
        ("resolution_source", pa.string()),
        ("group_item_title", pa.string()),
        ("group_item_threshold", pa.int64()),
    ]
)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def build_market_row(slug: str, city: str, weather_tags: str) -> dict[str, Any]:
    m = load_gamma(slug)
    tokens = [str(t) for t in parse_json_list(m.get("clobTokenIds"))]
    outcomes = [str(o) for o in parse_json_list(m.get("outcomes"))]
    outcome_prices = [
        float(p) for p in parse_json_list(m.get("outcomePrices")) if p not in (None, "")
    ]
    yes_token = tokens[0] if len(tokens) >= 1 else None
    no_token = tokens[1] if len(tokens) >= 2 else None
    return {
        "slug": m.get("slug") or slug,
        "condition_id": m.get("conditionId"),
        "question": m.get("question"),
        "description": m.get("description"),
        "city": city,
        "weather_tags": weather_tags,
        "outcomes": outcomes,
        "outcome_prices": outcome_prices,
        "clob_token_ids": tokens,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "volume_num": _float_or_none(m.get("volumeNum")),
        "volume_clob": _float_or_none(m.get("volumeClob")),
        "liquidity_num": _float_or_none(m.get("liquidityNum")),
        "best_bid": _float_or_none(m.get("bestBid")),
        "best_ask": _float_or_none(m.get("bestAsk")),
        "spread": _float_or_none(m.get("spread")),
        "last_trade_price": _float_or_none(m.get("lastTradePrice")),
        "order_price_min_tick_size": _float_or_none(m.get("orderPriceMinTickSize")),
        "order_min_size": _float_or_none(m.get("orderMinSize")),
        "neg_risk": bool(m.get("negRisk")) if m.get("negRisk") is not None else None,
        "active": bool(m.get("active")) if m.get("active") is not None else None,
        "closed": bool(m.get("closed")) if m.get("closed") is not None else None,
        "created_at": parse_ts(m.get("createdAt")),
        "end_date": parse_ts(m.get("endDate") or m.get("endDateIso")),
        "closed_time": parse_ts(m.get("closedTime")),
        "resolution_source": m.get("resolutionSource"),
        "group_item_title": m.get("groupItemTitle"),
        "group_item_threshold": _int_or_none(m.get("groupItemThreshold")),
    }


def write_markets(
    slugs: list[str], slug_meta: dict[str, dict[str, str]], log: logging.Logger
) -> int:
    rows = [
        build_market_row(slug, slug_meta[slug]["city"], slug_meta[slug]["weather_tags"])
        for slug in slugs
    ]
    table = pa.Table.from_pylist(rows, schema=_MARKETS_SCHEMA)
    MARKETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, MARKETS_PATH, compression="zstd")
    log.info("wrote markets.parquet: %d rows, %d bytes", len(rows), MARKETS_PATH.stat().st_size)
    return len(rows)


# --- stage 2: fills partitioned by year/month ----------------------------- #


_FILLS_SCHEMA = pa.schema(
    [
        ("timestamp", pa.timestamp("ms", tz="UTC")),
        ("slug", pa.string()),
        ("condition_id", pa.string()),
        ("token_id", pa.string()),
        ("outcome", pa.string()),  # "YES" | "NO"
        ("side", pa.string()),  # "buy" | "sell"
        ("price", pa.float64()),
        ("shares", pa.float64()),
        ("usd", pa.float64()),
        ("maker", pa.string()),
        ("taker", pa.string()),
        ("maker_asset_id", pa.string()),
        ("taker_asset_id", pa.string()),
        ("maker_amount_filled", pa.int64()),
        ("taker_amount_filled", pa.int64()),
        ("fee", pa.int64()),
        ("transaction_hash", pa.string()),
        ("order_hash", pa.string()),
    ]
)


def derive_fill(
    f: dict[str, Any], slug: str, condition_id: str, yes_token: str | None, no_token: str | None
) -> dict[str, Any] | None:
    """Expand one OrderFilled event into the wide-form fills row.

    Returns None for token↔token swaps (rare) that can't be priced in USDC.
    """
    maker_asset = f["makerAssetId"]
    taker_asset = f["takerAssetId"]
    maker_amt = int(f["makerAmountFilled"])
    taker_amt = int(f["takerAmountFilled"])

    # Identify which side this fill is on (YES or NO token)
    if maker_asset == "0":
        token = taker_asset
    elif taker_asset == "0":
        token = maker_asset
    else:
        return None  # token ↔ token swap, no USDC involvement

    if token == yes_token:
        outcome = "YES"
    elif token == no_token:
        outcome = "NO"
    else:
        # Token not in this market's clob_token_ids — shouldn't happen for data we fetched,
        # but guard just in case.
        outcome = "UNKNOWN"

    # Price + direction from taker perspective
    if maker_asset == "0":
        # Taker is buying `token` with USDC from maker
        if taker_amt == 0:
            return None
        price = maker_amt / taker_amt
        shares = taker_amt / 1e6
        side = "buy"
    else:
        # Taker is selling `token` for USDC
        if maker_amt == 0:
            return None
        price = taker_amt / maker_amt
        shares = maker_amt / 1e6
        side = "sell"

    usd = price * shares

    return {
        "timestamp": pd.Timestamp(int(f["timestamp"]), unit="s", tz="UTC"),
        "slug": slug,
        "condition_id": condition_id,
        "token_id": token,
        "outcome": outcome,
        "side": side,
        "price": price,
        "shares": shares,
        "usd": usd,
        "maker": f["maker"],
        "taker": f["taker"],
        "maker_asset_id": maker_asset,
        "taker_asset_id": taker_asset,
        "maker_amount_filled": maker_amt,
        "taker_amount_filled": taker_amt,
        "fee": int(f["fee"]),
        "transaction_hash": f["transactionHash"],
        "order_hash": f["orderHash"],
    }


class PartitionedWriter:
    """Write rows into Hive-partitioned Parquet by (year, month).

    Holds one ParquetWriter per (year, month) partition; opens on first use,
    closes all at shutdown.  Buffers incoming rows per partition to amortize
    PyArrow table creation overhead.
    """

    def __init__(self, root: Path, schema: pa.Schema, *, batch_rows: int = 250_000) -> None:
        self.root = root
        self.schema = schema
        self.batch_rows = batch_rows
        self._writers: dict[tuple[int, int], pq.ParquetWriter] = {}
        self._buffers: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self.rows_written: int = 0

    def _path_for(self, year: int, month: int) -> Path:
        return self.root / f"year={year}" / f"month={month:02d}" / "part-0.parquet"

    def add(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            ts: pd.Timestamp = r["timestamp"]
            key = (ts.year, ts.month)
            self._buffers.setdefault(key, []).append(r)
            if len(self._buffers[key]) >= self.batch_rows:
                self._flush_partition(key)

    def _flush_partition(self, key: tuple[int, int]) -> None:
        buf = self._buffers.get(key)
        if not buf:
            return
        table = pa.Table.from_pylist(buf, schema=self.schema)
        if key not in self._writers:
            path = self._path_for(*key)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._writers[key] = pq.ParquetWriter(str(path), self.schema, compression="zstd")
        self._writers[key].write_table(table)
        self.rows_written += len(buf)
        self._buffers[key] = []

    def close(self) -> None:
        for key in list(self._buffers.keys()):
            self._flush_partition(key)
        for w in self._writers.values():
            w.close()
        self._writers.clear()
        self._buffers.clear()


def write_fills(slugs: list[str], log: logging.Logger) -> tuple[int, int]:
    writer = PartitionedWriter(FILLS_ROOT, _FILLS_SCHEMA, batch_rows=500_000)
    markets_with_fills = 0
    total_fills = 0
    for i, slug in enumerate(slugs, 1):
        m = load_gamma(slug)
        tokens = [str(t) for t in parse_json_list(m.get("clobTokenIds"))]
        yes_tok = tokens[0] if len(tokens) >= 1 else None
        no_tok = tokens[1] if len(tokens) >= 2 else None
        condition_id = m.get("conditionId")

        fills_by_token = load_fills(slug)
        rows: list[dict[str, Any]] = []
        for token_fills in fills_by_token.values():
            for f in token_fills:
                row = derive_fill(f, slug, condition_id, yes_tok, no_tok)
                if row is not None:
                    rows.append(row)
        if rows:
            markets_with_fills += 1
            total_fills += len(rows)
            writer.add(rows)
        if i % 100 == 0 or i == len(slugs):
            log.info(
                "fills progress: %d/%d slugs, %d markets-with-fills, %d total fills",
                i,
                len(slugs),
                markets_with_fills,
                total_fills,
            )
    writer.close()
    log.info("wrote fills/: %d rows total across %d markets", total_fills, markets_with_fills)
    return total_fills, markets_with_fills


# --- stage 3: prices forward-filled at resolution ------------------------- #


_PRICES_SCHEMA = pa.schema(
    [
        ("timestamp", pa.timestamp("ms", tz="UTC")),
        ("slug", pa.string()),
        ("condition_id", pa.string()),
        ("yes_price", pa.float64()),
        ("no_price", pa.float64()),
        ("yes_fill_count", pa.int64()),
        ("no_fill_count", pa.int64()),
        ("yes_shares_cum", pa.float64()),
        ("no_shares_cum", pa.float64()),
        ("usd_cum", pa.float64()),
    ]
)


def build_prices_for_market(
    slug: str,
    market: dict[str, Any],
    fills_by_token: dict[str, list[dict[str, Any]]],
    resolution_s: int,
) -> list[dict[str, Any]] | None:
    """Forward-fill YES and NO prices at fixed bucket intervals.

    Returns a list of row dicts (one per bucket between first and last fill),
    or None if the market has no priced fills.
    """
    tokens = [str(t) for t in parse_json_list(market.get("clobTokenIds"))]
    if len(tokens) < 2:
        return None
    yes_token, no_token = tokens[0], tokens[1]
    condition_id = market.get("conditionId")

    def side_df(token_id: str) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for f in fills_by_token.get(token_id, []):
            maker_asset = f["makerAssetId"]
            taker_asset = f["takerAssetId"]
            if maker_asset not in ("0", token_id) or taker_asset not in ("0", token_id):
                continue
            if maker_asset == "0":
                if int(f["takerAmountFilled"]) == 0:
                    continue
                price = int(f["makerAmountFilled"]) / int(f["takerAmountFilled"])
                shares = int(f["takerAmountFilled"]) / 1e6
            else:
                if int(f["makerAmountFilled"]) == 0:
                    continue
                price = int(f["takerAmountFilled"]) / int(f["makerAmountFilled"])
                shares = int(f["makerAmountFilled"]) / 1e6
            rows.append(
                {
                    "ts": pd.Timestamp(int(f["timestamp"]), unit="s", tz="UTC"),
                    "price": price,
                    "shares": shares,
                    "usd": price * shares,
                }
            )
        return (
            pd.DataFrame(rows).sort_values("ts").reset_index(drop=True) if rows else pd.DataFrame()
        )

    yes_df = side_df(yes_token)
    no_df = side_df(no_token)
    if yes_df.empty and no_df.empty:
        return None

    # Determine bucket range from first to last fill on any side
    all_ts = pd.concat(
        [
            yes_df["ts"] if not yes_df.empty else pd.Series(dtype="datetime64[ns, UTC]"),
            no_df["ts"] if not no_df.empty else pd.Series(dtype="datetime64[ns, UTC]"),
        ],
        ignore_index=True,
    )
    freq = f"{resolution_s}s"
    start = all_ts.min().floor(freq)
    end = all_ts.max().ceil(freq)
    buckets = pd.date_range(start, end, freq=freq, tz="UTC")
    if len(buckets) == 0:
        return None

    # Forward-fill last price per side per bucket. Multiple fills in the same
    # bucket collapse to the last one ("close").
    def last_per_bucket(df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series(dtype=float, index=buckets)
        df = df.copy()
        df["bucket"] = df["ts"].dt.floor(freq)
        per_bucket = df.groupby("bucket", sort=True)["price"].last()
        return per_bucket.reindex(buckets).ffill()

    # Cumulative counts / shares / usd. Bucketed cumsum then reindex+ffill.
    def cum_per_bucket(df: pd.DataFrame, col: str) -> pd.Series:
        if df.empty:
            return pd.Series(0.0, index=buckets)
        df = df.copy()
        df["bucket"] = df["ts"].dt.floor(freq)
        sums = df.groupby("bucket", sort=True)[col].sum()
        cum = sums.cumsum()
        return cum.reindex(buckets).ffill().fillna(0.0)

    def count_per_bucket(df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series(0, index=buckets, dtype="int64")
        df = df.copy()
        df["bucket"] = df["ts"].dt.floor(freq)
        counts = df.groupby("bucket", sort=True).size()
        return counts.reindex(buckets, fill_value=0).cumsum().astype("int64")

    yes_price = last_per_bucket(yes_df)
    no_price = last_per_bucket(no_df)
    yes_count = count_per_bucket(yes_df)
    no_count = count_per_bucket(no_df)
    yes_shares = cum_per_bucket(yes_df, "shares")
    no_shares = cum_per_bucket(no_df, "shares")
    yes_usd = cum_per_bucket(yes_df, "usd")
    no_usd = cum_per_bucket(no_df, "usd")

    result = pd.DataFrame(
        {
            "timestamp": buckets,
            "slug": slug,
            "condition_id": condition_id,
            "yes_price": yes_price.values,
            "no_price": no_price.values,
            "yes_fill_count": yes_count.values,
            "no_fill_count": no_count.values,
            "yes_shares_cum": yes_shares.values,
            "no_shares_cum": no_shares.values,
            "usd_cum": (yes_usd + no_usd).values,
        }
    )
    return result.to_dict("records")


def write_prices(slugs: list[str], resolution_s: int, log: logging.Logger) -> tuple[int, int]:
    writer = PartitionedWriter(PRICES_ROOT, _PRICES_SCHEMA, batch_rows=500_000)
    markets_with_prices = 0
    total_rows = 0
    for i, slug in enumerate(slugs, 1):
        m = load_gamma(slug)
        fills_by_token = load_fills(slug)
        rows = build_prices_for_market(slug, m, fills_by_token, resolution_s)
        if rows:
            markets_with_prices += 1
            total_rows += len(rows)
            writer.add(rows)
        if i % 25 == 0 or i == len(slugs):
            log.info(
                "prices progress: %d/%d slugs, %d with-prices, %d total rows",
                i,
                len(slugs),
                markets_with_prices,
                total_rows,
            )
    writer.close()
    log.info("wrote prices/: %d rows total across %d markets", total_rows, markets_with_prices)
    return total_rows, markets_with_prices


# --- main ------------------------------------------------------------------ #


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=DESCRIPTION)
    p.add_argument("--force", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--city", default=None, help="filter slugs by exact city match")
    p.add_argument("--slugs-file", type=Path, default=DEFAULT_SLUGS_CSV)
    p.add_argument(
        "--slugs", default=None, help="comma-separated slug list (overrides --slugs-file)"
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--resolution-seconds", type=int, default=60)
    p.add_argument("--skip-markets", action="store_true")
    p.add_argument("--skip-fills", action="store_true")
    p.add_argument("--skip-prices", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.force = args.force or args.fresh

    if TransformManifest.check_already_complete(MANIFEST_PATH, force=args.force):
        print(f"{STEP_NAME} already complete; pass --force to rebuild")
        return 0

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    log = configure_logging(LOG_PATH, verbose=args.verbose)
    check_preconditions(log)

    if args.fresh:
        log.info("--fresh: wiping %s", PROCESSED_DIR)
        for child in PROCESSED_DIR.iterdir():
            if child.name == "transform.log":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    explicit = [s.strip() for s in args.slugs.split(",")] if args.slugs else None
    slugs = select_slugs(args.slugs_file, city=args.city, explicit=explicit, limit=args.limit)
    log.info(
        "selected: %d slugs (city=%r limit=%s resolution=%ds)",
        len(slugs),
        args.city,
        args.limit,
        args.resolution_seconds,
    )
    if not slugs:
        raise SystemExit("no slugs selected")

    # Build slug → city/weather_tags mapping from the catalog for markets stage
    slug_meta: dict[str, dict[str, str]] = {}
    with open(args.slugs_file, newline="") as f:
        for row in csv.DictReader(f):
            slug_meta[row["slug"]] = {
                "city": row.get("city", ""),
                "weather_tags": row.get("weather_tags", ""),
            }

    if args.dry_run:
        log.info(
            "dry-run: would write markets=%s fills=%s prices=%s",
            not args.skip_markets,
            not args.skip_fills,
            not args.skip_prices,
        )
        log.info("dry-run: first 5 slugs: %s", slugs[:5])
        return 0

    with TransformManifest(started_at=utc_now(), args=args) as manifest:
        stats: dict[str, Any] = {"selected_slugs": len(slugs)}

        if not args.skip_markets:
            log.info("stage 1: markets")
            n = write_markets(slugs, slug_meta, log)
            stats["markets_rows"] = n
            manifest.set_stat("markets_rows", n)
        else:
            log.info("stage 1: markets SKIPPED")

        if not args.skip_fills:
            log.info("stage 2: fills")
            n_fills, n_markets_with_fills = write_fills(slugs, log)
            stats["fills_rows"] = n_fills
            stats["markets_with_fills"] = n_markets_with_fills
            manifest.set_stat("fills_rows", n_fills)
            manifest.set_stat("markets_with_fills", n_markets_with_fills)
        else:
            log.info("stage 2: fills SKIPPED")

        if not args.skip_prices:
            log.info("stage 3: prices (resolution=%ds)", args.resolution_seconds)
            n_prices, n_markets_with_prices = write_prices(slugs, args.resolution_seconds, log)
            stats["prices_rows"] = n_prices
            stats["prices_resolution_seconds"] = args.resolution_seconds
            stats["markets_with_prices"] = n_markets_with_prices
            manifest.set_stat("prices_rows", n_prices)
            manifest.set_stat("prices_resolution_seconds", args.resolution_seconds)
            manifest.set_stat("markets_with_prices", n_markets_with_prices)
        else:
            log.info("stage 3: prices SKIPPED")

        manifest.complete(stats=stats)
        log.info("done: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())

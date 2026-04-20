"""Query Polymarket daily-temperature markets from the processed parquet.

Wraps `data/processed/polymarket_weather/markets.parquet` with a typed
API. Parses bucket thresholds (°F) from group_item_title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETS_PATH = REPO_ROOT / "data" / "processed" / "polymarket_weather" / "markets.parquet"

_RE_RANGE = re.compile(r"^(\d+)-(\d+)°F$")
_RE_BELOW = re.compile(r"^(\d+)°F or below$")
_RE_ABOVE = re.compile(r"^(\d+)°F or higher$")


@dataclass(frozen=True)
class BucketMarket:
    slug: str
    city: str
    yes_token_id: str
    no_token_id: str
    bucket_idx: int
    bucket_title: str
    bucket_low: float  # may be -inf
    bucket_high: float  # may be +inf
    bucket_center: float
    market_date: date


def _parse_bucket(title: str) -> tuple[float, float, float] | None:
    m = _RE_RANGE.match(title)
    if m:
        lo, hi = int(m[1]), int(m[2])
        return (float(lo), float(hi), (lo + hi) / 2.0)
    m = _RE_BELOW.match(title)
    if m:
        hi = int(m[1])
        return (float("-inf"), float(hi), float(hi - 1))
    m = _RE_ABOVE.match(title)
    if m:
        lo = int(m[1])
        return (float(lo), float("inf"), float(lo + 1))
    return None


def get_daily_temp_markets(target_date: date, city: str | None = None) -> list[BucketMarket]:
    """Return all daily-temp bucket markets resolving on `target_date`.

    If `city` is given, filter to that city. Otherwise all 11 US cities.
    """
    if not MARKETS_PATH.exists():
        raise FileNotFoundError(
            f"Markets parquet missing: {MARKETS_PATH}. "
            f"Run: uv run python scripts/polymarket_weather/download.py && "
            f"uv run python scripts/polymarket_weather/transform.py"
        )
    con = duckdb.connect()
    where_city = f"AND city = '{city}'" if city else ""
    df = con.execute(f"""
        SELECT slug, city, yes_token_id, no_token_id,
               group_item_threshold AS bucket_idx,
               group_item_title,
               DATE(end_date) AS market_date
        FROM '{MARKETS_PATH}'
        WHERE weather_tags ILIKE '%Daily Temperature%'
          AND DATE(end_date) = '{target_date}'
          {where_city}
        ORDER BY city, bucket_idx
    """).fetch_df()

    out: list[BucketMarket] = []
    for _, r in df.iterrows():
        parsed = _parse_bucket(r["group_item_title"])
        if parsed is None:
            continue
        lo, hi, center = parsed
        out.append(BucketMarket(
            slug=str(r["slug"]),
            city=str(r["city"]),
            yes_token_id=str(r["yes_token_id"]),
            no_token_id=str(r["no_token_id"]),
            bucket_idx=int(r["bucket_idx"]),
            bucket_title=str(r["group_item_title"]),
            bucket_low=lo, bucket_high=hi, bucket_center=center,
            market_date=r["market_date"] if isinstance(r["market_date"], date) else date.fromisoformat(str(r["market_date"])),
        ))
    return out


def get_city_buckets(target_date: date, city: str) -> list[BucketMarket]:
    """All buckets for one city on one day, sorted by bucket_idx."""
    return get_daily_temp_markets(target_date, city=city)


def find_nbs_fav_plus1(
    buckets: list[BucketMarket], nbs_pred_f: float, offset: int = 1,
) -> BucketMarket | None:
    """From a list of buckets (one city-day), find the (NBS_fav + offset) bucket.

    NBS_fav = bucket whose center is closest to nbs_pred_f.
    Returns None if the offset bucket doesn't exist (e.g., NBS_fav is already
    the highest-temp bucket).
    """
    if not buckets:
        return None
    fav = min(buckets, key=lambda b: abs(b.bucket_center - nbs_pred_f))
    target_idx = fav.bucket_idx + offset
    for b in buckets:
        if b.bucket_idx == target_idx:
            return b
    return None

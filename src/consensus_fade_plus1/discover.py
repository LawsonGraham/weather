"""Discover today's tradeable markets for the Consensus-Fade +1 strategy.

Reads weather forecasts + Polymarket market catalog, returns the list of
(city, market_date, condition_id, no_token_id, bucket_title) tuples we want
to subscribe + quote on.

Used by the Nautilus node at startup to pick its `load_ids`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from lib.weather.consensus import consensus_spread
from lib.weather.forecasts import get_all_cities

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKETS_PATH = REPO_ROOT / "data" / "processed" / "polymarket_weather" / "markets.parquet"

_RE_RANGE = re.compile(r"^(\d+)-(\d+)°F$")
_RE_BELOW = re.compile(r"^(\d+)°F or below$")
_RE_ABOVE = re.compile(r"^(\d+)°F or higher$")


@dataclass(frozen=True)
class TradeableMarket:
    """A single +1 offset bucket market identified for trading today."""
    city: str
    market_date: date
    consensus_spread: float
    nbs_pred: float
    gfs_pred: float
    hrrr_pred: float | None
    fav_bucket_title: str
    bucket_title: str
    bucket_idx: int
    slug: str
    condition_id: str
    yes_token_id: str
    no_token_id: str


def _parse_bucket_center(title: str) -> float | None:
    m = _RE_RANGE.match(title)
    if m:
        return (int(m[1]) + int(m[2])) / 2.0
    m = _RE_BELOW.match(title)
    if m:
        return float(int(m[1]) - 1)
    m = _RE_ABOVE.match(title)
    if m:
        return float(int(m[1]) + 1)
    return None


def discover_tradeable_markets(
    target_date: date | None = None,
    *,
    consensus_max: float = 3.0,
) -> list[TradeableMarket]:
    """Return the list of +1 offset markets eligible to trade today.

    Filters:
      1. Forecasts available (NBS + GFS required; HRRR optional)
      2. consensus_spread ≤ consensus_max
      3. +1 offset bucket exists for this city on this market_date
    """
    if target_date is None:
        target_date = datetime.now(UTC).date()

    if not MARKETS_PATH.exists():
        raise FileNotFoundError(
            f"{MARKETS_PATH} missing. Run the MarketsWatcher or "
            f"scripts/polymarket_weather/*.py first."
        )

    forecasts = get_all_cities(target_date)
    # Filter cities with complete NBS+GFS
    forecasts = [f for f in forecasts
                 if f.nbs_pred_max_f is not None and f.gfs_pred_max_f is not None]

    # Filter by consensus
    tight = []
    for f in forecasts:
        cs = consensus_spread(f, require_all_three=False)
        if cs is None or cs > consensus_max:
            continue
        tight.append((f, cs))

    if not tight:
        return []

    # Pull bucket metadata for these cities on this date
    con = duckdb.connect()
    cities_clause = ",".join(f"'{f.city}'" for f, _ in tight)
    df = con.execute(f"""
        SELECT slug, city, condition_id, yes_token_id, no_token_id,
               group_item_threshold AS bucket_idx, group_item_title,
               DATE(end_date) AS market_date
        FROM '{MARKETS_PATH}'
        WHERE weather_tags ILIKE '%Daily Temperature%'
          AND DATE(end_date) = '{target_date}'
          AND city IN ({cities_clause})
        ORDER BY city, bucket_idx
    """).fetch_df()

    if df.empty:
        return []

    df["center"] = df["group_item_title"].apply(_parse_bucket_center)
    df = df.dropna(subset=["center"])

    out: list[TradeableMarket] = []
    for f, cs in tight:
        city_df = df[df["city"] == f.city]
        if city_df.empty:
            continue
        # Nearest bucket to NBS prediction = favorite
        diffs = (city_df["center"] - f.nbs_pred_max_f).abs()
        fav_row = city_df.loc[diffs.idxmin()]
        fav_idx = int(fav_row["bucket_idx"])
        plus1 = city_df[city_df["bucket_idx"] == fav_idx + 1]
        if plus1.empty:
            continue
        r = plus1.iloc[0]
        out.append(TradeableMarket(
            city=f.city,
            market_date=target_date,
            consensus_spread=float(cs),
            nbs_pred=float(f.nbs_pred_max_f),
            gfs_pred=float(f.gfs_pred_max_f),
            hrrr_pred=f.hrrr_pred_max_f,
            fav_bucket_title=str(fav_row["group_item_title"]),
            bucket_title=str(r["group_item_title"]),
            bucket_idx=int(r["bucket_idx"]),
            slug=str(r["slug"]),
            condition_id=str(r["condition_id"]),
            yes_token_id=str(r["yes_token_id"]),
            no_token_id=str(r["no_token_id"]),
        ))
    return out


def print_discovery_summary(markets: list[TradeableMarket]) -> None:
    if not markets:
        print("No tradeable markets today (0 cities pass consensus filter).")
        return
    print(f"Discovered {len(markets)} tradeable +1 offset market(s):")
    for m in markets:
        hrrr = f"{m.hrrr_pred:.0f}" if m.hrrr_pred else "—"
        print(f"  {m.city:<16} cs={m.consensus_spread:.1f}  "
              f"NBS/GFS/HRRR={m.nbs_pred:.0f}/{m.gfs_pred:.0f}/{hrrr}  "
              f"fav={m.fav_bucket_title:<10} +1={m.bucket_title}")


if __name__ == "__main__":
    markets = discover_tradeable_markets()
    print_discovery_summary(markets)

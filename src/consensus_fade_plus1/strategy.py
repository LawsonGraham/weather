"""Consensus-Fade +1 Offset signal generation.

For a given target date:
1. Pull per-city NBS + GFS + HRRR forecasts from features parquet
2. Compute consensus_spread = max - min
3. For each city with spread ≤ threshold, identify NBS_fav + 1 bucket
4. Return list of BUY-NO recommendations

No order placement happens here — this is a pure signal-generation
module. Submission lives in cli.py / orders.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from lib.polymarket.markets import BucketMarket, find_nbs_fav_plus1, get_city_buckets
from lib.weather.consensus import consensus_spread
from lib.weather.forecasts import get_all_cities

# Pre-registered parameters (see STRATEGY.md §3)
DEFAULT_CONSENSUS_MAX_F = 3.0
DEFAULT_MIN_YES_PRICE = 0.005
DEFAULT_MAX_YES_PRICE = 0.5
DEFAULT_FEE_RATE = 0.05
EXPECTED_HIT_RATE = 0.97  # NO wins ~97% of the time under consensus filter


@dataclass
class Recommendation:
    """One trade recommendation (not yet submitted)."""
    city: str
    market_date: date
    consensus_spread: float
    nbs_pred: float
    gfs_pred: float
    hrrr_pred: float | None
    nbs_fav_bucket_title: str
    nbs_fav_bucket_idx: int
    plus1_bucket: BucketMarket
    # Market-state estimates (may be stale — fetch fresh at submit time)
    yes_price_estimate: float | None = None
    no_ask_estimate: float | None = None

    def est_edge_pp(self) -> float | None:
        """Estimated edge in percentage points (market-implied - actual)."""
        if self.no_ask_estimate is None:
            return None
        return (EXPECTED_HIT_RATE - self.no_ask_estimate) * 100


def build_recommendations(
    target_date: date,
    *,
    consensus_max: float = DEFAULT_CONSENSUS_MAX_F,
    min_yes_price: float = DEFAULT_MIN_YES_PRICE,
    max_yes_price: float = DEFAULT_MAX_YES_PRICE,
) -> list[Recommendation]:
    """Generate today's Consensus-Fade +1 recommendations.

    Does NOT hit the CLOB — uses the processed markets.parquet for
    bucket metadata. Call apply_live_prices() separately to attach
    fresh price estimates before submitting.
    """
    forecasts = get_all_cities(target_date)
    out: list[Recommendation] = []
    for f in forecasts:
        if f.nbs_pred_max_f is None or f.gfs_pred_max_f is None:
            continue
        cs = consensus_spread(f, require_all_three=False)
        if cs is None or cs > consensus_max:
            continue
        # Find NBS_fav + 1 bucket
        buckets = get_city_buckets(target_date, f.city)
        if len(buckets) < 2:
            continue
        fav = min(buckets, key=lambda b: abs(b.bucket_center - f.nbs_pred_max_f))
        plus1 = find_nbs_fav_plus1(buckets, f.nbs_pred_max_f, offset=1)
        if plus1 is None:
            continue
        out.append(Recommendation(
            city=f.city,
            market_date=target_date,
            consensus_spread=float(cs),
            nbs_pred=float(f.nbs_pred_max_f),
            gfs_pred=float(f.gfs_pred_max_f),
            hrrr_pred=f.hrrr_pred_max_f,
            nbs_fav_bucket_title=fav.bucket_title,
            nbs_fav_bucket_idx=fav.bucket_idx,
            plus1_bucket=plus1,
        ))
    return out


def apply_live_prices(
    client, recs: list[Recommendation], *,
    min_yes_price: float = DEFAULT_MIN_YES_PRICE,
    max_yes_price: float = DEFAULT_MAX_YES_PRICE,
) -> list[Recommendation]:
    """Attach fresh YES-mid / NO-ask to each recommendation via CLOB.

    Filters out recommendations whose YES midpoint is outside
    [min_yes_price, max_yes_price].
    """
    out: list[Recommendation] = []
    for r in recs:
        book = client.get_order_book(r.plus1_bucket.yes_token_id)
        bids = getattr(book, "bids", []) or []
        asks = getattr(book, "asks", []) or []
        if not bids or not asks:
            continue
        # pyclob returns OrderSummary objects with .price / .size (strings)
        top_bid = max(float(b.price) for b in bids)
        top_ask = min(float(a.price) for a in asks)
        yes_mid = (top_bid + top_ask) / 2
        no_ask = 1 - top_bid  # to BUY NO at this price, match YES-bid
        r2 = Recommendation(
            city=r.city, market_date=r.market_date,
            consensus_spread=r.consensus_spread,
            nbs_pred=r.nbs_pred, gfs_pred=r.gfs_pred, hrrr_pred=r.hrrr_pred,
            nbs_fav_bucket_title=r.nbs_fav_bucket_title,
            nbs_fav_bucket_idx=r.nbs_fav_bucket_idx,
            plus1_bucket=r.plus1_bucket,
            yes_price_estimate=yes_mid,
            no_ask_estimate=no_ask,
        )
        if yes_mid < min_yes_price or yes_mid > max_yes_price:
            continue
        out.append(r2)
    return out

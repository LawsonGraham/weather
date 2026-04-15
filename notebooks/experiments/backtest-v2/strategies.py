"""Pre-registered strategies (S0-S5).

All strategies: `selector_fn(day_df) -> list[bucket_idx]`.
- `day_df` = DataFrame of all 11 buckets for one (city, market_date)
- Returns which bucket indices to buy (1 share each)

S0: NBS favorite (control, expected to lose)
S1: +2°F offset (Strategy D V1)
S2: +4°F offset (Strategy D V2)
S3: S1 + S2 basket
S4: S1 + NBS-spread-filter
S5: LightGBM model edge (threshold-based)
"""
from __future__ import annotations


def _nbs_fav_bucket_idx(day) -> int:
    """Bucket whose center is closest to NBS predicted max."""
    nbs = day["nbs_pred_max_f"].iloc[0]
    # pick first row in ties
    diff = (day["bucket_center"] - nbs).abs()
    return int(day.loc[diff.idxmin(), "bucket_idx"])


def _market_fav_bucket_idx(day) -> int:
    """Bucket with highest entry_price (market favorite)."""
    return int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])


def _bucket_by_offset(day, base_idx: int, offset_buckets: int) -> int | None:
    """Return the bucket_idx at base_idx + offset_buckets, or None if out of range."""
    target = base_idx + offset_buckets
    avail = day["bucket_idx"].tolist()
    if target not in avail:
        return None
    return target


# --- S0: NBS favorite (control) ------------------------------------- #
def S0_nbs_fav(day):
    return [_nbs_fav_bucket_idx(day)]


# --- S0b: Market favorite (control variant) ------------------------- #
def S0b_market_fav(day):
    """Buy highest-priced bucket (market favorite)."""
    return [int(day.loc[day["entry_price"].idxmax(), "bucket_idx"])]


# --- S1: +2°F offset from NBS fav (+1 bucket) ----------------------- #
def S1_plus2f(day):
    fav = _nbs_fav_bucket_idx(day)
    t = _bucket_by_offset(day, fav, 1)
    return [t] if t is not None else []


# --- S1m: +2°F offset from MARKET fav (+1 bucket) ------------------- #
def S1m_plus2f_mkt(day):
    fav = _market_fav_bucket_idx(day)
    t = _bucket_by_offset(day, fav, 1)
    return [t] if t is not None else []


# --- S2: +4°F offset from NBS fav (+2 buckets) ---------------------- #
def S2_plus4f(day):
    fav = _nbs_fav_bucket_idx(day)
    t = _bucket_by_offset(day, fav, 2)
    return [t] if t is not None else []


# --- S2m: +4°F offset from MARKET fav (+2 buckets) ------------------ #
def S2m_plus4f_mkt(day):
    fav = _market_fav_bucket_idx(day)
    t = _bucket_by_offset(day, fav, 2)
    return [t] if t is not None else []


# --- S3: NBS-based basket S1 + S2 ----------------------------------- #
def S3_basket_plus2_plus4(day):
    out = []
    fav = _nbs_fav_bucket_idx(day)
    for off in (1, 2):
        t = _bucket_by_offset(day, fav, off)
        if t is not None:
            out.append(t)
    return out


# --- S3m: Market-based basket S1m + S2m ----------------------------- #
def S3m_basket_plus2_plus4_mkt(day):
    out = []
    fav = _market_fav_bucket_idx(day)
    for off in (1, 2):
        t = _bucket_by_offset(day, fav, off)
        if t is not None:
            out.append(t)
    return out


# --- S4: NBS-based S1 filtered on NBS spread ∈ [2, 3] --------------- #
def S4_plus2f_nbs_spread_2_3(day):
    spread = day["nbs_spread_f"].iloc[0]
    if spread is None or not (2.0 <= float(spread) <= 3.0):
        return []
    return S1_plus2f(day)


# --- S5: model-edge — SEE strategies_S5.py for build ----------------- #
# S5 requires loading the LightGBM model. Lazy-import to avoid pulling
# heavy deps when running S0-S4 only.

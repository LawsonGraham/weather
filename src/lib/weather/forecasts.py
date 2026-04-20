"""Load daily-max forecasts for a (city, local_date).

Reads from the precomputed feature parquet. If that's stale (doesn't
include target date), caller should refresh via
``notebooks/experiments/backtest-v3/build_features.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
FEATURES_PATH = REPO_ROOT / "data" / "processed" / "backtest_v3" / "features.parquet"

CITY_TO_STATION = {
    "New York City": "LGA",
    "Atlanta": "ATL",
    "Dallas": "DAL",
    "Seattle": "SEA",
    "Chicago": "ORD",
    "Miami": "MIA",
    "Austin": "AUS",
    "Houston": "HOU",
    "Denver": "DEN",
    "Los Angeles": "LAX",
    "San Francisco": "SFO",
}
STATION_TO_CITY = {v: k for k, v in CITY_TO_STATION.items()}


@dataclass(frozen=True)
class DailyForecast:
    city: str
    station: str
    local_date: date
    nbs_pred_max_f: float | None
    gfs_pred_max_f: float | None
    hrrr_pred_max_f: float | None
    tmp_noon_f: float | None
    tmp_morning_f: float | None

    @property
    def has_all_three(self) -> bool:
        return all(
            x is not None
            for x in (self.nbs_pred_max_f, self.gfs_pred_max_f, self.hrrr_pred_max_f)
        )


def load_features_for_date(target_date: date) -> pd.DataFrame:
    """Load all per-station forecast rows for a given local_date."""
    if not FEATURES_PATH.exists():
        raise FileNotFoundError(
            f"Features parquet missing: {FEATURES_PATH}. "
            f"Regenerate via notebooks/experiments/backtest-v3/build_features.py"
        )
    df = pd.read_parquet(FEATURES_PATH)
    df["local_date"] = pd.to_datetime(df["local_date"]).dt.date
    df = df[df["local_date"] == target_date].copy()
    return df


def get_forecast(city: str, target_date: date) -> DailyForecast | None:
    """Return the forecast for (city, target_date), or None if not present."""
    station = CITY_TO_STATION.get(city)
    if station is None:
        return None
    df = load_features_for_date(target_date)
    row = df[df["station"] == station]
    if row.empty:
        return None
    r = row.iloc[0]
    return DailyForecast(
        city=city,
        station=station,
        local_date=target_date,
        nbs_pred_max_f=_float_or_none(r.get("nbs_pred_max_f")),
        gfs_pred_max_f=_float_or_none(r.get("gfs_pred_max_f")),
        hrrr_pred_max_f=_float_or_none(r.get("hrrr_max_t_f")),
        tmp_noon_f=_float_or_none(r.get("tmp_noon_f")),
        tmp_morning_f=_float_or_none(r.get("tmp_morning_f")),
    )


def get_all_cities(target_date: date) -> list[DailyForecast]:
    """Return forecasts for every city with complete NBS+GFS+HRRR data."""
    df = load_features_for_date(target_date)
    out: list[DailyForecast] = []
    for station, city in STATION_TO_CITY.items():
        row = df[df["station"] == station]
        if row.empty:
            continue
        r = row.iloc[0]
        out.append(DailyForecast(
            city=city,
            station=station,
            local_date=target_date,
            nbs_pred_max_f=_float_or_none(r.get("nbs_pred_max_f")),
            gfs_pred_max_f=_float_or_none(r.get("gfs_pred_max_f")),
            hrrr_pred_max_f=_float_or_none(r.get("hrrr_max_t_f")),
            tmp_noon_f=_float_or_none(r.get("tmp_noon_f")),
            tmp_morning_f=_float_or_none(r.get("tmp_morning_f")),
        ))
    return out


def _float_or_none(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f

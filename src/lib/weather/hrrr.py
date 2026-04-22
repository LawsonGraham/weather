"""Canonical time-resolved HRRR peak-window max.

Single source of truth for what "HRRR max" means in this project. Used
by the features builder (populates `hrrr_max_t_f` in `features.parquet`),
the canonical backtest reproducer (time-resolved entry), and anywhere
else that needs an HRRR peak max. Replaces three previously-drifting
definitions:

1. `notebooks/experiments/backtest-v3/build_features.py` — morning
   cutoff (init <= 10 local), no peak-window filter, no coverage
   requirement. Simpler but didn't validate afternoon-peak quality.
2. `src/consensus_fade_plus1/backtest.py` — time-resolved, peak-
   window filter, min_coverage=6. The version the canonical-v2
   backtest stats were computed against.
3. Exploration notebooks — yet another inline variant.

Canonical definition (now used everywhere):

- Station's local peak window: [12:00, 22:00] (inclusive) in airport tz
- Use only HRRR forecasts with `init_time <= cutoff_utc`
- HRRR gives fxx=6 (6-hour forecasts); for each `valid_time` in the
  peak window, use the forecast from the most recent `init_time` at
  or before the cutoff (in case multiple inits produced overlapping
  valid_times, which happens hourly)
- Require at least `min_coverage` distinct valid-hours covered within
  the peak window; otherwise return None
- Return the maximum forecast temperature across the covered valid-hours,
  converted from Kelvin to Fahrenheit

Returning None on insufficient coverage is load-bearing: a partial
peak-coverage max would typically fall well below true daily peak
(morning-only coverage misses afternoon), which would then pass a
consensus-spread filter for the wrong reason. Better to treat the
market as "not yet evaluable" and skip.

Three public entry points, all implementing the same core semantics:

- `hrrr_peak_max_f(station, local_date, cutoff_utc=...)`
    Single lookup; reads the parquet for one station.
- `hrrr_peak_max_f_batch(station, local_dates, cutoff_utc=...)`
    One file read per station, iterate many dates in-Python. For
    the features builder's all-station x all-date rebuild.
- `hrrr_peak_max_f_from_frame(frame, local_date, tz_name, cutoff_utc)`
    Operates on a pre-loaded DataFrame for one station (columns:
    init_time, valid_time, t_k). For the backtest which walks many
    cutoffs per station.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from lib.weather.timezones import STATION_TZ

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HRRR_ROOT = REPO_ROOT / "data" / "raw" / "hrrr"

PEAK_START_LOCAL_HOUR = 12
PEAK_END_LOCAL_HOUR = 22
MIN_PEAK_COVERAGE_HOURS = 6


def _peak_window_utc(local_date: date, tz_name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(peak_start_utc, peak_end_utc) for the given local date + tz."""
    midnight_local = pd.Timestamp(local_date).tz_localize(tz_name)
    return (
        (midnight_local + pd.Timedelta(hours=PEAK_START_LOCAL_HOUR)).tz_convert("UTC"),
        (midnight_local + pd.Timedelta(hours=PEAK_END_LOCAL_HOUR)).tz_convert("UTC"),
    )


def _canonical_cutoff(cutoff_utc: datetime) -> pd.Timestamp:
    t = pd.Timestamp(cutoff_utc)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _load_station_frame(station: str, cutoff_utc: pd.Timestamp,
                        hrrr_root: Path) -> pd.DataFrame | None:
    """Read one station's HRRR parquet, filter by cutoff. Returns None
    if the file doesn't exist or no rows pass the cutoff."""
    hrrr_path = hrrr_root / f"K{station}" / "hourly.parquet"
    if not hrrr_path.exists():
        return None
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT init_time, valid_time, t2m_heightAboveGround_2 AS t_k
        FROM read_parquet('{hrrr_path}')
        WHERE t2m_heightAboveGround_2 IS NOT NULL
          AND init_time <= TIMESTAMP '{cutoff_utc.isoformat()}'
    """).df()
    if df.empty:
        return None
    df["init_time"] = pd.to_datetime(df["init_time"], utc=True)
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    return df


def hrrr_peak_max_f_from_frame(
    frame: pd.DataFrame,
    local_date: date,
    tz_name: str,
    cutoff_utc: datetime | pd.Timestamp,
    *,
    min_coverage: int = MIN_PEAK_COVERAGE_HOURS,
) -> float | None:
    """Compute canonical peak-max from a pre-loaded DataFrame for one
    station. Frame columns: init_time (UTC-aware), valid_time (UTC-aware),
    t_k (Kelvin). Rows with init_time > cutoff are filtered out here.

    Exposed so callers with many cutoffs per station (e.g. backtest)
    can share the file-read cost."""
    if frame is None or frame.empty:
        return None
    cutoff = _canonical_cutoff(cutoff_utc) if not isinstance(cutoff_utc, pd.Timestamp) else cutoff_utc.tz_convert("UTC")
    peak_start, peak_end = _peak_window_utc(local_date, tz_name)
    sub = frame[(frame.init_time <= cutoff)
                & (frame.valid_time >= peak_start)
                & (frame.valid_time <= peak_end)]
    if sub.empty:
        return None
    latest = sub.sort_values("init_time").groupby("valid_time", as_index=False).tail(1)
    if latest.valid_time.dt.hour.nunique() < min_coverage:
        return None
    t_f = (latest["t_k"] - 273.15) * 9 / 5 + 32
    return float(t_f.max())


def hrrr_peak_max_f(
    station: str,
    local_date: date,
    *,
    cutoff_utc: datetime,
    min_coverage: int = MIN_PEAK_COVERAGE_HOURS,
    hrrr_root: Path | None = None,
) -> float | None:
    """Return the time-resolved HRRR peak-window max (°F), or None.

    `station` is the 3-letter code without the K prefix (ATL, LGA, etc.).
    `local_date` is the calendar date at the airport whose peak we want.
    `cutoff_utc` is the wall-clock limit on `init_time`. For live use:
    pass `datetime.now(UTC)`. For backtest use: pass the entry time.
    """
    tz_name = STATION_TZ.get(station)
    if tz_name is None:
        return None
    cutoff = _canonical_cutoff(cutoff_utc)
    frame = _load_station_frame(station, cutoff, hrrr_root or DEFAULT_HRRR_ROOT)
    if frame is None:
        return None
    return hrrr_peak_max_f_from_frame(frame, local_date, tz_name, cutoff,
                                      min_coverage=min_coverage)


def hrrr_peak_max_f_now(station: str, local_date: date,
                        **kwargs) -> float | None:
    """Convenience wrapper: canonical peak-max using `datetime.now(UTC)`
    as the cutoff. For live use from the features builder or any
    process wanting "freshest available" HRRR."""
    return hrrr_peak_max_f(station, local_date,
                           cutoff_utc=datetime.now(UTC),
                           **kwargs)


def hrrr_peak_max_f_batch(
    station: str,
    local_dates: list[date],
    *,
    cutoff_utc: datetime,
    min_coverage: int = MIN_PEAK_COVERAGE_HOURS,
    hrrr_root: Path | None = None,
) -> dict[date, float | None]:
    """Batched: one file read per station, iterate dates in-Python.
    Returns {local_date: peak_max_f_or_None}."""
    tz_name = STATION_TZ.get(station)
    if tz_name is None:
        return {d: None for d in local_dates}
    cutoff = _canonical_cutoff(cutoff_utc)
    frame = _load_station_frame(station, cutoff,
                                hrrr_root or DEFAULT_HRRR_ROOT)
    if frame is None:
        return {d: None for d in local_dates}
    return {d: hrrr_peak_max_f_from_frame(frame, d, tz_name, cutoff,
                                          min_coverage=min_coverage)
            for d in local_dates}

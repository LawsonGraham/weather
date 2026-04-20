"""Consensus spread across available forecasts."""
from __future__ import annotations

from lib.weather.forecasts import DailyForecast


def consensus_spread(f: DailyForecast, require_all_three: bool = False) -> float | None:
    """Return max(forecasts) - min(forecasts) in °F.

    If `require_all_three`, returns None unless NBS+GFS+HRRR are all present.
    Otherwise uses whatever 2+ forecasts are available (falls back to NBS+GFS).
    """
    vals: list[float] = []
    for v in (f.nbs_pred_max_f, f.gfs_pred_max_f, f.hrrr_pred_max_f):
        if v is not None:
            vals.append(v)
    if require_all_three and len(vals) < 3:
        return None
    if len(vals) < 2:
        return None
    return max(vals) - min(vals)

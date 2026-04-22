"""Consensus spread across available forecasts."""
from __future__ import annotations

from lib.weather.forecasts import DailyForecast


def consensus_spread(f: DailyForecast, require_all_three: bool = True) -> float | None:
    """Return max(forecasts) - min(forecasts) in °F.

    With ``require_all_three=True`` (default), returns None unless NBS + GFS +
    HRRR are all present. A missing HRRR is treated as a hard skip — we will
    not trade a signal that hasn't been confirmed by all three independent
    models. This also doubles as a freshness check: in live operation, HRRR's
    daily max only matches NBS / GFS once HRRR has accumulated enough cycles
    to cover the day's peak hours. If HRRR is partial-day, its max falls 15-
    30°F below NBS / GFS and the caller's cs ≤ threshold filter rejects it.

    With ``require_all_three=False``, falls back to whatever 2+ forecasts are
    available (NBS+GFS, NBS+HRRR, or GFS+HRRR). Retained for diagnostic use
    cases; the live strategy always uses True.
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

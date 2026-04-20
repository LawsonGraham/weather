"""Consensus spread across available forecasts."""
from __future__ import annotations

from lib.weather.forecasts import DailyForecast

# If HRRR differs from BOTH NBS and GFS by more than this many °F, treat
# it as an outlier and drop it from the consensus calculation. This
# protects against partial-day HRRR data (typical live-trading scenario:
# we have only morning-valid forecasts, so max(HRRR) = morning max ≠
# daily max). Backtest has full-day HRRR and won't trip the threshold.
HRRR_OUTLIER_THRESHOLD_F = 10.0


def consensus_spread(f: DailyForecast, require_all_three: bool = False) -> float | None:
    """Return max(forecasts) - min(forecasts) in °F.

    If `require_all_three`, returns None unless NBS+GFS+HRRR are all present.
    Otherwise uses whatever 2+ forecasts are available (falls back to NBS+GFS).

    HRRR outlier rule: when HRRR is present but differs from both NBS and
    GFS by more than HRRR_OUTLIER_THRESHOLD_F, it's dropped (data coverage
    gap produces misleading daily-max estimates during live operation).
    """
    nbs = f.nbs_pred_max_f
    gfs = f.gfs_pred_max_f
    hrrr = f.hrrr_pred_max_f

    # Detect + drop HRRR outliers
    if hrrr is not None and nbs is not None and gfs is not None:
        if (abs(hrrr - nbs) > HRRR_OUTLIER_THRESHOLD_F
            and abs(hrrr - gfs) > HRRR_OUTLIER_THRESHOLD_F):
            hrrr = None

    vals: list[float] = []
    for v in (nbs, gfs, hrrr):
        if v is not None:
            vals.append(v)
    if require_all_three and len(vals) < 3:
        return None
    if len(vals) < 2:
        return None
    return max(vals) - min(vals)

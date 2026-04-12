# Exp 03 — METAR-derived running max (gap-free re-run of exp02)

**Script**: `exp03_metar_running_max.py`
**Date**: 2026-04-11

## Fix

Exp02 used ASOS 1-min running max, which had ~15°F gaps due to coverage holes. Exp03 rebuilds with METAR hourly `tmpf` + `max_temp_6hr_c` from the 00/06/12/18Z synoptic reports, rowwise max. Gap between METAR running-max and realized day-max at each snap:

| Snap    | mean  | n==0 | n≥2 |
|---------|-------|------|-----|
| 14 EDT  | 1.53  | 28   | 22  |
| 16 EDT  | 0.69  | 34   | 10  |
| 18 EDT  | 0.45  | 36   | 5   |

Much tighter than 1-min. By 18 EDT the running max is at the day max on 36/55 days, and within 1°F on 14 more. Use METAR for anything resembling a live-state temperature signal in this project — 1-min is too holey at LGA.

## Strategy results (buy the METAR-rmax range-strike bucket at each snap)

| Snap    | n  | avg_entry | hit_rate | avg_ret | med_ret | cum_pnl |
|---------|----|-----------|----------|---------|---------|---------|
| 12 EDT  | 41 | 0.13      | 37%      | 68.2    | -1.0    | +2794   |
| 14 EDT  | 36 | 0.31      | 50%      | 75.1    | -0.32   | +2702   |
| 16 EDT  | 36 | 0.54      | 58%      | 67.9    | +0.04   | +2445   |
| 18 EDT  | 36 | 0.66      | 64%      | 82.5    | +0.00   | +2970   |

Mean returns still outlier-dominated, but the **hit rate is now the interesting story**: 37% → 50% → 58% → 64% as the day progresses. The 18 EDT rmax bucket correctly identifies the final bucket 64% of the time across 36 days.

Translated plainly: at 18 EDT, the METAR-derived running max in whole-F tells you which range strike will resolve YES about 2 times out of 3. That's not tradeable edge in itself (by 18 EDT the market usually agrees — avg entry p=0.66), but it's a clean baseline.

The MEDIAN return is ~0 at 16/18 EDT, confirming the "mean is outlier-distorted" story. No clean play here on its own.

## Takeaway

Running-max-based strikes are not a durable edge on this data. Move on from running-max signals. **The real finding is in exp04** (fade morning favorites) — exp03 was mostly a signal-hygiene fix to unblock that analysis.

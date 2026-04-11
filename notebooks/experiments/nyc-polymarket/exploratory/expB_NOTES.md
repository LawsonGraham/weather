# Exp B — Winner's 1-min path vs losers on april-10 (resolved day)

**Script**: `expB_winner_vs_losers.py`
**Date**: 2026-04-11
**Status**: Observational. The 1-min endpoint gives us only the 24-h TAIL of
the market's life — by the time 1-min data starts, the winner is already
dominant (p_58-59 = 0.977 at the first datapoint 15:18 EDT on april-10).
The real "info phase" happened BEFORE our 1-min coverage window.

## Method

For april-10 (the only resolved day we have 1-min data for), pull all 11
buckets' 1-min paths and compare. The winner is 58-59°F per
`markets.parquet::outcome_prices`.

## Findings

**Coverage runs from april-10 19:18 UTC to april-11 07:29 UTC** — a 12-hour
window containing resolution time (roughly april-10 ~20:00 EDT = april-11
00:00 UTC based on the LGA max being computed at end-of-local-day).

**Favorite was already dominant at window start:**

- 19:18 UTC (15:18 EDT): 58-59f = 0.977
- 19:20 UTC: 58-59f = 0.960 (brief dip)
- 20:00 UTC: 58-59f = 0.994
- 00:00 UTC (post-resolution): 58-59f = 0.998
- 07:00 UTC: 58-59f = 1.000 (hard lock)

The winner never dipped below 0.960 in the entire window. The loser buckets
all sat at 0.001 (floor quote) with tiny ±1-bp noise.

**Biggest 1-min moves of the winner** (all in the first 40 minutes of
coverage, no moves of any size post-resolution):

| time        | before | after | delta  |
|-------------|--------|-------|--------|
| 19:31       | 0.982  | 0.960 | -0.022 |
| 19:25       | 0.963  | 0.982 | +0.019 |
| 19:48       | 0.964  | 0.979 | +0.015 |
| 19:47       | 0.979  | 0.964 | -0.015 |

These are 1.5–2.2 cent oscillations while the market was effectively done.
Not information; spread chatter.

**Ladder sum during locked state**: 1.001–1.011, reflecting the ~1% overround
from exp A even with everything but one bucket at the floor.

## Interpretation

- **The 1-min endpoint gives us the POST-INFORMATION tail of a resolved day**.
  The interesting move from favorite = X to final resolution = 58-59f happened
  off-screen before 15:18 EDT.
- This is OK for exp14/Strategy D backtest validation (because Strategy D
  enters at 16 EDT which is 20:00 UTC, inside our coverage window) but it's
  not useful for studying the information-discovery phase.
- **The live book recorder is the only way to capture the info-discovery
  window** for future days. The WS daemon we just started will fill this gap
  going forward.

## Why april-10 looks "already over" at 15:18 EDT

april-10 was a 58-59°F day at LGA. By 15:00 EDT on a cool day, the high has
usually already been set (the 2:00–3:00 PM peak has passed). The market
naturally collapses onto the realized bucket as the temperature trajectory
becomes deterministic. This is a structural feature of daily-temp markets
on cool days — the market resolves *informationally* a few hours before
resolution *time* because the peak temperature is already observable.

**Hot days should resolve later** — because the peak can still happen as
late as 17:00 EDT. The april-11, -12, and -13 data we are pulling will
test this: we should see the favorite solidify later on warmer days.

## Followups

- Run the same analysis on april-11 tonight after resolution (we have 1-min
  coverage for the full info-discovery day since the data starts 15:16 EDT
  yesterday).
- Measure: for each day, at what clock time did the winning bucket first
  pass 0.5? First pass 0.9? This gives us a "time-to-information" measure
  per day.

# Exp E — does the Polymarket price react to METAR temperature readings?

**Script**: `expE_temp_price_lag.py`
**Date**: 2026-04-11
**Status**: **BIG finding**. The market does NOT react to individual METAR
hourly readings — it reprices based on FORECASTS, not observations. The
only time temperature readings move price is late in the day when the
realized trajectory converges on the resolution bucket.

## Setup

Joined fresh METAR LGA hourly readings (46 rows, 2026-04-10 18 UTC through
2026-04-11 18:51 UTC) to the april-11 1-min price data. For each hourly
reading, event-studied the bucket containing `round(tmpf)` at t-5, t-1, t0,
t+1, t+5, t+10 minutes relative to the reading.

## Observations

### (1) Market favorite mostly diverges from realized temp during the day

Through the morning + midday of 2026-04-11, temperatures climbed:

| UTC (EDT)   | tmpf  | fav bucket | fav_p |
|-------------|-------|------------|-------|
| 02:51 (22 EDT-1) | 53°F | 62-63°F | 0.475 |
| 05:51 (01 EDT)   | 57°F | 62-63°F | 0.385 |
| 09:51 (05 EDT)   | 55°F | 62-63°F | 0.465 |
| 12:51 (08 EDT)   | 52°F | 62-63°F | 0.575 ← peak conviction |
| 14:51 (10 EDT)   | 54°F | 62-63°F | 0.415 |
| 15:51 (11 EDT)   | 56°F | 60-61°F | 0.480 ← flip |
| 16:51 (12 EDT)   | 57°F | 60-61°F | 0.490 |
| 17:51 (13 EDT)   | 59°F | 60-61°F | 0.480 |
| 18:51 (14 EDT)   | 60°F | 60-61°F | 0.590 ← late surge |

**For ~13 hours (02–15 UTC), the favorite was 62-63°F** while observations
sat in the 49–56°F range — 6-13 degrees below the market's mode. The market
was pricing a FORECAST of ~62°F afternoon high, not reacting to the readings.

Then at 15:51 UTC (11 EDT), the favorite flipped to 60-61°F. By 17-18 UTC
as temperatures hit 59-60°F, the 60-61°F bucket was firming at 0.59.

**So the question "does the market react to METAR readings?" has a two-
part answer:**
- **Early in the day: No.** The market prices the forecast. Single hourly
  readings below forecast don't move price materially.
- **Late in the day (past ~12 EDT): Yes — but only in the direction of the
  realized high.** Once the trajectory converges toward a specific bucket,
  that bucket gets bought up.

### (2) Event-study shows near-zero immediate response

The event study (p_yes of the bucket containing current tmpf at t ± 5 min)
shows NO systematic post-reading price move during the morning/midday. The
market is either:
- Already pricing the full-day peak forecast (so a morning reading ≠ high has
  no information value), or
- Too thin to reprice on cold readings that don't challenge the forecast

### (3) When the reading moves the market, the move is 5-10 minutes later

The one clean example: **18:51 UTC, tmpf = 60°F** (first reading that hits the
60-61 bucket):
- t-5: 60-61 = 0.565
- t0:  60-61 = 0.590
- t+5: 60-61 = 0.625
- t+10: 60-61 = **0.680**

That's +11.5 cents in 10 minutes after the reading confirms the bucket
is live. **The reaction lag is ~5-10 minutes, not 1 minute.** Consistent
with a human trader noticing the print, not a bot.

### (4) The 20:51 UTC +0.41 jump was info-discovery, not METAR reaction

At 20:51 UTC on 2026-04-10 (previous evening), tmpf = 55°F. The 59forbelow
bucket went from 0.14 at t0 to 0.558 at t+10 — a 40-cent surge. Looks like
METAR reaction at first glance, but actually this was during the **17:00 EDT
previous-evening info-discovery peak** from exp C. The 55°F reading was
coincident with but not causing the repricing — traders were digesting the
next-day forecast, not this evening's observation.

## Interpretation — what's driving price?

**The market is moved by forecasts, not observations.** The forecast source
is likely one of:
- HRRR (public, hourly updates every 6 h)
- Google search result temperature card (pulls from NWS or third-party)
- OpenWeatherMap / Weather.com widgets
- A private model (Jane Street / Kalshi MM / etc.)

The fact that the market was stuck at 62-63°F all day despite reading
5-10°F lower is strong evidence of a model-driven pricing — real-time
temperature data is being ignored in favor of the modeled afternoon peak.

**Actionable question**: the forecast was wrong by ~3°F. HRRR probably
said 62°F for LGA peak at 12 UTC; actual peak so far is 60°F at 14 EDT,
still possibly climbing but unlikely to hit 62. If Strategy D V1 bought
the +2 bucket (favorite + 2 = 64-65 at the 12:51 UTC reading when fav was
62-63), it would be losing right now. But if Strategy D V1 bought the +2
bucket at the 15:51 UTC flip (favorite 60-61 → +2 = 64-65), still losing.

**If we had been running Strategy D V1 on april-11 today, it would have
lost.** The actual winner looks likely to be 60-61°F, which is the
CURRENT favorite — not +2. Strategy D V1 depends on the favorite being
under-priced by 2 buckets; today the favorite is roughly right.

This is one data point; it doesn't invalidate Strategy D. But it's a
warning that on days when the morning reading trajectory is clearly
below forecast, the +2 heuristic is catching a falling favorite, not a
rising one.

## Candidate edge identified

**"Disagree with the forecast" strategy variant**: if the METAR trajectory
over the past 6 hours shows temperatures running ≥3°F below the current
market favorite's lower bound, consider shorting the favorite (or skipping
Strategy D entry) rather than buying the +2.

This is a new variant that requires a day-of morning-trajectory feature.
Will flesh out as a full backtest once we have METAR for more trading
days. **Priority 1 for this loop's next iteration.**

## Followups

- exp F: full replay of today's favorite drift once april-11 resolves tonight
- exp H: "METAR trajectory vs favorite" as a Strategy D filter — does it
  improve hit-rate on the existing Strategy D V1?
- Find out which forecast the market is actually tracking: compare the
  market favorite at 12 UTC to HRRR's 12 UTC run's LGA peak prediction,
  to NWS's gridded forecast, to OpenWeatherMap. Whichever matches the
  market mode is the forecast they're using.

# Exp C — Where does the volatility live? (per-day, per-bucket, per-hour)

**Script**: `expC_volatility_regimes.py`
**Date**: 2026-04-11
**Status**: Big one. The 1-min data reveals where the info-discovery is
happening: specific buckets, specific hours, specific days. 17-cent 1-min
moves are observed on thin buckets — the book is essentially one person
walking the ladder. This suggests a **mean-reversion scalping edge** on
thin buckets is plausible.

## Method

For each of the 4 days with 1-min coverage, compute 1-min returns of every
bucket, sum |Δp| totals, bucket-level stddev, and hour-of-day aggregates.

## Findings

### (1) Days are not created equal

Total 1-min |Δp| across all buckets of a day:

| day           | n_steps | total_|Δp| | avg bp/min |
|---------------|---------|------------|------------|
| april-10      |  6263   |  0.59      |  0.94      |
| april-11      | 15286   | **24.40**  | **15.96**  |
| april-12      | 15058   | 13.62      |  9.04      |
| april-13      | 14811   | 12.29      |  8.30      |

**april-11 (today's resolution day) is 17x more active than april-10** (the
day that already resolved). april-12 and april-13 (2–3 days out) have
moderate activity. This matches intuition: information flows most about the
day-closest-to-resolution that isn't locked in yet.

### (2) Volatility concentrates in the "favorite neighborhood"

For april-11:
- **59forbelow**: std = 163 bp/min, max single-step = 17.45 cents, avg_p = 0.12
- **60-61f**: std = 112 bp/min, max = 13.50 cents, avg_p = 0.34 (the current favorite)
- **62-63f**: std = 108 bp/min, max = 14.00 cents, avg_p = 0.39 (near-favorite)
- **64-65f**: std = 59 bp/min, avg_p = 0.12 (+2 from favorite)
- **66–78f**: std < 20 bp/min, avg_p < 0.02 (tails, nearly motionless)

Same structure for april-12 and april-13 — the top ~5 buckets around the
current favorite carry ~95% of the daily |Δp|. Tails are essentially static
because they're already at the tick floor (0.001).

### (3) Hour-of-day: late-afternoon UTC concentration

Hour-of-day |Δp| totals (april-11/12/13 combined):

| hr UTC | total | bp/step |
|--------|-------|---------|
| **21** | 3.52  | **18.66** |  17 EDT — day BEFORE resolution peak repricing
| **18** | 2.99  | 15.60   |  14 EDT — midday repricing
| **22** | 2.88  | 15.70   |
| **14** | 2.83  | 15.13   |  10 EDT — morning HRRR update
| **16** | 2.80  | 14.56   |
| **15** | 2.36  | 12.62   |
| **23** | 2.36  | 12.33   |
| 01     | 1.06  | 5.66    |  (overnight, least active)

**Peak activity is 21 UTC = 17 EDT** — the late afternoon of the day-before-
resolution. This is BEFORE market close / resolution. That's the
information-discovery peak.

Secondary peaks at 14 UTC (~10 EDT morning HRRR run) and 18 UTC (~14 EDT
midday) — consistent with model-update and human-trader sessions.

### (4) Biggest single 1-min moves — all on thin buckets

Top-20 moves are ALL on april-11 in the 59forbelow / 60-61 / 62-63 buckets,
all between 2026-04-10 20:57 UTC and 2026-04-11 02:05 UTC (the 9-hour window
~25h before today's resolution). Sizes: 17, 15, 15, 14, 13 cents in a single
minute.

These are consistent with one thin-book trader walking a slice of the order
book from midpoint to some target, bouncing back, repeating. The pattern of
**oscillating big moves in the same bucket** (e.g. 59forbelow saw +15, -14,
+15, -13 in consecutive ~hour windows) is textbook "one actor pushing,
another pushing back, and neither has deep liquidity behind them."

## Implications — three candidate edges

### Edge 1: Thin-book mean-reversion scalping

When a single 1-min move on the 59forbelow bucket is >10 cents and the book
is thin, the move is typically reverted within the next 5-10 minutes. We could
place counter-trend limit orders at the post-move midpoint ± 1 cent and catch
the revert.

**Feasibility check needed**: do we have enough capital relative to book
depth to matter? A 1000-share limit order at 0.01 tick size is $10 — well
below book sizes. **Probable edge. Priority 1 for exp D.**

### Edge 2: Evening-before-resolution repricing

21 UTC peak = 17 EDT the day BEFORE resolution. If we combine this with
access to a 12 UTC HRRR run that predicts next-day max temperature with
accuracy beating the market favorite, we can front-run the 17 EDT repricing.

This is essentially the hypothesis in exp41 but moved from "day-of-
resolution" to "evening-before". **Priority 2** — requires joining HRRR
forecasts against next-day market movement.

### Edge 3: Short the ladder when overround > 5c

Exp A showed april-13 averages 1.052 (5.2c overround). Selling all 11 YES
tokens at midpoint gives a synthetic $1 - cost short. In practice we'd need
to check real asks (book data incoming). **Priority 3** — waiting for real
book data to verify ask ≤ midpoint + 1c.

## Followups

- Run the favorite-drift analysis for april-11 AFTER resolution (tonight)
  to see the full info-discovery trajectory of a single day.
- Join the 1-min price data against fresh METAR hourly temperature readings
  (downloading in background now) to check the temperature→price lag.
- Use the accumulating WS book data to compute real ask prices as soon as
  we have several hours of coverage.

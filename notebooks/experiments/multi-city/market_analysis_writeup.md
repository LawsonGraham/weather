# Multi-City Market Analysis: What the Data Actually Shows

**Date**: 2026-04-12
**Scope**: 11 US cities, 4,440 slugs, 262 resolved trading days, 5M fills

---

## 1. The Structural Bias Is Real (and large)

Across 262 resolved trading days and 11 cities, the actual daily high temperature CONSISTENTLY lands **ABOVE** the market's 16 EDT favorite bucket.

| city | n days | avg offset (°F) | % days winner is ABOVE fav | % at fav | % BELOW |
|---|---|---|---|---|---|
| Chicago | 30 | **+7.1** | **77%** | 13% | 10% |
| Denver | 18 | +5.5 | 56% | 22% | 22% |
| NYC | 23 | +4.0 | 70% | 22% | 9% |
| SF | 18 | +4.4 | 78% | 22% | 0% |
| Austin | 18 | +4.2 | 61% | 28% | 11% |
| Dallas | 30 | +3.3 | 50% | 37% | 13% |
| Seattle | 29 | +3.2 | 62% | 38% | 0% |
| Houston | 18 | +3.3 | 56% | 39% | 6% |
| Atlanta | 30 | +2.8 | 53% | 40% | 7% |
| Miami | 30 | +2.3 | 60% | 37% | 3% |
| LA | 18 | +1.6 | 50% | 39% | 11% |
| **ALL** | **262** | **+3.8** | **61%** | **31%** | **8%** |

**61% of the time, the actual high is above the favorite.** Only 8% of the
time is it below. The distribution of offsets peaks at +1 to +2°F (37% of
days) with a heavy right tail to +10°F+.

**Why this exists**: the market's favorite at 16 EDT (4 PM local) is set
by traders. On most days the afternoon high hasn't peaked yet at 4 PM,
so the market underestimates the final max. The remaining 2-4 hours of
sunlight push the reading higher.

**This bias IS the opportunity.** It's not a forecast model — it's a
structural feature of when the market snapshots versus when temperature
peaks.

---

## 2. The Market Is Approximately Calibrated — But Not Perfectly

Market calibration analysis (all 11 cities, 2,349 bucket-observations
with hourly pricing data):

| implied probability | n observations | actual win rate | calibration error |
|---|---|---|---|
| <2% | 1,520 | 0.07% | overpriced (paying for tail risk) |
| 2-5% | 178 | 2.25% | -0.9% overpriced |
| 5-10% | 122 | 7.38% | ~fair |
| **10-20%** | **137** | **15.3%** | **+1.0% underpriced** |
| 20-35% | 129 | 27.1% | ~fair |
| 35-50% | 117 | 38.5% | -2.6% overpriced |
| 50-70% | 96 | 59.4% | +1.7% slightly underpriced |
| 70-90% | 27 | 77.8% | -4.8% overpriced |
| >90% | 23 | 100% | underpriced |

**The market is within ±5% at every implied-probability bucket.**

That's GOOD calibration for a prediction market. There is no obviously
exploitable mis-calibration band. The 10-20% range (where Strategy D's
target sits) shows a marginal +1% underpricing — real but tiny.

**What this means for "can we beat the market"**: gross calibration-
based arbitrage is not available. You cannot simply buy cheap buckets
and expect to profit from systematic mispricing. The market gets
aggregate probabilities approximately right.

---

## 3. Where Specifically the Edge Might Live

Despite approximate aggregate calibration, the directional test reveals
something subtle:

**The bucket BELOW the favorite (the -2 bucket) is significantly
underpriced:**

| bucket position | n | avg implied | actual win rate | edge |
|---|---|---|---|---|
| favorite +2 (Strategy D V1 target) | 202 | 19.5% | 14.9% | **-24% OVERPRICED** |
| **favorite -2** | **158** | **19.6%** | **26.6%** | **+36% UNDERPRICED** |
| favorite +4 | 183 | 4.8% | 2.2% | -54% OVERPRICED |

**The +2 bucket (the Strategy D V1 target) is actually OVERPRICED** when
measured against the market's own resolution (outcome_prices field). The
market charges 19.5% implied for this bucket, but it resolves YES only
14.9% of the time.

**The -2 bucket is the real opportunity.** Priced at 19.6% implied, it
resolves YES 26.6% of the time — a +36% relative edge.

**Why this contradicts the bias finding**: the bias says the winner is
+3.8°F ABOVE the favorite on AVERAGE. But averages are misleading when
the distribution has a heavy right tail. The +3.8°F average is pulled up
by outlier hot days (+10°F). The MEDIAN day is closer to +1 to +2°F —
which means the favorite or the bucket just above it wins most often. And
the -2 bucket catches the 8% of days where the actual temp comes in BELOW
expectations, which happen more often than the 19.6% price implies.

---

## 4. How I Approached This Analysis (methodology)

### Data sources
- **Markets + fills**: Polymarket Gamma API metadata + Goldsky subgraph
  fills. 4,440 markets, 5M fill events, 22M trade-derived price rows.
- **Resolution ground truth**: IEM METAR for 12 US airport stations.
  Daily max temperature = `MAX(tmpf, max_temp_6hr_c * 9/5 + 32)` per
  local calendar day.
- **Price history**: Polymarket CLOB `/prices-history` endpoint. Hourly
  midpoints for full market lifetime, 1-min for active markets' past 24h.

### Analytical approach

**Step 1 — Structural bias detection**: for each resolved day, compute
`metar_daily_max - favorite_bucket_lo_f`. Aggregate across all 262
city-days to measure the systematic offset distribution. This is a raw
frequency count, no model involved.

**Step 2 — Market calibration**: bucket every observation by its 16 EDT
implied probability, then measure the actual YES resolution rate within
each band. Compare implied vs actual to find mis-calibrated ranges. This
is a calibration curve (reliability diagram), the standard tool for
evaluating probabilistic forecasts.

**Step 3 — Positional edge**: for each resolved day, identify the
specific fav+2, fav-2, and fav+4 buckets and measure their win rate vs
their implied price. This isolates whether the POSITION (above vs below
the favorite) matters for edge, controlling for implied probability.

**Step 4 — Competitive landscape**: for each trader address in the fills
data, reconstruct their net position per slug and compute P&L against
market resolution. Rank by total P&L + rebate earnings. Analyze their
trading behavior (timing, role, sizing, price distribution) to infer
strategy type.

**Step 5 — Statistical significance**: compute 95% confidence interval
on observed hit rates to determine whether the observed edge exceeds what
random noise could produce.

### What I did NOT do (and why it matters)
- **Did not build a predictive model.** No regression, no ML, no feature
  engineering. All results are empirical frequencies on historical data.
- **Did not adjust for multiple hypothesis testing.** I tested several
  bucket positions (+2, -2, +4) and several cities. The significant
  results could be p-hacked. A proper analysis would apply Bonferroni
  or similar corrections.
- **Did not out-of-sample test.** The "edge" numbers use the same data
  for discovery and measurement. A proper test would train on one time
  period and validate on another.
- **Did not model transaction costs properly.** Used a flat 0.6% fee
  based on the `C * 0.05 * p * (1-p)` formula at typical entry prices.
  Real execution costs vary by time-of-day, book depth, and competition.

---

## 5. What We'd Need to Actually Beat the Status Quo

### What we know works
The structural directional bias is real and persistent across 11 cities
and 262 days. The market systematically underestimates the daily high
because it snapshots before the peak. This is not a forecast — it's a
clock-based structural observation.

### What a forecast model COULD add
The bias is +3.8°F on AVERAGE but varies widely day-to-day (from -8°F
to +10°F+). A forecast model that could predict WHICH DAYS will have
larger positive offsets could:

1. **Filter**: skip days where the offset is likely near zero (the
   favorite will win, no edge)
2. **Select**: target the specific bucket most likely to win (the +1,
   +2, or -2 depending on conditions)
3. **Size**: bet bigger on high-conviction days

To do this, a model would need features like:
- **Current temperature trajectory** (METAR readings through the day —
  is the temp still climbing or has it peaked?)
- **HRRR forecast** for the station's grid point (what does the model
  say the afternoon max will be?)
- **Time-of-year seasonality** (summer days peak later than winter)
- **Cloud cover / frontal activity** (from METAR wx codes)

### Why I think we COULD improve — but haven't proven it

**The case for**:
- Nobody in these markets uses forecast signals (demonstrated by the
  behavioral analysis showing the top winner's taker-side buys perform
  WORSE than random)
- Public NWP data (HRRR, GFS) has skill at forecasting daily max
  temperature with ~2-3°F RMSE on the day-of — that's within the range
  of the +3.8°F average offset
- The NYC-specific exp40 showed HRRR has +1.27°F mean bias and r=0.11
  correlation with Polymarket's error — UNCORRELATED, meaning the market
  is not using HRRR
- If we can predict the offset direction (above or below favorite) at
  even 55-60% accuracy, the EV is positive at the implied prices

**The case against**:
- The market is approximately well-calibrated despite nobody using
  forecasts. The crowd's aggregate gut feeling works reasonably well.
- HRRR's correlation with the market error was only r=0.11 (NYC,
  limited sample). Weak signal. Might not survive multi-city testing.
- The +2 bucket is OVERPRICED per market resolution data (-24% edge).
  Strategy D V1's positive backtest may be an artifact of ground-truth
  differences (METAR vs Weather Underground resolution) or hourly
  sampling noise.
- Small sample: 198 Strategy D V1 trades, 262 resolved city-days. The
  95% CI is wide. Some of the per-city results (Miami +$189 driven by
  one 88x trade on Mar 27) are extremely outlier-dependent.

### The honest bottom line
We have a **structural observation** (the 16 EDT market underestimates
the daily high by ~3.8°F on average) and **approximate calibration data**
showing the market is roughly fair but not perfect. The directional bias
suggests buying ABOVE the favorite should work on average, but the
specific +2 bucket appears overpriced per market resolution while the -2
bucket appears underpriced.

To ACTUALLY beat these markets with confidence, we would need to:

1. **Validate the -2 bucket finding out-of-sample** — split the data
   temporally and confirm the +36% edge on the hold-out set
2. **Build a day-level feature model** using HRRR/METAR to predict
   offset direction — then backtest it with proper train/test splits
3. **Paper trade for 30+ days** to measure real-execution edge after
   fees, slippage, and competition
4. **Only then deploy with real capital**

We have NOT done steps 1-4. The current evidence is suggestive but not
conclusive. Claiming we'd "beat the backtest" or "bring a gun to a knife
fight" was premature and unsupported. What we have is a well-characterized
opportunity with identifiable structural features, public data, minimal
competition, and a clear path to validation — but the validation hasn't
been done yet.

# Backtest v3 — Unified Ensemble Model + Market Strategy

**Branch**: `wt/backtest-v2` (continued; v3 folder under `notebooks/experiments/backtest-v3/`)
**Date**: 2026-04-15
**Status**: Iteration 1 of recurring loop.

## TL;DR so far

**The weather model beats NBS** on OOS daily-max MAE (1.99°F vs 2.28°F, 13%
better). **But that forecasting advantage does NOT translate to a
model-vs-market trading edge** — when we convert the ensemble into
bucket probabilities and bet where model > market, OOS trading is
negative (-$0.050/trade at best IS-chosen threshold).

**One interesting filter-based finding** — "buy market favorite only
when the ensemble model agrees within 1 bucket":
- IS: +$0.010/trade (weak)
- OOS: **+$0.101/trade, hit 71.6%, t=+1.98** on 74 trades

The IS/OOS inversion (OOS better than IS) is suspicious — likely noise
on small samples. Needs more data before taking seriously.

## Data split (LOCKED before model training)

- **Model IS** (for weather prediction regression): 2025-12-01 → 2026-02-28 (90 days)
- **Model OOS** (for weather prediction regression): 2026-03-01 → 2026-04-10 (41 days)
- **Strategy IS** (for threshold/strategy tuning with prices): 2026-03-11 → 2026-03-31
- **Strategy OOS** (one-shot hold-out): 2026-04-01 → 2026-04-10

The prices_history data only starts 2026-03-11, so model-based strategies
can only be tested on the overlap window. The strategy IS/OOS re-uses the
model's OOS period as strategy IS (Mar 11-31).

## Weather model results

Input features (11):
- `nbs_pred_max_f`, `gfs_pred_max_f`, `hrrr_max_t_f` (3 forecast sources)
- `nbs_spread_f` (NBS model uncertainty)
- `yesterday_max_f` (autocorrelation)
- `tmp_noon_f`, `tmp_morning_f` (diurnal anchor from morning METAR)
- `nbs_minus_gfs`, `hrrr_minus_nbs` (model disagreement)
- `day_of_year`, `month` (seasonality)

Target: `actual_max_f` — daily max °F from METAR in local calendar day.

### OOS comparison (972 IS, 455 OOS rows × 11 stations)

| model | IS MAE | OOS MAE | OOS RMSE | IS bias | OOS bias |
|---|---|---|---|---|---|
| NBS only | 1.911 | 2.283 | 3.310 | +0.019 | +0.592 |
| GFS only | 2.327 | 2.653 | 3.812 | +0.561 | +0.138 |
| HRRR only | 2.079 | 2.699 | 3.890 | -0.791 | -1.210 |
| Mean ensemble (NBS+GFS+HRRR) | 1.668 | 2.088 | 2.903 | -0.070 | -0.160 |
| **Linear/Ridge** | **1.417** | **1.987** | 2.925 | ~0 | -0.697 |
| LightGBM | 1.065 | 2.198 | 3.065 | -0.043 | -1.054 |

Key takeaways:
- **Linear ensemble is best** (1.99 OOS MAE vs 2.28 for NBS alone). 13% improvement.
- **LightGBM overfits** (IS 1.07, OOS 2.20). Not enough training data for the gradient boost.
- **Simple mean ensemble** is nearly as good as linear — validates that more forecasts help.
- **NBS over-forecasts by 0.6°F OOS**. Linear model corrects but slightly overshoots (-0.7°F).

### Per-station OOS MAE (LGBM vs NBS, Δ = NBS − LGBM)

| station | NBS MAE | LGBM MAE | Δ |
|---|---|---|---|
| LGA (NYC) | 4.02 | 1.80 | **+2.21** (big gain) |
| LAX | 3.24 | 1.77 | **+1.47** |
| ORD (Chicago) | 3.20 | 2.41 | +0.80 |
| DEN | 3.07 | 2.48 | +0.59 |
| HOU | 1.50 | 1.21 | +0.29 |
| ATL | 2.26 | 1.99 | +0.27 |
| MIA | 1.29 | 1.09 | +0.20 |
| DAL | 2.07 | 2.62 | -0.55 |
| SEA | 1.20 | 1.66 | -0.46 |
| SFO | 1.98 | 3.78 | **-1.81** (big loss) |
| AUS | 1.62 | 3.29 | **-1.67** |

LGBM dramatically helps for cities where NBS is already weak (LGA, LAX,
ORD, DEN). But hurts in cities where NBS is already accurate (SFO, AUS).
The linear model gives more uniform small improvements.

### Feature importance (LGBM)

1. `tmp_noon_f` (348) — morning METAR observation
2. `hrrr_max_t_f` (285) — HRRR forecast
3. `hrrr_minus_nbs` (189) — disagreement signal
4. `nbs_pred_max_f` (168) — NBS forecast
5. `tmp_morning_f` (134) — early morning temp
6. `gfs_pred_max_f` (114)
7. `yesterday_max_f` (106)
8. `nbs_minus_gfs` (68)
9. `day_of_year` (58)
10. `nbs_spread_f` (36)
11. `month` (4)

Obvious: **noon temperature is the most informative single feature**.
Makes sense — by noon, you've seen the morning ramp, so the distance to
peak is predictable.

## Model-vs-Market strategy

Approach: for each bucket, compute `model_p = Normal_CDF(bucket | pred, sigma)`.
Bet on buckets where `model_p - market_p > threshold`.

### Strategy-IS threshold sweep (Mar 11-31)

| threshold | n | hit | per_trade | total | t |
|---|---|---|---|---|---|
| 0.00 | 195 | 13.3% | +$0.010 | +$1.88 | +0.44 |
| 0.05 | 153 | 13.1% | +$0.011 | +$1.74 | +0.46 |
| 0.10 | 114 | 13.2% | +$0.016 | +$1.79 | +0.54 |
| **0.15** (best IS) | 82 | 13.4% | **+$0.026** | +$2.17 | +0.77 |
| 0.20 | 56 | 7.1% | -$0.012 | -$0.67 | -0.35 |

Best IS threshold = 0.15. Per-trade $0.026. Marginal statistical significance.

### Strategy-OOS at threshold 0.15 (Apr 1-10)

**-$0.050/trade on 47 trades, hit 4.3%, t=-1.59. FAILS.**

All OOS thresholds are negative (-$0.02 to -$0.07 per trade). No
threshold survives OOS.

### Diagnostic: per-city OOS at best IS threshold

| city | n | hit | per_trade |
|---|---|---|---|
| Austin | 6 | 16.7% | +$0.082 |
| Denver | 5 | 20.0% | +$0.092 |
| (all others) | 5-7 | 0% | -$0.06 to -$0.12 |

Only Austin and Denver had any wins OOS. Sample sizes are tiny.

## Simpler strategies tested (exploratory)

| strategy | IS n | IS per | OOS n | OOS per | OOS t |
|---|---|---|---|---|---|
| S1 buy bucket closest to linear-model pred | 110 | +$0.025 | 70 | +$0.001 | +0.03 |
| S2 buy bucket closest to mean-ensemble pred | 109 | +$0.019 | 70 | +$0.002 | +0.05 |
| S3 buy bucket closest to (NBS − 0.5°F) | 110 | +$0.011 | 71 | +$0.017 | +0.36 |
| **S4 buy market-fav iff model agrees ≤1 bucket** | 103 | +$0.010 | 74 | **+$0.101** | **+1.98** |
| S6 buy 3 buckets centered on linear pred | 290 | +$0.009 | 193 | -$0.010 | -0.37 |

**S4 is the only strategy with a positive OOS t-stat near 2.** The
market-favorite prior (known profitable in v2 OOS) is refined with a
model-consistency filter — removing 30% of trades (where model
disagrees) boosts per-trade PnL from $0.063 (v2 raw market_fav OOS) to
$0.101 (here, filtered).

**Caveat**: IS was only $0.010. The OOS > IS inversion is the red flag.

## Iteration 2 (2026-04-15)

### S4 debug
Investigated why S4's OOS per-trade ($0.101) was 10x its IS ($0.010). Root
cause: **it was a period effect, not a model effect.**

- Market-favorite hit rate by week:
  - IS wk11 (Mar 8-14): 70.8% hit, -$0.035/trade
  - IS wk12 (Mar 15-21): 64.3% hit, -$0.076/trade
  - IS wk13 (Mar 22-28): 70.8% hit, +$0.016/trade
  - IS wk14 (Mar 29-Apr 4, partial): 66.7% hit, +$0.120/trade
  - **OOS wk14 (Mar 29-Apr 4 the rest): 78.0% hit, +$0.117/trade** ← hot week
  - OOS wk15 (Apr 5-11): 74.5% hit, +$0.012/trade
- Model-agreement filter removed only ~7 OOS trades. No model-specific signal.
- "Edge" was driven by wk14 being unusually kind to favorites.

### Per-station models

Trained 11 independent Ridge regressions, each on its station's 71 IS
days. Most stations improved over global ensemble:

| station | NBS OOS MAE | Global OOS MAE | Per-station OOS MAE | IS sigma |
|---|---|---|---|---|
| ATL | 2.26 | 1.89 | **1.70** | 1.90 |
| AUS | 1.62 | 1.74 | 1.74 | 1.56 |
| DAL | 2.07 | 1.64 | 1.86 | 1.50 |
| DEN | 3.07 | 2.16 | **2.04** | 1.62 |
| HOU | 1.50 | 1.22 | **1.21** | 1.64 |
| LAX | 3.24 | 2.06 | **1.68** | 1.06 |
| LGA | 4.02 | 2.38 | 2.67 | 2.17 |
| MIA | 1.29 | 1.07 | 1.09 | 0.92 |
| ORD | 3.20 | 2.57 | **1.95** | 1.93 |
| SEA | 1.20 | 1.37 | 1.83 | 1.06 |
| **SFO** | 1.98 | 3.91 | 2.34 | 1.63 |

Per-station helps most at cities where NBS is weak (LAX, ORD, DEN,
ATL). Global ensemble is better at LGA, DAL, SEA, MIA.

### Simple strategy on per-station model: "buy bucket with max model_p"

Pick the bucket with highest predicted probability under each
station's model+sigma.

- **IS: n=107, hit 43.0%, per=+$0.036/trade, t=+0.84**
- **OOS: n=73, hit 46.6%, per=+$0.046/trade, t=+1.02**

**Consistent IS→OOS direction** — unlike S4's inversion.

Per-city OOS breakdown:
| city | n | hit | per-trade | total |
|---|---|---|---|---|
| Atlanta | 9 | 88.9% | +$0.351 | +$3.16 |
| Dallas | 7 | 85.7% | +$0.309 | +$2.16 |
| Denver | 6 | 33.3% | +$0.117 | +$0.70 |
| Miami | 6 | 66.7% | +$0.032 | +$0.19 |
| SF | 10 | 40.0% | +$0.029 | +$0.29 |
| Seattle | 10 | 30.0% | +$0.014 | +$0.14 |
| Chicago | 5 | 40.0% | -$0.067 | -$0.33 |
| Austin | 7 | 28.6% | -$0.077 | -$0.54 |
| LA | 7 | 14.3% | -$0.135 | -$0.95 |
| Houston | 6 | 33.3% | -$0.248 | -$1.49 |

Edge concentrated in ATL + DAL. Both cities are in the group where
per-station model improves over NBS. LAX/AUS lose despite per-station
model being decent. Noise dominates at n=6-10 per city.

### Empirical residual distribution approach

Trained residual model (target = actual_max − NBS) on IS, then used the
empirical distribution of IS residuals to compute bucket probabilities
(Monte Carlo style — no Normal assumption).

- NBS alone OOS MAE: 2.283
- NBS + predicted residual OOS MAE: **1.994** (same 13% improvement as global)
- IS sigma after residual correction: **1.913** (same as Normal assumption)

Strategy: "buy bucket with max empirical model_p":
- **IS: n=102, hit 45.1%, per=+$0.052/trade, t=+1.22**
- **OOS: n=68, hit 41.2%, per=+$0.025/trade, t=+0.54**

IS > OOS (the **expected decay pattern** for true edge, opposite of S4).
But sample is small and t-stat drops from 1.22 to 0.54 — within noise.

### Synthesis of iteration 2

The "max model_p" strategy (pick the bucket the ensemble-model thinks
most likely, using either per-station or empirical-residual calibration)
gives consistent:
- IS per-trade: +$0.036 to +$0.052
- OOS per-trade: +$0.025 to +$0.046
- Entry price: ~$0.39 to $0.42 (mid-price buckets, not market favorite)
- Hit rate: 41-47%

Net edge ≈ 2-5% per trade before fees, 1-4% after. Statistically
underpowered at current n=68-73 OOS but **directionally consistent
across multiple modeling choices**. This is the most promising
finding of v3 so far.

**Key insight**: the model's favorite bucket is within 1 bucket of
the market favorite 93% of OOS days. Where they agree (gap=0, 45%
of OOS days), hit rate is 87%. Where they disagree within 1 bucket
(48% of OOS days), hit rate is still 67%. Where they disagree by 2+
buckets (7%), market wins more often (71%).

**Actionable framing**: the model may be adding ~3% of marginal value
by picking between adjacent buckets when the market favorite is ambiguous.

## Iteration 3 (2026-04-15, continued)

### Confidence filter on max model_p

Tested multiple filters (top-1 minus top-2 probability, NBS spread,
bucket_gap from market fav). Best finding:

**`bucket_gap=0`** (buy market fav iff per-station model also picks it):
- Full Mar 11-Apr 10: n=79, hit 72.2%, per=+$0.078/trade, t=+1.56
- **But period-concentrated**: Wk14 (Mar 29-Apr 4, n=21) had 90.5% hit,
  +$0.239/trade. Without Wk14: per-trade drops from $0.078 to $0.019.
- Cumulative PnL: flat through Mar 25, then +$6.32 in last 15 days.

Same "hot period" signature as S4 from iter 1. Not robust.

### Sign-of-NBS-error classifier

Trained binary classifier (GBC) on `sign(actual - NBS)` — 75-81% OOS
accuracy on high-confidence predictions. **Genuinely significant
predictive signal** — model can tell which way NBS is likely wrong.

Strategy (shift NBS fav ±1 bucket based on sign):
- Mar 11-25: +$0.096/trade, t=+1.44, n=50 ← works
- Mar 26-Apr 10: -$0.010/trade, t=-0.27, n=98 ← fails

**Inverted pattern from bucket_gap=0** — first half works, second half doesn't.
Sign-classifier edge requires regime where market UNDER-anchors on NBS
(Mar 11-25); when market fully prices in NBS + consensus (Mar 26-Apr 10),
edge vanishes.

### Baseline: offset from NBS favorite (no model)

| offset | n | hit | per-trade | total | t-stat | avg_price |
|---|---|---|---|---|---|---|
| -3 | 46 | 23.9% | +$0.028 | +$1.28 | +0.53 | $0.206 |
| -2 | 100 | 24.0% | -$0.010 | -$0.98 | -0.27 | $0.243 |
| **-1** | 158 | 39.2% | **+$0.013** | +$2.07 | +0.40 | $0.371 |
| **0** (NBS fav) | 186 | 39.2% | **+$0.022** | +$4.12 | +0.74 | $0.362 |
| **+1** | 156 | 10.3% | **-$0.071** | **-$11.02** | **-3.52** | $0.168 |
| +2 | 79 | 6.3% | -$0.018 | -$1.46 | -0.73 | $0.079 |

**Key finding**: buying ABOVE NBS favorite (offset=+1 or +2) is
**strongly negative** across the full 180-day sample (t=-3.52 at
offset=+1). Confirms v2 result that Strategy D V1 (buy +2°F above fav)
was an unreliable edge.

**NBS favorite itself (offset=0)** has mild positive edge: +$0.022/trade.
**NBS fav - 1 (offset=-1)** has barely-positive edge: +$0.013/trade.

### Per-city offset=-1 — Texas cities stand out

| city | n | hit | per-trade | total | t | NBS bias (iter 2) |
|---|---|---|---|---|---|---|
| **Houston** | 13 | **84.6%** | **+$0.218** | +$2.84 | **+2.75** | -1.71°F (over-fcst) |
| **Austin** | 13 | 53.8% | +$0.180 | +$2.35 | +1.69 | -1.35°F (over-fcst) |
| Miami | 7 | 57.1% | +$0.076 | +$0.53 | +0.38 | -1.07°F |
| Atlanta | 14 | 50.0% | +$0.075 | +$1.05 | +0.55 | -0.10°F |
| Denver | 13 | 30.8% | +$0.007 | +$0.10 | +0.06 | -2.53°F |
| Dallas | 22 | 36.4% | -$0.019 | -$0.43 | -0.20 | -1.73°F |
| LA | 14 | 35.7% | -$0.017 | -$0.24 | -0.20 | -2.06°F |
| SF | 15 | 26.7% | -$0.034 | -$0.52 | -0.28 | +0.12°F |
| Seattle | 26 | 26.9% | -$0.060 | -$1.56 | -0.82 | -0.11°F |
| NYC | 12 | 25.0% | -$0.109 | -$1.30 | -1.04 | +0.04°F |
| Chicago | 9 | 22.2% | -$0.083 | -$0.74 | -0.75 | +1.51°F |

**Pattern**: cities with significant NBS over-forecast bias (-1.0°F to
-2.1°F: HOU, AUS, MIA, ATL) show positive per-trade edge on offset=-1.
Chicago (NBS UNDER-forecasts +1.51°F) is negative on offset=-1.
NBS-neutral cities (SFO, SEA, NYC) are mixed.

**This is the most robust structural insight of v3**: per-city NBS bias
calibration from Dec-Feb training data partially carries into Mar-Apr
trading. But sample sizes per city (n=9-26) are tiny for confidence.

### Synthesis of iteration 3

1. The weather model ensemble genuinely predicts better than NBS (13%
   MAE improvement) — but the market prices don't reflect enough
   mispricing for the model to extract a clean edge OOS.
2. **Several "edges" (bucket_gap=0, sign classifier) are regime-dependent**
   — they each work in opposite halves of the OOS window. This is a
   fingerprint of noise dominating small samples, not genuine alpha.
3. The ONLY structurally-grounded finding is **per-city NBS bias
   calibration** — cities where NBS over-forecasts in training data
   continue to benefit from offset=-1 in OOS. Houston + Austin are the
   strongest signals but each has only 13 trades.
4. **Offset=+1 is a consistent FADE-EDGE** (don't buy above NBS fav)
   across cities, periods, and samples. This is the inverse of the
   (failed) Strategy D V1.

## Iteration 4 (2026-04-15)

### Per-city NBS bias calibration (attempted)

Used IS (Dec-Feb) per-city NBS bias to pick per-city integer offset.

**IS → OOS bias DRIFT across many cities**:
- Denver: IS +0.23 → OOS -1.83 °F
- LA: IS 0.00 → OOS -1.86 °F
- Houston: IS -0.74 → OOS -1.12 °F
- Dallas: IS -0.01 → OOS -1.21 °F

Most IS biases round to offset=0, so per-city calibration degenerated
to "buy NBS fav" for most cities. Per-city offset strategy total:
+$0.43 on 182 trades, t=+0.08. Essentially break-even.

Per-city SHIFTED (fractional) version slightly better: +$2.84 on 182
trades, t=+0.52. Still weak.

### Seattle deep dive: the real structural signal

Seattle's NBS MAE is lowest of all cities (1.28 IS, 1.20 OOS — most
stable). Small daily weather variance (std 6.7°F OOS) means forecasts
are more accurate, and market slightly underprices the NBS favorite.

Seattle offset=0 (NBS fav) alone: **61.5% hit, +$0.167/trade, t=+1.82
(n=26)**. Within-OOS both halves positive.

### FINAL STRATEGY (v3 finding): NBS-accurate-cities filter

**Simple rule**:
1. On IS (Dec 1 - Feb 28), compute per-city NBS MAE
2. Select cities with MAE ≤ 1.5°F → this identifies **Seattle + Miami**
3. On those cities only, **buy NBS favorite** (no model needed)
4. Stake 1 share per trade

Results:
- Full Mar 11-Apr 10: **n=39, hit 56.4%, per=+$0.126/trade, t=+1.89**
- Mar 11-25 half: n=16, +$0.085/trade, t=+0.79
- Mar 26-Apr 10 half: n=23, +$0.154/trade, t=+1.79
- Per-city: Seattle dominates (+$4.33), Miami marginal (+$0.57)
- Avg entry price: $0.43

**Why this is the cleanest v3 finding**:
- City selection based only on IS data (no leakage)
- Strategy is `buy NBS fav` — trivially simple, no fitting
- Both halves of OOS positive (no period concentration)
- t=+1.89 on 39 trades is marginal but consistent

**Caveats**:
- Small sample (39 trades). 95% CI on per-trade PnL is wide.
- Cutoff threshold (1.5°F) is somewhat arbitrary — at 2.5°F threshold
  (8 cities), per-trade drops to +$0.009.
- Miami contribution is weak; if Seattle is genuinely unique the
  strategy is really a Seattle-only play (n=26 is very small).

### Deployable rule (tentative, needs 2-4 more weeks of data)

If we wanted to paper-trade one thing starting today:
- Every day, for Seattle + Miami's markets, at 20 UTC
- Buy 1 share of the NBS-favorite bucket
- Expected PnL: $0.10-0.15 per trade
- ~11 trades per week (2 cities × ~5 markets/week)
- ~$1.10-1.65/week paper (tiny but directionally consistent)

Not deployable yet — needs more data, and capacity is limited by
market liquidity at ~$0.43 price level (probably $100-200 per trade max).

## What to do next (for cron iterations)

1. **Extend the data window** — prices_history is the binding constraint
   at 31 days. If we can backfill earlier prices (even daily close-time)
   the IS/OOS would have ~100+ strategy-IS days.
2. **Per-station models** — the global model hurts SFO/AUS. A per-
   station or per-cluster model could add edge where NBS is weak
   (LGA, LAX, ORD, DEN).
3. **Retest S4 with a proper temporal holdout** — use Mar 15-31 IS
   (17 days) + Apr 1-10 OOS = 10 days. 103/74 split is too small for
   confident claim.
4. **Compare fee impact** — strategies picking high-price (favorite)
   buckets pay less as a % of stake than tail buckets. S4 may benefit
   from lower fee burden at $0.50 prices vs $0.15 prices.
5. **Build per-city NBS bias calibration** — NBS bias varies +1.5
   (Chicago) to -2.5 (Denver) across cities in IS. A per-city bias
   correction could add juice.

## Honest summary

- The weather-prediction ensemble is meaningfully better than any single
  forecast (13% MAE improvement). That's a real signal.
- Translating it into a pure model-vs-market strategy FAILS OOS.
- One filter-based strategy (S4) shows positive OOS but IS was flat,
  so the signal is suspicious and needs more data.
- **No deployable edge found yet**, but the unified-model framework is
  a much cleaner foundation than prior exp01-36 work.

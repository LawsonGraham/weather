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

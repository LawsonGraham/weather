---
name: model-training
description: "Reference for model training conventions in this repo — time-based splits, calibration evaluation, per-airport vs pooled models, probabilistic outputs, backtest rigor. Use when designing or reviewing training code, hyperparameter choices, or evaluation pipelines."
allowed-tools: Read, Grep, Glob
---

# Model training conventions

## North-star metric

This is a trading project. The target metric is **edge vs market-implied probability**, NOT RMSE vs TAF.

- Predict `P(high > threshold)` or other binary market-resolution probabilities — not just point forecasts.
- Evaluate calibration (reliability curves, Brier score, log loss) separately from accuracy.
- Only trade when `|edge| > transaction_cost_threshold`.

## Splits

**Time-based only. Never random.**

Default split for ~1 year of training data:
- Train: first 8 months
- Val: next 2 months
- Test: last 2 months

For more robust eval, use purged block-based cross-validation — fixed-length blocks with a gap between train and val to prevent leakage through autocorrelation.

## Targets per market type

| Market | Target shape | Model output |
|---|---|---|
| Daily high temperature (Kalshi) | Binary: obs > threshold | Calibrated probability |
| Precipitation occurrence | Binary: any precip in window | Calibrated probability |
| Precipitation timing | Interval window | Distribution over windows |

## Model family

- **Start simple.** XGBoost or LightGBM with proper regularization is the reference model for tabular bias correction on HRRR + METAR features.
- Add deep models only if there is a concrete gap the tabular model cannot close.
- **HRRR ensemble spread** (HRRRx member disagreement) is one of the strongest features for uncertainty-aware outputs. Use it.

## Per-airport vs pooled

- **Per-airport** — trains separately per station. Captures microclimate but has less data per model.
- **Pooled with airport embedding** — shares data across stations, learns airport-specific offsets via a learned embedding.
- **Default for v1: pooled.** More data, simpler infrastructure. Split to per-airport only if pooled underperforms at specific airports.

## Evaluation rigor

- Backtesting must go through a market simulator that models **spreads, slippage, and resolution-window timing** — not a point-in-time probability comparison.
- Report both:
  - Probabilistic metrics: Brier, log loss, calibration ECE, reliability curve
  - Trading metrics: Sharpe, max drawdown, hit rate, edge distribution
- **No data peek.** The test set is the test set. No hyperparameter tuning on test.

## Re-training cadence

- **Daily** for small incremental updates
- **Weekly** for full re-fits
- **Never** during active trading without a shadow eval first

## Common pitfalls

- **Temporal leakage** is the #1 failure mode. See `weather-data` skill for alignment rules.
- **Rare-event class imbalance** — convective events and fog are rare. Consider oversampling or separate regime-specific models.
- **Overfitting to recent weather** — check performance across seasons in the backtest, not just aggregate metrics.
- **Transaction-cost negligence** — a model with "edge" smaller than the bid-ask spread is a losing model.
- **Chasing RMSE** — a model that's more accurate but less well-calibrated will lose to a worse-accuracy but better-calibrated model in a binary prediction market.

## When this skill is invoked

- By architect or implementer when designing or reviewing training code
- By the reviewer subagent when checking for leakage, calibration, or evaluation rigor
- Directly by the user asking training-related questions

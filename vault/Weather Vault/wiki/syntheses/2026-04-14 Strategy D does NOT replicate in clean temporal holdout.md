---
tags: [synthesis, backtest, strategy-d, retraction, overfit, oos]
date: 2026-04-14
related: "[[2026-04-11 Strategy D deployment refinements]], [[2026-04-11 NYC Polymarket upward-bias Strategy D]], [[2026-04-11 Polymarket fee structure + maker rebate pivot]], [[Polymarket]]"
---

# Strategy D does NOT replicate in a clean IS/OOS temporal holdout (2026-04-14)

**Status**: **RETRACTION of Strategy D V1/V2 + deployment refinements.**
A pre-registered 2/3-1/3 temporal holdout (IS: Mar 11-31, OOS: Apr 1-10)
across all 11 US cities shows **no** pre-registered directional
strategy survives out-of-sample. The previously claimed Strategy D V1
edge of +$1.94/trade does not replicate.

This supersedes the enthusiastic "Strategy D is deployable today"
recommendation in [[2026-04-11 Strategy D deployment refinements]].

## The test

- **Pre-registered** 6 strategies (S0-S4 control + offset variants +
  NBS-spread filter) before touching OOS data
- **Discovered 1 exploratory strategy (S6)** on IS-only offset sweep
  with t-stat +2.23 (market-fav −1 offset)
- **One-shot OOS evaluation** — no tuning of strategies based on OOS
  results, no adding new strategies after peeking
- Universe: all 11 US daily-temp cities, entry at 20:00 UTC (16 EDT),
  hold to resolution, fee `C × 0.05 × p × (1-p)`, 1 share per trade

## Headline results

| strategy | tag | IS per-trade | OOS per-trade | verdict |
|---|---|---|---|---|
| S0 NBS fav (control) | — | -$0.003 | +$0.033 | weak, probably period noise |
| S0b market fav (control) | — | -$0.010 | +$0.063 | best OOS, t=1.68, period noise |
| **S1 +2°F offset (NBS)** | PRE-REG | **-$0.028** | **-$0.086** | **FAILS** |
| **S1m +2°F offset (MKT)** | PRE-REG | **-$0.029** | **-$0.086** | **FAILS** |
| S2 +4°F offset (NBS) | PRE-REG | -$0.035 | +$0.026 | OOS marginal, hit rate 6% |
| S2m +4°F offset (MKT) | PRE-REG | -$0.029 | +$0.002 | OOS break-even |
| S3 basket (NBS +2°F & +4°F) | PRE-REG | -$0.031 | -$0.032 | FAILS |
| S3m basket (MKT +2°F & +4°F) | PRE-REG | -$0.029 | -$0.044 | FAILS |
| **S4 NBS-spread filter (2-3°F)** | PRE-REG | -$0.017 | -$0.090 | **FAILS worse OOS** |
| **S6 mkt-fav −1 (exploratory)** | EXP | **+$0.058** | **+$0.001** | **100% edge decay OOS** |

n per strategy per fold: IS 76-271, OOS 49-167 (~130 trades typical).

## What broke Strategy D

Prior results used the FULL available window (Dec 2025-Apr 2026) for
both discovery and evaluation — no temporal holdout. In a clean
Mar 11-Apr 10 subset:

1. **NBS bias flipped**: across our IS, `actual_max − NBS_max = −0.66°F`
   on average (NBS OVER-forecasts, not under). Chicago is the only city
   where NBS under-forecasts (+1.5°F bias). Strategy D assumed the
   opposite — that NBS/market under-forecasts peaks systematically.
2. **Period effect**: Mar-Apr 2026 was apparently cooler-than-NBS-
   predicted in most cities (Denver −2.5°F, LA −2.1°F, Dallas −1.7°F).
   Prior's "warm bias" was likely a Dec-Feb artifact.
3. **Per-city variance is enormous**: even the surviving exploratory
   strategy S6 flipped from a Dallas +$3.49 IS winner to a Dallas
   −$1.04 OOS loser. Prior's "edge transfers to 10 unseen cities"
   geographic-holdout claim was on a DIFFERENT temporal period,
   leaving period-effect unaddressed.

## Why S6 looks instructive

S6 (market-fav −1 offset) had t=+2.23 on IS — a 2-sigma result that
would pass many naive significance tests. Its OOS per-trade PnL
is +$0.001 (t=+0.02). **100% edge decay in 10 days OOS.** This is the
canonical pattern the pre-registered holdout was designed to catch.

## Depth data

Book JSONL recorder started 2026-04-13 — AFTER the backtest OOS window.
**No direct capacity estimate for IS/OOS trades.**

Indicative figures from Apr 13-14 slugs:
- Favorites (p ~$0.40-0.51): ~1000-16000 shares depth within 2¢, median
  fill size 5-156 shares
- +2°F bucket (p ~$0.20): ~6000 shares depth, median fill 100 shares
- +4°F bucket (p ~$0.05): thin, 1000 shares depth, tiny fills

Retail-dominated flow (5-15 share median fills = $0.25-$0.75 per fill).
A few hundred $ of taker stake per slug is absorbable; thousands moves
the market.

## What this retracts

- "Strategy D V1 is the single most reliable edge" — NOT SUPPORTED in
  Mar 11-Apr 10 holdout
- "+$3.40/trade expected PnL" — NOT SUPPORTED
- "Universal upward bias decaying 34% — deploy now" — NOT SUPPORTED;
  the upward bias was a prior-period artifact, not universal
- "Edge transfers to 10 unseen cities, +$377 OOS test PnL" — this was
  a GEOGRAPHIC holdout on the SAME temporal period. Tells us the edge
  is geographically portable in that period. Says NOTHING about edge
  persistence, which is where the decay actually lives

## What survives

Nothing directly deployable. The honest read:

1. **Market is approximately calibrated** on 2°F-wide buckets in Mar-Apr
   2026 across 11 US cities. Favorite's implied probability hits within
   a few pp of realized win rate.
2. **Directional buys at small edge levels are swamped by fees** (peak
   fee at p=0.5 is 1.25% of notional; entry at $0.15 gives a 4%
   effective fee burden).
3. **City-specific biases exist** but are noisy and period-dependent;
   won't support a robust allocation strategy without much more data.

## What to do next

- **Stop paper-trading Strategy D** — it was deployed in good faith on
  the prior result, which doesn't replicate
- **Focus on structural microstructure edges** (maker rebate per
  [[2026-04-11 Polymarket fee structure + maker rebate pivot]])
- **Retrain the daily-max model without training-cutoff overlap** with
  IS/OOS, then evaluate as a proper model-based strategy. Deferred from
  v2 due to TRAIN_CUTOFF=Mar 15 leakage
- **Extend book recorder history** another 14-28 days before any real
  depth/capacity claim
- **Next backtest**: re-run exactly the prior exp32 methodology (ask-
  adjusted entry, entry hour sweep, NYC only) to ISOLATE what drove
  prior's +$1.94/trade headline. This is diagnostic, not a revival

## Diagnostic: NBS forecast bias per city (Mar 11-31, IS)

| city | mean(actual − NBS_pred_max) | n |
|---|---|---|
| Atlanta | −0.10 | 30 |
| Austin | −1.35 | 17 |
| **Chicago** | **+1.51** | 30 |
| Dallas | −1.73 | 30 |
| Denver | −2.53 | 17 |
| Houston | −1.71 | 17 |
| Los Angeles | −2.06 | 17 |
| Miami | −1.07 | 30 |
| New York City | +0.04 | 24 |
| San Francisco | +0.12 | 17 |
| Seattle | −0.11 | 30 |

Chicago is the only city where NBS under-forecasts; all others are
near-zero or NBS over-forecasts. Prior "warm-bias" thesis was wrong
direction for most of the sample.

## Related

- [[2026-04-11 Strategy D deployment refinements]] — predecessor; now
  retracted for Mar 11-Apr 10
- [[2026-04-11 Polymarket fee structure + maker rebate pivot]] — still
  valid; maker-rebate path unchanged
- [[Polymarket]] — parent entity

# Backtest v2 Findings (IS/OOS temporal holdout)

**Date**: 2026-04-14
**Branch**: `wt/backtest-v2`

## TL;DR

**None of the prior "Strategy D" directional edges replicate in a
clean temporal holdout.** When you actually hold out Apr 1-10 as OOS
while developing on Mar 11-31 IS:

- Every pre-registered "+2°F offset" and "+4°F offset" strategy LOSES
  both in-sample and out-of-sample across 11 US cities.
- The one IS-discovered exploratory strategy (buy market-fav -1
  bucket, i.e., "fade the market favorite downward") decayed from
  **+$0.058/trade IS → +$0.001/trade OOS** — exactly the overfit
  pattern the holdout was designed to catch.
- The "best" OOS strategy is `buy the market favorite` at
  +$0.063/trade — but this wasn't statistically significant (t=1.68,
  n=108) and wasn't an edge we were looking for.

**Interpretation**: the edges prior exp01-36 + multi-city offset scan
reported were largely a period-and-city-specific artifact, not a
persistent mispricing. The market across 11 US cities in Mar-Apr 2026
is approximately calibrated on 2°F-wide bucket contracts.

This is a **null result** from a rigorous temporal holdout. Report
it honestly; do not re-tune to make it look better.

---

## Data, split, fees — see [PRE_REGISTRATION.md](PRE_REGISTRATION.md) §1-2

- **IS**: Mar 11-31 2026 (21 days, 126 usable market-days across 11 cities)
- **OOS**: Apr 1-10 2026 (10 days, 102 usable market-days)
- **Ground truth**: `won_yes` from `markets.parquet.outcome_prices[0]==1.0`
  (Polymarket's own resolution, no dependence on our METAR reconstruction)
- **Entry**: 20:00 UTC (≈16 EDT). Midpoint price at or before entry hour.
  Forward-fill for tail buckets that stopped being quoted.
- **Fee**: `C × 0.05 × p × (1-p)` USDC per share, paid at entry, no exit fee.
- **Stake**: 1 share per trade (depth-agnostic; see depth section below).

---

## Results (per pre-registration, single-shot OOS)

### IS → OOS per-trade PnL

| strategy | tag | IS | OOS | Δ | verdict |
|---|---|---|---|---|---|
| S0 NBS fav | CONTROL | -$0.003 | **+$0.033** | +$0.036 | unexpected OOS win (t=0.95) |
| S0b market fav | CONTROL | -$0.010 | **+$0.063** | +$0.073 | unexpected OOS win (t=1.68) |
| S1 +2°F (NBS anchor) | PRE-REG | -$0.028 | -$0.086 | -$0.058 | **FAILS both, OOS worse** |
| S1m +2°F (MKT anchor) | PRE-REG | -$0.029 | -$0.086 | -$0.058 | **FAILS both, OOS worse** |
| S2 +4°F (NBS anchor) | PRE-REG | -$0.035 | +$0.026 | +$0.060 | OOS win but hit 6.2% |
| S2m +4°F (MKT anchor) | PRE-REG | -$0.029 | +$0.002 | +$0.031 | OOS break-even |
| S3 basket (NBS) | PRE-REG | -$0.031 | -$0.032 | ~$0 | **FAILS both** |
| S3m basket (MKT) | PRE-REG | -$0.029 | -$0.044 | -$0.015 | **FAILS both** |
| S4 +2°F ∧ NBS-spread∈[2,3] | PRE-REG | -$0.017 | -$0.090 | -$0.073 | **FAILS, OOS much worse** |
| S6 mkt-fav -1 | EXPLORATORY | **+$0.058** | +$0.001 | -$0.058 | IS edge decayed to ~0 OOS |

### Per-strategy IS vs OOS interpretation

- **S1 / S1m ("Strategy D V1" equivalent)**: the thesis that "markets
  underestimate afternoon peaks → buy +2°F offset" LOSES on both folds.
  IS per-trade was -$0.028 (already negative before peeking at OOS), and
  OOS is -$0.086 — triple the IS loss. Out of 88 OOS trades only 4 won.
- **S4 (NBS-spread-filtered S1)**: prior work claimed 42.9% hit @ spread 2-3°F
  (n=28). We got 17.1% IS (n=76) → 6.1% OOS (n=49). No signal.
- **S6 (exploratory)**: IS t=+2.23, looked promising. OOS t=+0.02.
  Discovery → deployment gap is essentially 100%. This IS what the
  holdout was for.
- **S0b (market favorite)**: the best OOS entry. But favorites trade at
  ~$0.40-0.60, hit ~69-77%, and the per-trade PnL barely clears zero on
  IS. OOS outperformance (+$0.063/trade) is likely period-specific.

### Per-city patterns (hints at non-stationarity)

The only city where `market-fav -1` held up across both IS and OOS:
- **Denver**: IS +$0.190/trade, OOS +$0.154/trade (n=7+10)
- **Atlanta**: IS +$0.072, OOS +$0.093

Flipped from IS winner to OOS loser:
- **Dallas**: IS +$3.49 total → OOS -$1.04 total (Dallas was the
  strongest IS contributor for S6)
- **Seattle**: IS -$1.02 → OOS -$1.12 (consistent loser, not flip but decay)

Per-city results are **very noisy** at n=10-20 per fold. No city has
evidence sufficient to deploy a city-specific strategy.

---

## Why prior results looked so different

The vault synthesis (`2026-04-11 Strategy D deployment refinements.md`)
claimed:
- Strategy D V1 buy +2°F: **+$1.94/trade** on 198 trades
- Offset scan showed +2°F and +4°F both profitable across cities
- NBS spread filter (2-3°F): **+$5.45/trade** on 28 trades

Our clean IS subset (Mar 11-31, 11 cities, ~140 trades per strategy)
shows LOSSES at those same offsets. Candidate reasons:

1. **Period effect**: prior likely used Dec 2025 - Apr 2026 or longer,
   and Mar-Apr has a NEGATIVE NBS bias (NBS over-forecasts by ~1°F on
   average in our IS window → +2°F offset pays for nothing).
2. **Cherry-picked cities**: prior per-city results had big per-city
   variance (Miami +$233, Houston/Austin/Atlanta 0% hit); averaged-all
   headline was ~$1/trade but dominated by 2-3 cities.
3. **Different entry-price methodology**: prior may have used
   ask-adjusted entry that inflated WIN sizes (taking into account
   $0.10 spread on the winning bucket). We used strict midpoint.
4. **Small-sample noise**: 198 trades with $1.94/trade → std_pnl per
   trade likely $0.30+, so standard error ≈ $0.02 and t-stat ≈ 100.
   That t-stat is implausibly high unless measurement-inflated. More
   likely: the "per trade PnL" was in a different unit (dollar stake?)
   than mine (per share).

Without re-running prior exactly, I can't pin down the exact mismatch.
But the **direction** is clear: prior's headline numbers do not
survive this temporal holdout.

---

## Depth analysis (exploratory, post-hoc)

### Data availability
Book JSONL recorder started **2026-04-13** — after our OOS window closes
(Apr 10). Cannot directly estimate depth for any backtest trade.

### Indicative figures (from Apr 13-14 slugs)
For the kinds of price levels our strategies targeted:

| price tier | typical best_ask | depth within 2¢ | median fill size |
|---|---|---|---|
| favorite (p ~0.40-0.51) | ~$0.40-0.51 | 1000-16000 shares | 5-156 |
| fav+1 (p ~0.15-0.30) | ~$0.20 | ~6000 shares | ~100 |
| fav+2 (p ~0.03-0.10) | ~$0.05 | ~1000 shares | small, sparse |

**Capacity estimate (indicative, Apr 13-14 slugs only)**:
- At fav-level prices: a few hundred to ~$1000 of stake per trade per
  slug is probably absorbable within 2¢ of the quoted ask.
- At tail prices (+2°F / +4°F buckets): much thinner. Median fill sizes
  are 5-15 shares ($0.25-$0.75 per fill). Sustained larger stakes would
  move the market.
- **These figures do NOT apply to the Mar 11-Apr 10 backtest period.**

### Recommendation
**Run the backtest agnostic to size (1 share/trade).** Real capacity
estimation requires 14+ days of book data. Revisit late April / early May.

---

## What survives? (honest)

**Nothing deployable.** Specifically:

- ❌ Strategy D V1 (+2°F offset): fails both IS and OOS.
- ❌ Strategy D V2 (+4°F offset): loses IS, weakly positive OOS but
  t<1.2 and hit rate 4-6%.
- ❌ NBS-spread filter: fails harder OOS.
- ❌ Market-fav-minus-1 (IS-discovered): 100% edge decay OOS.
- ⚠ "Buy market favorite": the only strategy with *any* positive-ish
  OOS signal, but this is essentially a high-confidence directional
  wager on the most-likely bucket and needs ask-aware execution
  analysis before being taken seriously.

## What to do next

1. **Stop deploying Strategy D paper trades** on the assumption that
   the edge is real. It isn't, at least not in the Mar-Apr 2026 11-city
   sample.
2. **Re-examine the prior backtest's price methodology** (ask vs mid,
   entry hour, city filter) to understand what exactly drove the
   previously reported +$1.94/trade. This is a diagnostic exercise,
   not a revival attempt.
3. **Focus on the execution-microstructure edges** (maker rebate,
   ladder-bid arb) per the 2026-04-11 fee-structure synthesis. Those
   are structurally grounded; Strategy D was empirically inferred.
4. **Build the model-based strategy (S5) properly** — train on data
   BEFORE Mar 11, evaluate on IS+OOS with zero leakage. Deferred from
   this round due to training-cutoff overlap.
5. **Keep the book recorder running** for at least 14 more days, then
   re-do the capacity estimate with real depth data.

---

## Files produced

- `PRE_REGISTRATION.md` — pre-reg (locked before OOS peek)
- `harness.py` — master trade-table loader + strategy runner
- `strategies.py` — pre-registered strategies S0-S4 + S6 exploratory
- `run_is.py` — IS-only run
- `run_offset_sweep.py` — exploratory offset sweep (IS)
- `run_oos.py` — single-shot IS+OOS run
- `depth_analysis.py` — depth estimate from Apr 13-14 book data
- `data/processed/backtest_v2/{trade_table,all_trades}.parquet`

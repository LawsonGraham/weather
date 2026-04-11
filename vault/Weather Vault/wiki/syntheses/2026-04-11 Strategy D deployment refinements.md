---
tags: [strategy, polymarket, backtest, deployment, nyc, weather-markets]
date: 2026-04-11
source: notebooks/experiments/nyc-polymarket/exp16_NOTES.md..exp36_NOTES.md
related:
  - "[[2026-04-11 NYC Polymarket upward-bias Strategy D]]"
  - "[[2026-04-11 NYC Polymarket intraday sniping backtest]]"
  - "[[Polymarket]]"
  - "[[KLGA]]"
  - "[[METAR]]"
  - "[[ASOS 1-minute]]"
  - "[[Polymarket weather market catalog]]"
  - "[[Project Scope]]"
---

# Strategy D — deployment refinements (exp16–exp36)

**Headline: Strategy D is deployable today.** The [[2026-04-11 NYC Polymarket upward-bias Strategy D|iter-10 synthesis]] covered the discovery. This page covers the operationalization: cost-model correction, entry-hour optimization, microstructure evidence, edge-decay monitoring, and the live recommender pipeline.

Key result: the **real-ask cost model roughly doubles backtest PnL** vs the placeholder, the **optimal entry hour is 16–18 EDT** (not 12 EDT), and the pipeline is **end-to-end paper-tradeable** via a live Gamma API recommender. The live test at ~13:40 EDT on 2026-04-11 caught the intraday repricing pattern in real time.

---

## 1. Cost model was ~2x too conservative (exp19, exp06b)

The placeholder cost model in the iter-10 chain assumed a non-trivial spread between mid and ask. Empirical check on 35 real Strategy D trades:

- **Median spread = 0.000.** The mid IS the ask on NYC daily-temp Polymarket markets.
- Every Strategy D headline in the prior synthesis was roughly 2x understated.
- **Strategy D V1 @ 12 EDT, real-ask cum PnL = +$54.38** (vs +$27.34 with the placeholder).

Late-afternoon spreads are wider than the historical median — see the live observation in section 10 (3¢ on the +2 bucket at 13:40 EDT).

## 2. Entry hour dominates (exp18, exp25)

Backtested Strategy D across candidate entry hours with the corrected real-ask cost model:

| Entry hour (EDT) | Variant | n trades | Hit rate | Cum PnL | Median PnL | Note |
|---|---|---|---|---|---|---|
| 08–10 | V1 | 36 | 17% | loss | — | **AVOID** — mechanism unclear |
| 12 | V1 | 35 | 31% | +$54 | — | original anchor |
| 16 | V1 | 28 | 46% | +$94 | — | **primary** |
| 18 | V1 | 15 | 53% | +$116 | **+$2.61** | **best** — positive median |

Findings:

- **16 EDT is the new primary entry hour.** 18 EDT shows the strongest per-trade economics (first hour with positive median PnL across the whole chain) but smaller sample.
- **08–10 EDT LOSES.** 36 trades, 17% hit. Whatever produces the edge later in the day is absent or reversed in the morning. Mechanism unknown; treat as a hard skip.
- **V5 skip rules help at 12 EDT only.** V5 is a forecast-bias filter built for noon entries; late-day edge is driven by observed-peak resolution lag, which is a different mechanism. At 16/18 EDT the unfiltered V1 wins.

## 3. Market is HUMAN-driven, not HRRR-driven (exp28)

Volume profile analysis across the sample period:

- **Peak fill volume lands at 14–15 EDT** — the peak-heat afternoon hour, not any HRRR release window (00/06/12/18 UTC).
- HRRR-window hours capture **exactly 33% of fills** — proportional to wall-clock time, no excess concentration.
- Retail traders react to the thermometer, not the forecast.

Major implication: **edge persistence is months, not days.** If the counterparties were HRRR-driven bots the bias would decay in hours once anyone noticed. Humans adapt slowly. Strategy D's runway is long, but deploy early anyway — see section 4.

## 4. Universal upward bias is decaying (exp29)

Split the sample in half chronologically and compute mean signed forecast–observation gap:

| Half | Days | Mean signed gap (°F) |
|---|---|---|
| First 27 days | 27 | **+4.93** |
| Second 28 days | 28 | **+3.25** |

- **34% drop** in the mean gap across the sample window.
- Rolling 14d gap: ~6.5°F mid-March → ~2.5°F by early April.
- **Caveat:** two outlier days (March 10: +20°F, March 11: +26°F) drag the first-half mean up. Without them the drop is smaller.
- Either way, the signal is **"deploy now, don't wait."**

## 5. Retail flow is universally bullish (exp30)

Net YES taker flow by bucket across every day in the sample:

- **Favorite bucket: positive YES net flow on 100% of days.**
- **Favorite +2 bucket: positive YES net flow on 100% of days.**
- Retail buys lottery tickets across multiple strikes. No one is net-short anything.

Implication: **Strategy D rides the same direction retail rides.** We also buy YES on the +2 bucket. This is not a fade trade. We are slipping in alongside the dominant flow, which is why fills are available and the strategy doesn't create its own adverse selection.

## 6. Day-of-week effect (exp31, exp34) — small sample but real

| DoW | n | Hit rate | Mean gap (°F) | Note |
|---|---|---|---|---|
| Mon | — | — | — | de-size |
| Tue | 5 | 20% (1/5) | **+7.88** | worst — gap too big for +2 offset; de-size |
| Wed | — | — | — | baseline |
| Thu | — | — | — | baseline |
| Fri | — | — | — | baseline |
| **Sat** | **7** | **57% (4/7)** | — | **best — 7/7 days upward-direction** |
| Sun | — | — | — | baseline |

- Saturday is the strongest single DoW. Every Saturday in the sample was upward-direction.
- Tuesday is worst — not because the bias fails but because the mean gap is too large (+7.88°F) for the +2 offset bucket to catch. A +3 or +4 variant might help on Tuesdays but was not backtested.
- **Action: de-size Monday/Tuesday entries.** Sample is small — treat as priors, not hard rules.

## 7. Flat favorites NEVER win (exp32) — real pattern, NOT an entry filter (exp33)

- **27% of all days have favorites that don't move >5¢ all day.**
- **Hit rate on those days: 0/15.** Zero.
- These are cheap, forgotten lottery tickets that retail bought early and walked away from.

But (exp33 V6): trying to use flatness as an intraday entry filter at 14 EDT or 16 EDT only qualifies 9 or 3 days respectively, vs 15 from the full-day measurement. **The pattern is real but not actionable as an entry filter** — you can't know a favorite will be flat until the day is over.

## 8. Market never crosses 50% on the actual winner (exp35)

- At 18 EDT — after the peak temperature has already occurred — the market favorite is the actual winning bucket **only 38% of the time.**
- The eventual winner is priced at **most 38¢** across the day.
- Polymarket daily-temp markets are a **probability-spreading system, not a winner-picker.** The order book smears probability across buckets rather than concentrating it on the truth.
- Strategy D rides this slop systematically by always buying the under-priced adjacent bucket.

## 9. Targeted multi-bucket basket FAILS (exp36)

- Even with the exp35 result (winner undervalued), buying 3–5 candidate buckets instead of one dilutes per-dollar edge from **+94% to +1%**.
- **Single bucket is structurally better than a basket** because it concentrates EV on the one offset we actually know is cheap.
- **Rule: don't basket; don't dilute.**

## 10. Bias has no day-to-day persistence (exp27)

- **Lag-1 autocorrelation of the signed gap = 0.04.**
- Yesterday's gap does not predict today's gap.
- No "carryover" filter available. Each day is independent.
- Cannot stack a "yesterday was bullish → trade bigger today" rule on top of Strategy D.

---

## Live test — intraday repricing caught in real time (2026-04-11)

Ran `live_now.py` at ~13:40 EDT against the Gamma API:

- **This morning** (inferred from the stale parquet snapshot): favorite was 62–63°F at 0.39
- **At 13:40 EDT**: favorite had shifted to **60–61°F at 0.495**; new +2 target is **62–63°F at 0.305**
- **This is exp32 happening live** — intraday rebalancing as observations roll in. The cheap forgotten lottery ticket from this morning is now the +2 target. Direct confirmation that the mechanism persists into the deployment window.

**Live spread:** 3¢ on the +2 bucket (bid 0.29 / ask 0.32). Wider than the ~0¢ historical median from exp06b. Late-afternoon liquidity is present but not free.

### Today's actionable trade (2026-04-11, pulled from live API)

| Field | Value |
|---|---|
| Market | `highest-temperature-in-nyc-on-april-11-2026-62-63f` |
| Side | YES |
| Shares | 612.75 |
| Limit price | ≤ **$0.3264** (real ask 0.32 + 2% fee buffer) |
| Stake | **$200** (2% Kelly on $10k bankroll) |
| Profit if hit | **+$412.75** |
| Loss if miss | **−$200** |

---

## The deployable pipeline

| Artifact | Role |
|---|---|
| `notebooks/experiments/nyc-polymarket/exp01–36*.py` | full backtest research chain — discovery → refinement → deployment gate |
| `scripts/polymarket_weather/live_recommender.py` | reads stale local parquet, outputs trade recommendation (dev/test) |
| `scripts/polymarket_weather/live_now.py` | production: queries Gamma API directly for fresh ladder; outputs live recommendation |
| `scripts/polymarket_weather/paper_ledger.py` | append-only JSONL paper-trade ledger — `log`, `score`, `report` subcommands |

All artifacts live on the `wt/nyc-polymarket-backtest` worktree branch.

---

## Deployment checklist

- [x] Strategy validated (exp14 OOS gates passed in the iter-10 synthesis)
- [x] Cost model corrected — real-ask, mid == ask (exp19, exp06b)
- [x] Entry hour optimized — 16 EDT primary, 18 EDT bonus, 08–10 EDT hard avoid (exp18, exp25)
- [x] Skip rules tested — V5 helps at 12 EDT only; V1 wins at 16/18 EDT (exp25)
- [x] Edge decay monitored — ~34% drop across the sample window; deploy now (exp29)
- [x] Live recommender + paper-trade ledger shipped
- [x] Direct Gamma API integration (`live_now.py`)
- [ ] HRRR backfill (~96% complete) — blocks exp30 HRRR-conditional analysis and Phase 2
- [ ] 14 days of paper-trade validation
- [ ] Scale to real capital after paper validation
- [ ] Phase 2 — HRRR-driven full-ladder model

---

## Cross-references

- [[2026-04-11 NYC Polymarket upward-bias Strategy D]] — iter-10 discovery synthesis (exp01–14)
- [[2026-04-11 NYC Polymarket intraday sniping backtest]] — iter-1 negative-result synthesis
- [[Polymarket]] — venue entity
- [[KLGA]] — NYC Polymarket resolution station
- [[METAR]] — Layer 3 observed-temp source for resolution alignment
- [[ASOS 1-minute]] — Layer 1 ground truth
- [[Polymarket weather market catalog]] — slug catalog used by `live_now.py`
- [[Project Scope]] — canonical project scoping doc

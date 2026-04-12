---
tags: [synthesis, polymarket, strategy, backtest, nyc, deployable, positive-result]
date: 2026-04-11
related: "[[Polymarket]], [[KLGA]], [[IEM]], [[Polymarket weather market catalog]], [[ASOS 1-minute]], [[METAR]], [[Project Scope]], [[2026-04-11 NYC Polymarket intraday sniping backtest]], [[2026-04-11 Polymarket schema corrections]]"
---

# NYC Polymarket upward-bias — Strategy D (deployable)

**Companion to the negative-result sniping backtest at [[2026-04-11 NYC Polymarket intraday sniping backtest]]** — same dataset, different angle, real edge.

## Headline

[[Polymarket]] NYC daily-high-temperature markets ([[KLGA]] resolution) are structurally ~4°F too cold at 12 EDT every day. **Strategy D — at 12 EDT, buy the range strike whose `lo_f = fav_lo + 2`** (one bucket above the morning favorite's lower edge) — earns **+81.59 cum PnL per $1 stake** across 44 backtest trades with **29.5% hit rate**. Chronological 60/40 OOS split **improves** on test (+69.79) vs train (+11.81) — reverse of the usual overfit pattern. Deploy at 2% Kelly with a `p_entry ≥ 0.02` filter, paper-trade 30 live days before scaling.

## The universal upward bias

- **44 of 55** scored days have `day_max` landing **above** the 12 EDT favorite's lower edge
- **Mean signed gap: +4.07°F** (day_max − fav_lo)
- 7 downward misses, 4 at-edge
- **80% directional reliability** from a dead-simple cross-check at a single clock time

Mechanism (hypothesis): the morning book is anchoring on an overnight HRRR or NBM run that under-forecasts afternoon rise on clear / dry / still-morning days. Boundary-layer mixing at 13–15 EDT delivers more warming than the forecast captured. **Exp18 (blocked on HRRR backfill at ~42%) will test whether HRRR shares this bias** or whether HRRR is closer to truth — the latter would be direct alpha vs market.

## Conditioning features (METAR at LGA, 12 EDT)

From `exp12_NOTES.md`:

| Feature | Split | Mean signed gap |
|---|---|---|
| `rise_needed` (fav_lo − temp_12Z) | `corr(gap, rise_needed) = −0.759` | **single strongest predictor** |
| Sky coverage | clear / scattered | +5 to +6°F |
| Sky coverage | broken / overcast | +1 to +2°F |
| Humidity | dry / mid | +5°F |
| Humidity | humid | +2°F |
| Wind direction | southerly (onshore warm Atlantic) | +6.2°F |
| Wind direction | NW | +1.4°F |
| Morning temp tercile | warm-morning | +7.2°F |
| Morning temp tercile | mild | +1.2°F |

All four condition axes point the same way: **clear, dry, southerly, already-warm mornings underprice the afternoon high by the most**. They all line up with a boundary-layer-mixing under-forecast.

## Strategy D — the deployable rule

**At 12 EDT every day on the NYC daily-temp market, buy the range strike whose `lo_f = fav_lo + 2` (one bucket above the morning favorite's lower edge).**

Entry cost modeled as `(p + 0.03 spread) * 1.02 fee`.

| Metric | Value |
|---|---|
| Bets | 44 / 55 scored days |
| Hit rate | 29.5% |
| Avg entry | $0.163 |
| Cum PnL (per $1 stake) | **+$81.59** |
| Winners | 13 across 13 distinct dates (not outlier-concentrated) |

Offset scan in `exp13_NOTES.md` confirmed `fav_lo + 2` dominates neighbor offsets — it's the sweet spot between "too close to favorite (low payoff, not enough edge)" and "too far out (hit rate collapses)".

## OOS gate — chronological 60/40

| Split | Bets | Hit rate | Cum PnL |
|---|---|---|---|
| Train (first 60%) | 26 | 19.2% | +11.81 |
| **Test (next 40%)** | **18** | **44.4%** | **+69.79** |

**Test is better than train.** Reverse of the overfit pattern — not a fluke of the train window, the bias is structural and stable or strengthening.

## Conservative filter — `p_entry ≥ 0.02`

Removes the handful of sub-2¢ outlier fills that would be hard to replicate live:

| Split | Bets | Hit rate | Cum PnL |
|---|---|---|---|
| Train | 21 | 23.8% | +16.81 |
| Test | 14 | 42.9% | +10.53 |

**Both halves positive** after the filter. This is the production entry rule.

## Monthly split

| Month | Bets | Cum PnL |
|---|---|---|
| Dec 2025 | 2 | −2.0 |
| Feb 2026 | 9 | +10.3 |
| Mar 2026 | 24 | +39.2 |
| Apr 2026 | 9 | +34.1 |

**Not seasonal** — works across three different months with wildly different weather regimes. Dec has only 2 obs, not enough to read.

## Drawdown / Kelly sim

- Max consecutive losing streak: **6**
- At 2% Kelly → **11.4% bankroll DD**
- At 4% Kelly → **21.7% bankroll DD**

## Deployment plan

1. **Start at 2% Kelly**, conservative entry filter (`p_entry ≥ 0.02`)
2. **30-day paper-trade gate** before real capital
3. Scale to 3–4% Kelly after 30 days **iff** hit rate ≥ 20% **and** cum PnL ≥ 0
4. Re-evaluate after Exp18 delivers HRRR backfill — if HRRR is closer to truth than market, add a direct HRRR-vs-market overlay

## Applied to today (2026-04-11)

- Favorite: **62–63°F at $0.39**
- Strategy D buy: **64–65°F at $0.14**
- Entry cost: **$0.173** (0.14 + 0.03 spread, ×1.02 fee)
- Payoff: **5.78×**
- EV at 31% hit rate: **+$0.79 per $1 invested**

## Anti-findings — do not re-investigate

From the same exploration loop. These all dead-ended; log them so future sessions don't burn cycles re-running them:

- **ASOS 1-min threshold sniping** — market reprices within ~90s of a sustained cross; no human reaction window. See [[2026-04-11 NYC Polymarket intraday sniping backtest]] for the full negative-result writeup.
- **Paired long-underdog hedges** — all underperform solo short because miss magnitudes (5–10°F) blow past adjacent bucket hedges.
- **Follow-the-running-max** — outlier lottery; 1-min data too gappy at LGA to run cleanly.
- **Solo fade of morning favorite** — mean-positive but **median −$1**. Strategy D is a strictly better use of the same bias (keeps the directional insight, replaces the fade with a bounded-risk buy of an adjacent cold-side strike).

## Discovered facts worth recording

Independent of Strategy D, but captured so they don't get lost:

- **Polymarket NYC daily-temp ladder is prob-normalized.** Mean sum of all strike prices at 12 EDT = **1.000 ± 0.065**. No systematic overround / vig to fight.
- **[[ASOS 1-minute]] at [[KLGA]] has ~15°F gaps.** For live temp state on this project, prefer [[METAR]] hourly + 6-hour RMK max over ASOS 1-min.
- **Polymarket `end_date` is NOT market close.** It's 07:00 EDT on the target date, but real fills continue through the afternoon. Verified on the April 3 market — fills through 17:52 UTC on April 3. This is load-bearing for any backtest cut-off.
- **DuckDB naive-timestamp gotcha.** `AT TIME ZONE 'America/New_York'` on a naive timestamp converts OUT of NY-local, not into it. Must wrap with `AT TIME ZONE 'UTC'` first. Several early experiments had the arrow flipped.

## Source artifacts

- `notebooks/experiments/nyc-polymarket/exp01_*.py` … `exp14_*.py` + `*_NOTES.md` on the `wt/nyc-polymarket-backtest` branch
- **Key notes files:**
  - `exp12_NOTES.md` — universal bias discovery + METAR feature conditioning
  - `exp13_NOTES.md` — Strategy D construction + offset scan
  - `exp14_NOTES.md` — OOS gates + monthly split + Kelly sim

## Related

- [[Polymarket]] — resolution venue
- [[KLGA]] — NYC resolution station for Polymarket daily-temp markets
- [[IEM]] — upstream for [[METAR]] feature conditioning
- [[Polymarket weather market catalog]] — slug catalog the backtest keys off
- [[ASOS 1-minute]] — rejected live-state source for this project (too gappy at LGA)
- [[METAR]] — accepted live-state source; 12 EDT METAR at LGA is the feature clock
- [[Project Scope]] — trading-not-forecasting thesis; this is the first deployable strategy
- [[2026-04-11 NYC Polymarket intraday sniping backtest]] — negative-result companion (sniping dead-ended; this page is what worked on the same dataset)
- [[2026-04-11 Polymarket schema corrections]] — schema gotchas that shaped the backtest's join / cutoff logic

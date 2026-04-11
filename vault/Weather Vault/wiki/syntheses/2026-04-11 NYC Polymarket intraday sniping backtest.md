---
tags: [synthesis, polymarket, backtest, negative-result, sniping, klga, asos]
date: 2026-04-11
related: "[[Polymarket]], [[KLGA]], [[KNYC]], [[Polymarket weather market catalog]], [[ASOS 1-minute]], [[Project Scope]], [[2026-04-11 Polymarket schema corrections]]"
---

# NYC Polymarket intraday sniping backtest — negative result

Backtest of the thesis that [[Polymarket]] NYC daily-temperature "or higher" / "or below" rungs can be sniped off [[ASOS 1-minute]] [[KLGA]] readings the moment a running-max threshold is crossed. **Result: not viable for a human trader.** Market reprices in ~90 seconds after a *sustained* cross and correctly ignores 1-minute sensor spikes. The real edge lives pre-cross, in forecast quality, not post-cross in reaction speed.

This page is the anchor for that negative result. Future sessions: do not re-investigate ASOS-threshold sniping without reading this first.

Source: `notebooks/expl_nyc_polymarket_backtest.py` (DuckDB-native backtest, currently in the `wt/nyc-polymarket-backtest` worktree; will merge to master).

## Hypothesis

NYC daily-temperature strike markets on [[Polymarket]] stay live through the target local day. Once [[KLGA]] running-max crosses a strike threshold, the `X°F or higher` / `X°F or below` rungs become **deterministically locked** — yes-price should snap to ~$1.00 / ~$0.00. If the market is slow to reprice, the window between lock-time and price-snap is an arb snipe.

## Data

- **Universe:** 532 closed NYC Daily Temperature markets; **109 "or higher"/"or below" end rungs** — the cleanest deterministic cases. (423 interior range rungs like `54-55°F` have knock-in + knock-out structure and were not tested in this pass.)
- **Ground truth:** [[IEM]] [[ASOS 1-minute]] LGA `tmpf`. Afternoon coverage is usually OK; full archive has ~150 days with <1000 valid minutes (see [[ASOS 1-minute]] for coverage notes).
- **Market data:** processed `data/processed/polymarket_weather/prices/**/*.parquet` — dense per-second forward-filled `yes_price` for every slug.
- **Window:** 2026-01-01 → 2026-04-09.

## Discovered schema fact — `end_date` is not close-of-trading

NYC daily-temperature `end_date` is **noon UTC (~07:00 EDT) on the target local day**, NOT when trading stops. Real fills continue well into the afternoon of the target day. **Example:** the April 3 market had fills until **17:52 UTC on April 3**. The market is live *during* the actual weather event.

This contradicts the natural reading of `end_date` and is an important correction to the schema understanding in [[2026-04-11 Polymarket schema corrections]]. **Trading window includes the entire resolution day until the market resolves**, not up to `end_date`.

## Discovered bug — timezone conversion on naive timestamps

Raw [[IEM]] [[ASOS 1-minute]] CSV has a `valid(UTC)` column read as **naive TIMESTAMP** by DuckDB. Using `AT TIME ZONE 'America/New_York'` on a *naive* timestamp converts **OUT of** the named zone, not **INTO** it. Day boundaries were shifted by 5h and some lock events got assigned to the wrong local day.

**Fix:** double-cast to attach UTC first, then convert to local.

```sql
"valid(UTC)" AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
```

## Main result

- **57 lock events** over 43 days.
- **5 material snipe candidates** (≥10¢ edge at lock). All on "or higher" strikes — afternoon warming crossing a threshold.
- **Mean edge at lock: 6¢.**
- **Median fraction-of-gap-closed at 5m / 15m / 60m: 0%** — reaction stats are noise-dominated because **roughly half of "locks" are 1-minute sensor spikes that the market correctly ignores.**

The tail of "material" snipes is partly an artifact of naive lock detection that treats a single-minute spike as a cross event.

## The cleanest case — `2026-03-04 48°F or higher`

Walk-through of the clearest lock-and-reprice sequence in the backtest:

| UTC time | Event | Price |
|---|---|---|
| 17:00 | 1h pre-cross baseline | **29¢** |
| 18:31 | First 48°F reading — 1-min spike, reverts next minute | — market ignores (correct) |
| 18:46 | Second 1-min 48°F spike, also reverts | — ignored (correct) |
| 18:49–53 | **Sustained 48°F for 5 minutes** | ~52¢ → 61¢ |
| 18:54:07–27 | **Price rips from 64¢ to 99¢ in ~20 seconds** | 64¢ → 99¢ |
| 18:57 | Fully settled | **99.9¢** |

**Reaction latency from sustained-cross confirmation: ~90 seconds.**

Two lessons from this case:

1. The market has **a noise-rejection filter**. Single-minute sensor spikes don't move the price.
2. Once the market concludes the cross is real, it reprices **faster than any human can react**.

## Conclusion — naive ASOS sniping is not viable

- Market correctly fades single-minute spikes → any naive "cross detected, fire order" bot eats false positives on exactly the same signals the market ignores.
- Once the cross is confirmed sustained, market reprices in ~90 seconds or faster.
- Only a **sub-second bot with direct [[IEM]] feed latency** could conceivably arb this window — and the latency budget (IEM ingest lag → our pipeline → order submission → on-chain settlement) is almost certainly larger than that.
- The 5 "material" snipes in this backtest are partly artifacts of naive lock detection treating sensor spikes as crosses.

## Where the real edge lives — pre-crossing forecast quality

On 2026-03-04 the market was at **29¢ an hour before the cross** and **50¢ right before**. A calibrated HRRR-based model saying `P(max ≥ 48) = 0.8` at 17:00 UTC could have bought at 29¢ and held to 99¢.

This is the **"react to HRRR update"** thesis from [[Project Scope]], not "react to ASOS readings". The edge compounds with lead time, not reaction time.

## What this backtest doesn't yet cover

- **The 423 interior range rungs** (`54-55°F` style). They have knock-in AND knock-out structure, carry most of the price (favorite on April 12 is `54-55°F` at 39¢ vs ~1¢ for tails), and are **untested**. Extension planned in the same notebook.
- **HRRR-based pre-cross edge backtest** — blocked on in-progress HRRR backfill; will swap in once that download completes.
- **Fee and spread modeling** — snipe PnL numbers in this backtest are **gross**: no slippage, no LP-token fees, no price-impact.

## Decision

- **Deprioritize** ASOS-based sniping.
- **Prioritize** HRRR-based pre-cross modeling of the full strike ladder once HRRR data lands.
- This synthesis stands as the **negative-result anchor** that justifies the pivot. Don't re-investigate ASOS sniping without refuting the ~90-second sustained-cross reprice latency documented above.

---
date: 2026-04-22
type: synthesis
tags: [consensus-fade, backtest, strategy-iteration]
---

# Consensus-Fade v2 — local-time anchoring replaces 20 UTC fixed

## Summary

Consensus-Fade +1 Offset rewritten from "enter at 20 UTC fixed" to
"enter at ≥16:00 city-local time". Same trade (buy NO on +1 bucket when
NBS+GFS+HRRR all agree within 3°F), same thesis, better coordinate
system. Also tightened the HRRR input requirement to "fxx=6 must cover
≥6 distinct valid-hours in the local 12-22 peak window" so early-day
entries can't fire on partial peak coverage.

Canonical v2 backtest (Mar 11 – Apr 10 2026, 11 US cities, same
hourly-price source throughout): n=78, 77/1, 98.7% hit, +$0.046 per
trade, IS t=+1.85, OOS t=+4.49, Sharpe 9.3 annualized.

## Why v1's 20 UTC entry was hiding a bug

v1 used "20 UTC fixed", which silently translated to totally different
local times per city:

| zone | 20 UTC = local |
|---|---|
| Eastern (ATL/NYC/MIA) | 16:00 EDT |
| Central (ORD/DAL/HOU/AUS) | 15:00 CDT |
| Mountain (DEN) | 14:00 MDT |
| Pacific (SEA/LAX/SFO) | 13:00 PDT |

West Coast entries were pre-divergence (before midday METAR had let
the market separate winners from losers); East Coast entries were
post-divergence. The strategy's edge was thus city-dependent in a way
that was never explicit in STRATEGY.md. 16:00-local anchoring
standardizes and improves OOS t from +1.39 → +4.49 on the same
apples-to-apples price source.

## v1 headline was also inflated by price-source

v1 STRATEGY.md reported n=94 / 98.9% / +$0.083 / t=+4.44. That used
the `trade_table.entry_price` snapshot column. Replaying v1's exact
rule (20 UTC fixed, cs ≤ 3°F, cap 0.50) against the Polymarket hourly
prices feed — the same source v2 uses — gives n=84 / 97.6% / +$0.058 /
t=+3.14. The v1 headline was a ~30% overstatement of the edge.

Lesson: always state the price source in backtest headlines, and never
compare stats across different snapshot columns.

## The 16:00 local floor is physical, not statistical

Two reasons the floor matters, both mechanical:

1. **HRRR peak-window coverage.** HRRR is fxx=6-only, so at 13 local
   (say 18 UTC Eastern) the init-times ≤ entry time produce forecasts
   valid through only 20-21 local. Full peak coverage (through 22
   local) needs init ≥ 16 UTC local, i.e., ~16 local. Before that,
   "HRRR max over peak" is based on partial coverage and biases low,
   letting consensus trigger on forecasts that haven't yet projected
   the afternoon.
2. **Market self-correction.** Live METAR between noon and 15 local
   updates the prediction market's view of whether today will
   overshoot. Winners see YES drift toward 0, losers see YES rise
   sharply. By 16 local the separation is near-complete:

   | local hour | winner YES | loser YES |
   |---|---|---|
   | 12 | $0.13 | $0.27 |
   | 14 | $0.13 | $0.42 |
   | 16 | $0.08 | $0.75 |
   | 17 | $0.04 | $0.81 |

   The market, in aggregate, already knows which days are winners and
   which are losers by 16 local. Earlier entries are trading before
   that signal exists.

## Floor sweep confirms the mechanics

| floor local | n | hit | per | t | IS t | OOS t |
|---|---|---|---|---|---|---|
| ≥13 | 93 | 91.4% | +$0.029 | +1.06 | +0.28 | +1.27 |
| ≥15 | 84 | 96.4% | +$0.052 | +2.51 | +5.71 | +0.66 |
| **≥16** | **78** | **98.7%** | **+$0.046** | **+3.67** | **+1.85** | **+4.49** |
| ≥17 | 76 | 98.7% | +$0.041 | +3.30 | +1.73 | +3.96 |

≥16 is the earliest defensible floor in OOS.

## The 0.22-cap finding — NOT canonical

Overlaying `yes_ask ≤ 0.22` (instead of 0.50) at 16 local entry gives
n=72, **100.0% hit**, t=+7.70, IS t=+5.29, OOS t=+5.85. This
eliminates the one canonical loss (Chicago 2026-03-17).

It is deliberately NOT promoted to canonical because:

1. 72/0 vs 77/1 is statistically indistinguishable (95% Wilson CI for
   the true 72/0 hit rate is 95-100%, overlapping 98.7%).
2. The 0.22 cap was chosen post-hoc after inspecting the winner/loser
   price split. Filter-selection from the outcome variable needs
   independent OOS to validate.
3. The cap pushes fills deep into the NO side ($0.78-$0.99 per share),
   where book depth is less well-characterized.
4. Per-trade edge shrinks $0.046 → $0.039.

Documented in STRATEGY.md §5.1 as an optional tightening to overlay
on the first 20 live paper trades during the trust-building phase. If
realized hit rate ≥99% over 30+ independent trades, promote to
canonical.

## Lessons

1. **Coordinate systems matter.** "20 UTC" vs "16 local" looks like the
   same thing for East Coast and nothing changes — until you realize
   you have 11 airports in 4 time zones and the strategy is a
   different strategy in each.
2. **Always state the price source.** A reproducer that doesn't match
   the original headline isn't necessarily wrong — it may be a cleaner
   price source. Investigate before assuming either number.
3. **Post-hoc filters need independent validation.** A 100%-hit-rate
   overlay is tempting to ship; the right move is to paper-trade it
   against fresh data first and promote only after.
4. **IS/OOS within the same 2-month regime is not a real hold-out.**
   Both v1 and v2 passed IS/OOS t-stats. Only live out-of-sample
   (different weather regime, different season) counts as real
   validation.

## Links

- [Strategy doc](../../../../src/consensus_fade_plus1/STRATEGY.md)
- [Canonical backtest reproducer](../../../../src/consensus_fade_plus1/backtest.py)
- [v1 vs v2 head-to-head](../../../../notebooks/experiments/backtest-v3/v1_v2_compare.py)
- [Optimal sweep (grid over floor × cs × cap × stable)](../../../../notebooks/experiments/backtest-v3/consensus_optimal_sweep.py)
- [Time-resolved variants exploration](../../../../notebooks/experiments/backtest-v3/consensus_form_variants.py)

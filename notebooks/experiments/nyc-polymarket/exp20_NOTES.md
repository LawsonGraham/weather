# Exp 20 — Combined portfolio with real bid/ask costs (across 3 entry hours)

**Script**: `exp20_portfolio_real_costs.py`
**Date**: 2026-04-11
**Status**: 12 EDT headline confirmed at 4.4x (matches exp19/06b prediction).
16 EDT and 18 EDT produce implausibly large backtest multiples driven by
compounding small-n lottery wins. 12 EDT is the realistic deployment target.

## Spread confirmation at all hours

| hour  | n  | mean d_ask−mid | median d_ask−mid | mean fav_bid−mid | median |
|-------|----|----------------|-------------------|-------------------|--------|
| 12 EDT | 35 | +0.0002        | **0.000**         | -0.008            | 0.000  |
| 16 EDT | 28 | +0.006         | **0.000**         | -0.011            | 0.000  |
| 18 EDT | 15 | -0.002         | **0.000**         | +0.007            | 0.000  |

**Zero median spread at every entry hour.** The mid-price is the ask on
more than half of trades at every hour of the day. Real cost ≈ mid cost.

## Solo Strategy D across hours (2% Kelly, real ask)

| entry    | n  | final    | multiple  | max DD |
|----------|----|----------|-----------|--------|
| 12 EDT   | 35 | $24,245  | 2.42x     | 26.1%  |
| 16 EDT   | 28 | $45,184  | 4.52x     | 9.6%   |
| 18 EDT   | 15 | $62,834  | **6.28x** | **5.9%** |

At 18 EDT, solo D with 2% Kelly earns 6.28x with only 5.9% max
drawdown — spectacular risk-adjusted return... on n=15 bets. Small-n
caveat applies.

## Combined portfolio D+F+P (2% Kelly per leg, real costs)

| entry    | n days | final       | multiple     | max DD |
|----------|--------|-------------|--------------|--------|
| 12 EDT   | 35     | $43,900     | **4.39x**    | 15.8%  |
| 16 EDT   | 28     | $151,412    | **15.14x**   | 11.5%  |
| 18 EDT   | 15     | $1,194,557  | **119.46x**  | 4.0%   |

The 12 EDT 4.39x matches the exp19+06b prediction almost exactly.
The 16/18 EDT numbers are driven by compounding a handful of
big-winner trades — not deployable as headline returns.

## Why the 15x/120x numbers are not real

At 18 EDT, the 15 bets include several winners at extremely low entry
prices (e.g., 0.02 → 50x payoff). Betting 2% of a growing bankroll on
each one compounds cartoonishly:

```
bet 1 wins 50x at 2% stake → bankroll *= 1 + (0.02 * 49) = 1.98x
bet 2 wins 50x              → bankroll *= 1.98x = 3.92x
bet 3 wins 20x              → bankroll *= 1.38x = 5.41x
... and so on
```

Sequence-dependent compounding with fat-tailed outcomes is exactly the
pattern that produces lottery-sized backtest multiples. The real-trading
version must either:
1. **Cap per-bet notional** at a fixed dollar amount instead of a %age
   of bankroll (prevents compounding into unrealistic sizes)
2. **Pull profits after 2x** (realize winnings, reset bankroll)
3. **Treat late-day results as aspirational** — target the 12 EDT
   result and treat the late-day uplift as lottery bonus

## Realistic deployment target

**Strategy D + F + P at 12 EDT, 2% Kelly per leg, real-ask costs**:
- Expected 55-day return: **~4.4x**
- Max drawdown: **~15.8%**
- Execution: 35 trade-days out of 55

This is what I would actually deploy. The 12 EDT result is the
realistic read; everything later-in-day is icing.

## Updated deployment recommendation

### Primary strategy (safe + realistic):
- **Entry**: 12 EDT each day (unchanged from exp14 gate)
- **Legs**: D (broad, always), F (clear-sky filter), P (peaked-ladder
  filter) — combined
- **Cost model**: use real last-YES-buy as ask, last-YES-sell as bid —
  NOT placeholder (mid + 3¢)
- **Sizing**: 2% Kelly per leg, capped at 3% of bankroll per day
- **Expected 55-day return**: ~4.4x (on backtest)
- **Max DD**: ~16%

### Aspirational upgrade (small n, high volatility):
- **Entry**: 16 EDT or 18 EDT for the same legs when books are active
- **Needs**: exp19-style verification of book activity per trade day
  before firing
- **Caveats**: compounding lottery, cap per-bet notional to avoid
  explosion

### Today's deployable trade (April 11, 2026, $10k bankroll)

At 12 EDT for NYC daily-temp market:

```
Current ladder:  fav 62-63°F at 0.39 / target 64-65°F at 0.14
Real ask:        0.14 (mid == ask per exp06b)
Entry cost:      0.14 * 1.02 = $0.1428
Payoff if hits:  $1 / $0.1428 − 1 = +6.00x
At 2% Kelly:     $200 stake → +$1,200 profit if 64-65 hits (31% chance)

If sky is clear AND rise_needed < 3°F (check 12 EDT METAR):
    F leg fires: short 62-63°F favorite
    NO ask ≈ 1 - 0.39 = 0.61 (real-bid reconstruction)
    Entry cost: 0.61 * 1.02 = $0.6222
    Payoff if fav misses: $1 / $0.6222 − 1 = +0.61x
    At 2% Kelly: $200 stake → +$122 if 62-63 misses (82% chance)

Peaked leg (P): p_fav = 0.39 < 0.60 → SKIP.

Total capital at risk: $200-400 (2-4% of bankroll)
```

## Artifacts

- `exp20_portfolio_real_costs.py` — rebuilt bid/ask reconstruction at 3
  hours, portfolio sim with real legs
- `exp20_NOTES.md` — this file

## Queued follow-ups

- **Exp 21**: notional-cap version of the 16/18 EDT sim. Cap per-bet at
  $500 regardless of bankroll. See if the spectacular multiples survive
  non-compounding sizing.
- **Exp 22**: time-of-day spread stability — what does the spread look
  like at 10 EDT / 20 EDT / 22 EDT? If spreads widen late, the real-ask
  assumption collapses.
- **Exp 23**: the 6 "fav below current" days from exp12 (still
  unexplored) — those had mean gap +16°F and 0% hit rate. If we can
  identify them prospectively, it's a separate huge-edge play.

## Decision

**The 12 EDT real-cost 4.39x is the honest tradeable target.** Every
post-12 multiple is small-n lottery. Deploy 12 EDT for 14 days paper-
trading, then scale. Late-day expansion is a V2 after live data
confirms the base case.

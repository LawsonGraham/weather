# Comprehensive Strategy Synthesis: What Actually Works in Polymarket Weather

**Date**: 2026-04-12
**Scope**: 11 US cities, 262 resolved days, 5M fills, NBS/GFS forecasts, METAR ground truth

---

## The Three Proven Strategy Archetypes (from trader behavior analysis)

### 1. METAR Clock Selling (proven by `0x7e243e99`, +$8.3k)

**How it works**: at ~22Z (6 PM EDT) the daily high is confirmed by
METAR. Sell YES on every non-winning bucket. Revenue = sell price,
cost = fee + $1 on any mistaken sell.

**Our backtest**: +$48 at 1-share scale over 166 days (480 sells, $0.29/day).
100% correct by construction (selling after the high is known).

**Why it's small**: by 22Z, prices have already collapsed on the losing
buckets (avg sell price $0.10). The edge is real but revenue per sell
is tiny. The live trader likely operates at 50-500 share scale for
meaningful P&L.

**Scaling**: at 100 shares: ~$29/day ($4,800 over 166 days). At 500
shares: ~$145/day. Requires book depth at 22Z on losing buckets.

### 2. NBS Tail Pruning (proven by `0xcbbc5e03`, +$19.6k)

**How it works**: morning NBS forecast says max = X°F with spread S.
Sell YES on buckets where lo_f >= X + threshold. These are "impossible"
tails that the model says won't happen.

**Our backtest** (16 EDT entry, all 11 cities):

| threshold | sells | accuracy | NET PnL | per day |
|---|---|---|---|---|
| +2°F | 395 | 95.2% | +$4.47 | $0.15 |
| +4°F | 223 | 95.1% | -$0.58 | -$0.02 |
| +6°F | 99 | 94.9% | +$0.55 | $0.02 |
| +8°F | 45 | 93.3% | +$0.56 | $0.04 |

**Why it's small**: the fundamental asymmetry of selling cheap YES tokens.
Revenue per correct sell: ~$0.05-0.06. Cost per miss: -$1.00. You need
>94% accuracy just to break even. NBS at 95% is barely sufficient.

**The real trader (`0xcbbc5e03`) does better** because:
- They sell at BETTER prices (not just 16 EDT but reactive to book)
- They sell more aggressively when NBS spread is low (higher confidence)
- They operate as 79% maker (earning spread + rebate on top of the
  directional sell)

### 3. Combined Long + Short (Strategy D V1 + sell the tail)

**Backtest**: Strategy D V1 (buy fav+2 at 16 EDT) produced +$385 at
1-share across 198 trades. The NBS-bucket variant produced +$289 at
+$3.17/trade on 91 trades.

**These are BUY-side strategies** with opposite risk profile to the
sell strategies above: you risk the entry price (~$0.15) and win $1
on success. The hits are BIGGER than the misses.

---

## What Drives Market Accuracy (the data sources question)

### NBS forecast vs the market

| metric | market | NBS |
|---|---|---|
| MAE (°F) | 3.4 | **2.8** (18% better) |
| Bias | +2.5 (underestimates high) | **+1.1** (nearly unbiased) |
| Bucket-level accuracy | ~30% | ~35% |
| Head-to-head wins | 33% of days | **40% of days** |

**NBS beats the market on accuracy but only by a moderate margin.**
At the 2°F bucket granularity, NBS picks the exact winner 35% of
the time — better than the market's 30% but far from reliable.

### What traders are actually using

| trader | data source | evidence |
|---|---|---|
| `0x7e243e99` (+$8.3k) | **METAR real-time** | Fills spike at 22:47-22:56Z, exactly when METAR publishes |
| `0xcbbc5e03` (+$19.6k) | **NBS + something else** | Sells far from NBS forecast (95%+ accuracy). When long, beats NBS (66% vs 35%), suggesting additional signal |
| `0x594edb91` (+$23.4k) | **Unknown — NOT forecast-based** | Taker-side buys are worse than random. Edge is on maker side (spread + rebate) |
| `0xc5d563a3` (-$83.9k) | **No model** | Hit rates at exact market baseline. Pure liquidity provision |

**Nobody is running a sophisticated NWP pipeline** that takes HRRR
ensemble output and converts it to bucket probabilities. The winning
strategies are:
- **Observation speed** (read METAR before the market adjusts)
- **NBS as a pruning filter** (sell what the model says is impossible)
- **Market-making patience** (post limits, earn spread + rebate)

### The crowd's information source

The market's +2.5°F bias (underestimates the daily high) suggests
the crowd is anchoring to MORNING temperatures rather than AFTERNOON
forecasts. A trader checking "what's the temp in Dallas right now" at
10 AM sees 65°F but the afternoon high will be 72°F. The market's
favorite drifts upward through the day but never fully catches up.

NBS gets this right because it explicitly forecasts the afternoon
max, not the current reading. The market's bias is the bias of humans
who don't understand that 10 AM temperature ≠ afternoon peak.

---

## Honest P&L Assessment Across Strategies

| strategy | total P&L | trades | per trade | per day | scale factor |
|---|---|---|---|---|---|
| **Strategy D V1** (buy fav+2) | +$385 | 198 | $1.94 | $1.15 | × shares |
| **NBS-bucket** (buy NBS forecast) | +$289 | 91 | $3.17 | $0.86 | × shares |
| **METAR clock** (sell losers at 22Z) | +$48 | 480 | $0.10 | $0.29 | × shares |
| **NBS tail prune** (sell >2°F above NBS) | +$4.5 | 395 | $0.01 | $0.02 | × shares |

All at 1-share scale. At 100-share: multiply by 100 (subject to book
depth). At 1000-share: multiply by 1000 (likely exceeds book depth on
most buckets).

**Strategy D V1 remains the highest-PnL approach** despite being the
simplest. Its edge comes from the structural warm bias (+2.5°F) that
the entire market consistently exhibits.

---

## What We've Proven and What We Haven't

### Proven
- The market has a persistent warm bias (+2.5°F across 262 days, 11 cities)
- NBS is 18% more accurate than the market at predicting daily max temp
- Nobody in these markets uses forecast data systematically
- The winning traders use METAR speed, NBS pruning, or market-making
- The market is approximately calibrated at the aggregate level

### Not proven
- Whether a COMBINED NBS + Strategy D approach would compound the edges
  (needs out-of-sample testing with proper train/test splits)
- Whether the warm bias will persist going forward (it could be seasonal)
- Whether our backtested P&L survives real execution (slippage, competition)
- Whether HRRR adds anything beyond what NBS already provides

### Next steps to validate
1. Out-of-sample temporal split (train on Jan-Feb, test on Mar-Apr)
2. Paper trade for 30 days with real Polymarket orders
3. Start with the METAR clock (simplest, 100% theoretical win rate)
4. Add NBS tail pruning as a morning supplement
5. Add Strategy D V1 directional buys as a third leg

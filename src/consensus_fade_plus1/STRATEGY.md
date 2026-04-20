# Consensus-Fade +1 Offset

Fade retail's systematic over-pricing of the bucket one above the NBS
favorite on daily-temperature markets, restricted to days where three
independent weather forecasts agree.

**Status**: paper-trade (need 2+ weeks of live fill data before real capital)
**Backtest**: n=94 trades / 31 days (Mar 11 – Apr 10 2026) / 98.9% hit /
+$0.083 per trade / IS t=+4.44 / OOS t=+4.98 / 27 of 27 positive days
**Venue**: Polymarket
**Entry**: 20:00 UTC (≈ 16 EDT), same-day markets
**Exit**: hold to resolution

---

## 1. Thesis in one paragraph

On days when NBS, GFS MOS, and HRRR all forecast a similar daily high
(consensus spread ≤ 3°F), weather is highly predictable — NBS MAE in this
regime is ~1.5°F. For the actual daily high to land in the bucket **2°F
above NBS's forecast**, you need a ~1.5-sigma upward surprise, which
happens only ~3% of the time. But retail on Polymarket prices that bucket
at ~$0.15-0.20 (implied 15-20% probability) because they spread
probability symmetrically above and below the forecast without
conditioning on how confident the forecasts are. We buy NO at ~$0.85 and
collect $1 with 97%+ probability.

## 2. Why this mispricing exists

Retail traders on Polymarket weather markets:

1. **Symmetric-uncertainty heuristic.** "Could be warmer, could be cooler"
   — bet both sides roughly equally. They don't incorporate which
   forecast-confidence regime they're in.
2. **"Coverage" betting.** A bettor who thinks 70-71°F is most likely
   will still put a few dollars on 72-73°F "just in case." This inflates
   the +1 bucket price even when the real probability is near zero.
3. **The +1 sweet spot.** Buckets 2+ away are priced near the $0.01 tick
   floor — mispricing is negligible pennies. The +1 bucket sits where
   "unlikely but possible" lives cognitively, which is exactly where
   humans over-price low-probability events (the classic 3% → 17%
   calibration error).
4. **Asymmetric, not symmetric.** We confirmed the -1 bucket (below NBS
   fav) does NOT have the same mispricing — it's ~50/50 and priced near
   fair. Only the upside is systematically over-priced.

Structural explanation: retail is chasing warmth upside because days with
consensus-tight forecasts are typically spring-warming days where "maybe
it'll hit an unexpected high" is psychologically attractive.

## 3. Signal

For each Polymarket daily-temperature market (a city on a given day):

### Inputs required

| input | source | when available | used for |
|---|---|---|---|
| NBS max forecast | IEM MOS archive / NBS text | issued ~19/01/07/13 UTC | favorite bucket |
| GFS MOS max forecast | IEM MOS archive | issued ~00/06/12/18 UTC | consensus |
| HRRR t2m max | NOAA HRRR archive | every hour | consensus |
| Market bucket catalog | Polymarket Gamma `markets.parquet` | refreshed daily | bucket mapping |
| YES bid/ask at +1 bucket | Polymarket CLOB book WS | live | entry price |

### Derived signals

```
consensus_spread = max(NBS_max, GFS_max, HRRR_max) - min(NBS_max, GFS_max, HRRR_max)
NBS_fav_idx      = argmin_i |bucket_center[i] - NBS_max|
plus1_idx        = NBS_fav_idx + 1
yes_mid[plus1]   = (best_yes_bid + best_yes_ask) / 2 at plus1 bucket
no_ask[plus1]    = 1 - best_yes_bid at plus1 bucket
```

### Entry filter (all must be true)

1. `consensus_spread <= 3.0°F` (or the tighter `2.0°F` version for higher Sharpe, fewer trades)
2. `plus1_idx` exists among the market's listed buckets (NBS_fav isn't the highest tail bucket)
3. `0.005 <= yes_mid[plus1] <= 0.5` (excludes tick-floor dust and already-favorite regions)
4. Best NO-ask depth ≥ desired stake (see §6 capacity)
5. Slippage from best ask to intended stake ≤ 2¢ (protects edge)

If all 5 pass: **buy NO on the plus1 bucket** at 20:00 UTC.

## 4. Execution

### Time

- **Entry**: 20:00 UTC (16:00 EDT, 13:00 PDT). This is the entry hour used
  throughout the backtest; earlier entry windows may work too but have not
  been validated.
- **Exit**: Hold to market resolution (typically ~05:00 UTC the following
  day, when the resolution source publishes the actual daily max).

### Stake sizing

Baseline: 1-share-equivalent units (Kelly fraction ~0 for paper trading).
For real deployment, per §7:

- Paper trade first at nominal $10-20 per trade
- After 30 trades with realized ≥ $0.05/trade, scale to $50-100/trade
- Hard cap per market: stake ≤ 25% of observed depth within 2¢ of
  best NO-ask (keeps slippage under control)

### Order type

Limit order at `best_yes_bid + 0.01` (i.e., NO price = `1 - (best_yes_bid + 0.01)`).
If not filled in 5 minutes, step toward the ask by 1¢. This avoids paying
the full spread and captures the maker rebate on at least part of the
fill when available.

## 5. Expected performance (backtest)

### Headline

| metric | value |
|---|---|
| period | 2026-03-11 – 2026-04-10 (31 days) |
| markets | 11 US cities |
| filter | consensus_spread ≤ 3°F |
| trades | 94 |
| hit rate | 98.9% (93 wins, 1 loss) |
| per-trade PnL | +$0.083 |
| total PnL (1 share) | +$7.78 |
| IS t-stat (Mar 11-25) | +4.44 |
| OOS t-stat (Mar 26-Apr 10) | +4.98 |
| positive days | 27 of 27 (100%) |
| daily Sharpe | 1.108 |
| annualized Sharpe | 17.59 |
| worst 3 days | +$0.007, +$0.009, +$0.032 (all positive) |

### Breakdown by consensus threshold

| threshold | n | hit | per-trade | IS t | OOS t |
|---|---|---|---|---|---|
| ≤ 1.5°F | 32 | 96.9% | +$0.077 | +2.25 | +2.01 |
| ≤ 2.0°F | 52 | 98.1% | +$0.090 | +3.31 | +3.20 |
| **≤ 3.0°F** | **94** | **98.9%** | **+$0.083** | **+4.44** | **+4.98** |
| (no filter) | 179 | 96.1% | +$0.055 | +2.24 | +2.99 |

Looser filter (≤ 3°F) was the final choice because more trades at
similar per-trade edge give better statistical confidence. Tighter
filters (≤ 1.5°F) leave too few trades.

### Per-city

| city | n | hit | per-trade | t-stat |
|---|---|---|---|---|
| Miami | 17 | 100.0% | +$0.023 | +4.31 |
| Houston | 13 | 100.0% | +$0.091 | +3.54 |
| Atlanta | 12 | 100.0% | +$0.098 | +4.24 |
| NYC | 12 | 100.0% | +$0.032 | +3.48 |
| LA | 11 | 100.0% | +$0.080 | +3.06 |
| Dallas | 8 | 100.0% | +$0.136 | +3.07 |
| Denver | 7 | 100.0% | +$0.194 | +5.12 |
| Seattle | 5 | 100.0% | +$0.177 | +3.09 |
| Austin | 5 | 100.0% | +$0.134 | +1.62 |
| Chicago | 4 | 75.0% | -$0.058 | -0.29 |
| SF | 0 | — | — | — |

Chicago is the only city to lose (1 of 4 trades — actual temp landed
by 1°F into the +1 bucket). SF had no consensus-tight days in the
backtest window.

### Overfit protection

The following variants of the strategy were tested and rejected
because they fail out-of-sample:

- **Offset = +2 NO**: IS t = +5.10 (looks great), OOS t = -1.05 (fails).
  Classic overfit trap.
- **Offset basket (+1 and +2)**: IS t = +6.02, OOS t = +1.60 (diluted).
- **Offset = +3 NO**: Works OOS (t = +3.86) but per-trade only +$0.018
  because NO price is ~$0.98 — capital-inefficient.
- **Offset = -1 YES (symmetric)**: t = +0.80, no edge.
- **Offset = -1 NO (symmetric fade)**: t = -1.37, negative.

The +1-NO edge with consensus filter is the **only** variant that passes
a strict IS/OOS holdout discipline.

## 6. Capacity (realistic)

Based on 2-3 days of Polymarket CLOB book data (Apr 11-13 2026), observing
8 qualifying +1 offset buckets:

### Per-market depth (single city-bucket)

| slippage tolerance | median shares | approx $ |
|---|---|---|
| at best NO-ask | 28 | $22 |
| within 1¢ | 52 | $41 |
| **within 2¢** | **116** | **$92** |
| within 5¢ | 145 | $115 |

### Per-day aggregate

Typical day with 2-3 qualifying markets:

| slippage | total capital absorbable |
|---|---|
| at best NO-ask | $50-200 |
| within 2¢ (recommended) | **$300-500** |
| within 5¢ (aggressive, edge erodes) | $600-900 |

### Practical deployment ceiling

- **~$500/day total capital** with acceptable slippage
- **~$10-25/day expected PnL** at that scale
- Beyond ~$500/day, walking the book deeper eats the edge

This is a **portfolio** — you cannot concentrate in one market. Each
qualifying bucket gets ~$50-150 of stake; diversification across 2-6
markets per day is what drives the 98.9% hit rate and 100% positive-day
streak.

### Caveats

1. Capacity estimate is from a **tiny sample** (n=8 qualifying buckets
   over 3 days). Real execution will reveal the true distribution.
2. Observed depth may shrink once we actually bid — market makers may
   pull quotes.
3. Fill is often incremental (hours) as YES-bidders lift, not instant.

## 7. Risk management

### Loss scenarios

| event | probability | consequence | mitigation |
|---|---|---|---|
| +1 bucket wins | ~3% per trade | lose $0.80-0.90 per share | 98.9% hit rate absorbs this; 27/27 days positive in backtest |
| NBS + GFS + HRRR all wrong together | uncommon on consensus-tight days | multiple simultaneous losses | diversify across cities |
| Retail gets smarter | possible over time | edge compresses to fair | monitor realized edge weekly; kill if < $0/trade over 20+ trades |
| Polymarket changes fee structure | low | PnL math shifts | recompute fees before trades |
| Polymarket changes bucket structure (e.g. 5°F buckets) | low | strategy framework breaks | replan |

### Kill switches

- **3 consecutive negative days** — halt, re-examine
- **Realized < $0/trade after 40 trades** — halt, likely regime change
- **Observed depth at best ask < 5 shares across all markets for 3 days** — halt, liquidity gone

### What can go wrong even on a "winning" day

Since we buy NO at $0.85+, a single loss costs ~$0.86 per share. One
loss wipes out ~10 winning trades. The 98.9% hit rate in backtest means
this is survivable in expectation, but SINGLE-DAY P&L is skewed
(small wins, occasional large loss).

Sizing must account for this: the max drawdown over any 100 trades in
the backtest was about 1 losing trade × $0.80 = -$0.80, but at $100
per-trade stake that's -$80. Position size so a single loss is tolerable.

## 8. Deployment checklist

### Week 0: Infrastructure

- [ ] Polymarket API key + proxy wallet set up
- [ ] `py-clob-client` installed, authenticated
- [ ] Book recorder running on all 11 US cities (already live)
- [ ] NBS + GFS MOS + HRRR feeds refreshed daily before 20 UTC
- [ ] `recommender.py` runs cleanly and emits recommendations

### Week 1-2: Paper

- [ ] Log real NO-ask at 20 UTC for each recommendation
- [ ] Log actual resolution outcome
- [ ] Compute realized per-trade vs backtest (+$0.083) expectation
- [ ] Measure: are recommendations filling at our intended prices?

### Week 3-4: Small-scale live

- [ ] Deploy if realized ≥ $0.05/trade over 30 paper trades
- [ ] Start $10-20 per trade
- [ ] Keep paper ledger in parallel for comparison

### Ongoing

- [ ] Daily: review recommendations + fills
- [ ] Weekly: compute rolling realized edge, Sharpe
- [ ] Monthly: stress test (consensus-loose days, different seasons)
- [ ] If realized tracking backtest: scale toward $100/trade ceiling

## 9. References

- [Full v3 backtest findings](../../notebooks/experiments/backtest-v3/FINDINGS.md)
- [Strategy D retraction (predecessor)](../../vault/Weather%20Vault/wiki/syntheses/2026-04-14%20Strategy%20D%20does%20NOT%20replicate%20in%20clean%20temporal%20holdout.md)
- [Polymarket fee structure](../../vault/Weather%20Vault/wiki/syntheses/2026-04-11%20Polymarket%20fee%20structure%20+%20maker%20rebate%20pivot.md)
- [Polymarket CLOB WebSocket](../../vault/Weather%20Vault/wiki/concepts/Polymarket%20CLOB%20WebSocket.md)

## 10. Changelog

- **2026-04-15** — Strategy distilled from v3 iter 1-9 (backtest-v2 branch)

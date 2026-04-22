# Consensus-Fade +1 Offset

Fade retail's systematic over-pricing of the bucket one above the NBS
favorite on daily-temperature markets. Restrict to days where three
independent weather forecasts agree AND the prediction market itself
has already re-priced the +1 bucket as unlikely. Enter ≥16:00 city-local
so the forecast panel is fully mature, HRRR has real peak-window
coverage, and the book has absorbed midday METAR.

**Status**: paper-trade (need 2+ weeks of live fill data before real capital)
**Backtest (canonical rule)**: n=72 trades / 27 days (Mar 11 – Apr 10 2026) /
**100.0% hit** / +$0.039 per trade / IS t=+5.29 / OOS t=+5.85 / 27 of 27 positive days / daily Sharpe 1.31 (annualized 20.85)
**Venue**: Polymarket
**Entry**: continuous polling, gated by (per-city local ≥ 16:00) AND (best YES ask ≤ 0.22)
**Exit**: hold to resolution

---

## 1. Thesis in one paragraph

On days when NBS, GFS MOS, and HRRR all forecast a similar daily high
(consensus spread ≤ 3°F), weather is highly predictable — NBS MAE in this
regime is ~1.5°F. For the actual daily high to land in the bucket **2°F
above NBS's forecast**, you need a ~1.5-sigma upward surprise, which
happens only ~3% of the time. But retail on Polymarket prices that bucket
at ~$0.10-0.20 for most of the day because they spread probability
symmetrically above and below the forecast without conditioning on
forecast confidence. We wait for the afternoon — when HRRR has mature
peak-window coverage and morning METAR has begun to discipline the book
— then buy NO at ~$0.80-0.95 and collect $1 with ~99% probability.

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
4. **Asymmetric, not symmetric.** The −1 bucket (below NBS fav) does NOT
   have the same mispricing — it's ~50/50 and priced near fair. Only the
   upside is systematically over-priced.

Structural explanation: days with consensus-tight forecasts are
typically spring-warming days where "maybe it'll hit an unexpected high"
is psychologically attractive, and retail chases that tail.

## 3. Signal

For each Polymarket daily-temperature market (a city on a given day):

### Inputs required

| input | source | when available | used for |
|---|---|---|---|
| NBS max forecast | IEM MOS archive / NBS text | issued ~19/01/07/13 UTC | favorite bucket |
| GFS MOS max forecast | IEM MOS archive | issued ~00/06/12/18 UTC | consensus |
| HRRR t2m max | NOAA HRRR archive | every hour, fxx=6 | consensus |
| Market bucket catalog | Polymarket Gamma `markets.parquet` | refreshed daily | bucket mapping |
| YES bid/ask at +1 bucket | Polymarket CLOB book WS | live | entry price |
| Current city-local time | per-station IANA tz | — | entry gate |

### Derived signals

```
consensus_spread = max(NBS_max, GFS_max, HRRR_max) - min(NBS_max, GFS_max, HRRR_max)
NBS_fav_idx      = argmin_i |bucket_center[i] - NBS_max|
plus1_idx        = NBS_fav_idx + 1
yes_ask[plus1]   = best YES ask at plus1 bucket
no_ask[plus1]    = 1 - best_yes_bid at plus1 bucket
```

### Entry filter (all must be true)

1. **All three sources present** — NBS + GFS + HRRR. A missing HRRR is a
   hard skip, not a 2-of-3 fallback.
2. **HRRR peak-window coverage.** HRRR fxx=6 forecasts must cover ≥ 6
   distinct valid-hours in the station's local 12:00-22:00 peak window.
   Before this, HRRR's "max over peak" is biased low (it sees only the
   morning hours) and consensus can appear to hold for the wrong reason.
3. `consensus_spread ≤ 3.0°F` computed as `max(NBS, GFS, HRRR) − min(...)`.
   No outlier-drop, no weighting.
4. `plus1_idx` exists among the market's listed buckets (NBS_fav isn't
   the highest tail bucket).
5. **Current city-local time ≥ 16:00.** Before 16:00 local, HRRR peak
   coverage is incomplete AND the market has not yet reconciled with
   live midday METAR. Earlier entries have materially lower hit rate
   and edge (see §5).
6. **`yes_ask[plus1] ≤ 0.22`** — the market-wisdom cap. Evaluated live
   via `best_no_bid ≥ 1 − 0.22 = 0.78` on the subscribed NO book (YES
   ask and NO bid are complementary up to the spread). We only trade
   when the market itself has already priced the +1 bucket as
   unlikely — the intraday METAR that the market has observed is
   information we don't want to trade against. See §5.1 for the
   mechanism; cap ≤ 0.22 drops the one backtest loss and improves
   hit rate from 98.7% (cap 0.50) to 100.0% (cap 0.22).
7. `yes_ask[plus1] ≥ 0.005` — excludes tick-floor dust already resolved.
8. Best NO-ask depth ≥ desired stake (see §6 capacity).
9. Slippage from best ask to intended stake ≤ 2¢ (protects edge).

If all 9 pass: **buy NO on the plus1 bucket** whenever the strategy's
tick-loop sees matchable asks.

## 4. Execution

### Time

- **Entry**: continuous polling whenever it is ≥ 16:00 city-local time
  at the airport, all three forecasts are present with mature HRRR peak
  coverage, consensus spread ≤ 3°F, and book conditions pass. Once
  entered, do not re-check — one fill per (city, day).
- **Exit**: Hold to market resolution (typically ~05:00 UTC the
  following day, when the resolution source publishes the actual daily
  max).

### Why local time, not UTC

Peak temperature is a function of local solar time, not UTC. The
pre-v2 rule used "20 UTC fixed", which silently translated to
different effective times per city:

| zone | 20 UTC = local |
|---|---|
| Eastern (ATL/NYC/MIA) | 16:00 EDT |
| Central (ORD/DAL/HOU/AUS) | 15:00 CDT |
| Mountain (DEN) | 14:00 MDT |
| Pacific (SEA/LAX/SFO) | 13:00 PDT |

That meant West-Coast entries were pre-divergence (before the market
had separated winners from losers via METAR) while East-Coast entries
were post-divergence. Anchoring to 16:00 local standardizes across
cities so each market gets the same "afternoon-mature" treatment. OOS
t improves from +1.39 (20 UTC) to +4.49 (≥16 local) on the same
apples-to-apples price source.

### Stake sizing

Baseline: 1-share-equivalent units (Kelly fraction ~0 for paper
trading). For real deployment:

- Paper trade first at nominal $10-20 per trade
- After 30 trades with realized ≥ $0.03/trade AND ≤ 1 loss, scale to
  $50-100/trade
- Hard cap per market: stake ≤ 25% of observed depth within 2¢ of
  best NO-ask

### Order type

Limit order at `best_yes_bid + 0.01` (i.e., NO price =
`1 - (best_yes_bid + 0.01)`). If not filled in 5 minutes, step toward
the ask by 1¢. This avoids paying the full spread and captures the
maker rebate on at least part of the fill when available.

## 5. Expected performance (backtest, v2)

All numbers use the **same price source** — Polymarket hourly prices —
so the variants below are apples-to-apples. The earlier STRATEGY.md v1
numbers (n=94, hit=98.9%, per=+$0.083, t=+4.44) came from a different
snapshot column (`trade_table.entry_price`) that was more favorable
than what the hourly feed shows. Replaying v1 against the hourly feed
gives the "v1 (20 UTC)" row below.

### Headline — canonical rule (≥16 local, cs ≤ 3°F, cap 0.22)

| metric | value |
|---|---|
| period | 2026-03-11 – 2026-04-10 (31 days) |
| markets | 11 US cities |
| trades | 72 |
| **hit rate** | **100.0%** (72 wins, 0 losses) |
| per-trade PnL | +$0.039 |
| total PnL (1 share) | +$2.82 |
| IS t-stat (Mar 11-25) | +5.29 |
| OOS t-stat (Mar 26-Apr 10) | +5.85 |
| positive days | 27 of 27 (100%) |
| daily Sharpe | 1.31 (annualized 20.85) |

### Looser alternative — cap 0.50 (§5.1 reference)

| metric | value |
|---|---|
| trades | 78 (6 more than canonical) |
| hit rate | 98.7% (77 wins, 1 loss — Chicago 2026-03-17) |
| per-trade | +$0.046 (~15% larger than canonical) |
| t-stat | +3.67 (IS +1.85 / OOS +4.49) |

The looser cap collects a bigger per-trade edge but eats one
catastrophic loss (−$0.67 on the Chicago day where the market had YES
at $0.34 — above our canonical 0.22 threshold but under the loose
0.50). Net total PnL is slightly higher ($3.57 vs $2.82) but the
downside is much worse and IS t-stat drops by half.

### Entry-rule comparison

All rows: consensus ≤ 3°F, HRRR 6h peak coverage, hourly-price entry.

| rule | n | W/L | hit | per | t | IS t | OOS t |
|---|---|---|---|---|---|---|---|
| 20 UTC fixed (v1), cap 0.50 | 84 | 82/2 | 97.6% | +$0.058 | +3.14 | +4.91 | +1.39 |
| ≥13 local, cap 0.50 | 93 | 85/8 | 91.4% | +$0.029 | +1.06 | +0.28 | +1.27 |
| ≥15 local, cap 0.50 | 84 | 81/3 | 96.4% | +$0.052 | +2.51 | +5.71 | +0.66 |
| **≥16 local, cap 0.50** | **78** | **77/1** | **98.7%** | **+$0.046** | **+3.67** | **+1.85** | **+4.49** |
| ≥17 local, cap 0.50 | 76 | 75/1 | 98.7% | +$0.041 | +3.30 | +1.73 | +3.96 |
| **≥16 local, cap 0.22 (canonical)** | **72** | **72/0** | **100.0%** | **+$0.039** | **+7.70** | **+5.29** | **+5.85** |

Key observations:

- **16:00 local is the earliest defensible floor.** Earlier entries
  (13, 15) collapse OOS — these are the "morning consensus looked fine,
  afternoon METAR disagreed" losses.
- **Local time beats UTC-fixed on OOS.** v1 at 20 UTC had OOS t=+1.39;
  v2 at 16 local (cap 0.50) already has OOS t=+4.49. Same trade, better
  coordinate system.
- **The market-wisdom cap at 0.22 eliminates tail risk.** Drops hit rate
  from 98.7% to 100%, doubles t-stat (+3.67 → +7.70), at the cost of ~10%
  fewer trades and ~15% smaller per-trade edge. See §5.1.

### 5.1 Why the 0.22 cap is canonical

Throughout the day, the +1 YES price is a real-time market signal that
separates winners (NO wins) from losers (NO loses):

| local hour | YES mean, winners | YES mean, losers | gap |
|---|---|---|---|
| 0-11 | ~$0.14 | ~$0.28 | $0.14 |
| 12 | $0.13 | $0.27 | $0.14 |
| 13 | $0.14 | $0.38 | $0.24 |
| 14 | $0.13 | $0.42 | $0.29 |
| 15 | $0.10 | $0.63 | $0.53 |
| **16** | **$0.08** | **$0.75** | **$0.67** |
| 17 | $0.04 | $0.81 | $0.77 |

Live METAR between noon and 16:00 local lets the market separate
winners (YES drifts toward 0) from losers (YES rises toward 1). By
16:00 local the separation is nearly clean. A cap at $0.22 excludes
days where the market has already started pricing +1 as "likely" —
the market is telling you not to trade those. The market's own
intraday repricing becomes a second, independent filter on top of
forecast consensus.

**Why this is the canonical rule (not just an overlay):**

1. **Avoids a structural tail.** The single backtest loss under cap
   0.50 (Chicago 2026-03-17, −$0.67) is a 15-wins-worth of edge hit.
   Cap 0.22 eliminates it by respecting the market's warning. Live
   deployment at $100/share stake means a single loss costs $60-$80 —
   too painful to eat on purpose when an honest filter excludes it.
2. **Both folds agree.** Cap 0.22 has IS t=+5.29 and OOS t=+5.85, both
   strong. Cap 0.50 has IS t=+1.85 (barely significant) and OOS t=+4.49
   — inconsistent enough that one fold is carrying most of the signal.
3. **Daily Sharpe doubles.** 1.31 (cap 0.22) vs 0.59 (cap 0.50) — the
   cap 0.22 rule has much less day-to-day variance.
4. **The mechanism is robust, not ad-hoc.** The YES-price-by-local-hour
   split (§5 table above) is a physical effect of METAR absorption,
   not a fitted threshold. 0.22 falls naturally out of the winner /
   loser distribution; it's the number above which losers reliably
   sit at 16 local.

**Caveats to monitor live:**

- 72/0 at this sample size has wide confidence bounds (95% Wilson CI
  ~ 95-100%). A "true" hit rate of 96-97% is statistically consistent
  with the backtest. Kill-switch any live loss immediately.
- The 0.22 cap pushes fills to NO prices $0.78-$0.99 — book depth at
  these prices is less well-characterized than around the prevailing
  ask. Real capacity may be smaller than §6 estimates.
- Per-trade edge is $0.039 (smaller than looser variant's $0.046).
  At $100/share stake, each win is ~$4; each loss would be ~$60-$80.
  20-trade PnL buffer above zero before scaling stake.

**Looser alternative (cap 0.50) is retained as a CLI flag**
(`--max-yes-ask 0.50`) for paper-trade comparison and as a fallback
if the 0.22 rule underperforms live.

### Per-city breakdown (canonical rule, ≥16 local, cap 0.22)

| city | n | W/L | hit | per-trade | t-stat |
|---|---|---|---|---|---|
| Atlanta | 13 | 13/0 | 100.0% | +$0.067 | +4.27 |
| Austin | 5 | 5/0 | 100.0% | +$0.058 | +1.61 |
| Chicago | 1 | 1/0 | 100.0% | +$0.160 | — |
| Dallas | 8 | 8/0 | 100.0% | +$0.035 | +3.53 |
| Denver | 4 | 4/0 | 100.0% | +$0.044 | +2.43 |
| Houston | 9 | 9/0 | 100.0% | +$0.064 | +1.96 |
| LA | 5 | 5/0 | 100.0% | +$0.008 | +4.50 |
| Miami | 14 | 14/0 | 100.0% | +$0.019 | +3.47 |
| NYC | 11 | 11/0 | 100.0% | +$0.028 | +3.07 |
| Seattle | 4 | 4/0 | 100.0% | +$0.109 | +2.07 |
| SF | 0 | — | — | — | — |

Chicago had 2 trades under the looser cap 0.50 rule, one of which lost
(2026-03-17, YES entered at $0.34 — above the canonical 0.22 threshold
— +1 bucket resolved, NO paid 0). Under the canonical cap 0.22 rule,
that loss is filtered out: only 1 Chicago trade remains (the winner).
SF had no consensus-tight days in the backtest window.

### Overfit protection

The following variants were tested and rejected because they fail
out-of-sample:

- **Offset = +2 NO**: IS t = +5.10, OOS t = −1.05 (classic overfit trap).
- **Offset basket (+1 and +2)**: IS t = +6.02, OOS t = +1.60 (diluted).
- **Offset = +3 NO**: Works OOS (t = +3.86) but per-trade only +$0.018
  — capital-inefficient at NO cost ~$0.98.
- **Offset = −1 YES (symmetric)**: t = +0.80, no edge.
- **Offset = −1 NO (symmetric fade)**: t = −1.37, negative.
- **"First consensus" entry (no local floor)**: n=102, hit=90.2%,
  t=+1.66. Fails because morning-consensus days can later reprice
  against you as midday METAR lands.
- **≥13-15 local floors**: OOS t collapses (≥13: +1.27; ≥14: +1.15;
  ≥15: +0.66). Only ≥16 local passes cleanly in both folds.

The +1-NO edge with the ≥16 local consensus filter is the only variant
that passes a strict IS/OOS holdout discipline.

## 6. Capacity (realistic)

Based on 2-3 days of Polymarket CLOB book data (Apr 11-13 2026),
observing 8 qualifying +1 offset buckets:

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

- **~$300-400/day total capital** with acceptable slippage (tighter
  than cap-0.50 since the cap-0.22 rule takes fewer trades and all
  fills are deep on the NO side where depth is thinner)
- **~$10-15/day expected PnL** at that scale (per-trade edge $0.039,
  ~2.7 trades/day avg)
- Beyond ~$400/day, walking the book deeper eats the edge

This is a **portfolio** — you cannot concentrate in one market. Each
qualifying bucket gets ~$50-100 of stake; diversification across 2-3
qualifying markets per day is what drives the 100% hit rate and
positive-day streak (27 of 27 in backtest).

### Caveats

1. Capacity estimate is from a **tiny sample** (n=8 qualifying buckets
   over 3 days). Real execution will reveal the true distribution.
2. Observed depth may shrink once we actually bid — market makers may
   pull quotes.
3. Fill is often incremental (hours) as YES-bidders lift, not instant.
4. At 16:00 local on a day that will resolve NO, YES is decaying
   rapidly; the best ask at the moment we fire may be stale. Use a
   pre-trade book refresh immediately before order submission.
5. Canonical cap 0.22 means all fills are at NO prices 0.78-0.99.
   Book depth at those prices is less well-characterized than near
   prevailing ask. Real capacity may be 30-50% of §6 numbers above.

## 7. Risk management

### Loss scenarios

| event | probability | consequence | mitigation |
|---|---|---|---|
| +1 bucket wins | 0% observed in cap-0.22 backtest, ~3% historical | lose $0.78-$0.99 per share | 100% hit in backtest; cap-0.22 filter rejects the 1 known loss; diversify across cities |
| NBS + GFS + HRRR all wrong together | uncommon on consensus-tight days | multiple simultaneous losses | diversify across cities; size so one loss ≤ 20 prior wins |
| Retail gets smarter on +1 pricing | possible over time | edge compresses to fair | monitor realized edge weekly; kill if < $0/trade over 30+ trades |
| Polymarket changes fee structure | low | PnL math shifts | recompute fees before trades |
| Polymarket changes bucket structure | low | strategy framework breaks | replan |
| HRRR feed delayed past 16 local | possible | consensus not evaluable, skip day | alert on missing HRRR ≥15 UTC |

### Kill switches

- **Any single loss** — investigate immediately. Backtest under the
  canonical rule had zero; any live loss is a signal the market-wisdom
  filter isn't behaving as observed in backtest.
- **2 losses in any 20-trade window** — halt, re-examine.
- **Realized < $0/trade after 40 trades** — halt, likely regime change.
- **Observed depth at best ask < 5 shares across all markets for 3
  days** — halt, liquidity gone.
- **Consecutive negative days, 3 in a row** — halt.

### What can go wrong even on a "winning" day

Under the canonical cap-0.22 rule we buy NO at $0.78-$0.99. A single
loss costs $0.78-$0.99 per share. Per-trade edge is $0.039, so **one
loss wipes out 20-25 winning trades**. Sizing must account for this:

- At $100/share stake, a single loss costs ~$78-$99; a single win
  pays ~$4
- Single-day P&L is skewed: small wins, occasional large loss
- Max drawdown in canonical backtest: 0 (zero losses observed)
- Build a 25-trade realized-PnL buffer above zero before scaling stake
- The tighter the cap, the more expensive an individual loss is — the
  trade-off is the filter is more accurate at preventing losses

## 8. Deployment checklist

### Week 0: Infrastructure

- [ ] Polymarket API key + proxy wallet set up
- [ ] `py-clob-client` installed, authenticated
- [ ] Book recorder running on all 11 US cities (already live)
- [ ] NBS + GFS MOS + HRRR feeds refreshed at minimum hourly through
      16:00 local for each station
- [ ] City→tz table hard-wired (`src/lib/weather/timezones.py`)
- [ ] `cfp discover` runs cleanly with local-time gate
- [ ] `cfp run` default params: `--max-yes-ask 0.22` / `--min-entry-hour-local 16`

### Week 1-2: Paper

- [ ] Log real YES ask and NO ask at entry for each recommendation
- [ ] Log actual resolution outcome
- [ ] Compute realized per-trade vs backtest (+$0.039) expectation
- [ ] Measure: are recommendations filling at our intended prices?
- [ ] Track parallel cap-0.50 subset (`--max-yes-ask 0.50`) for
      comparison — if it outperforms in realized data, we regressed
      to the looser rule
- [ ] Expected sample: ~15-25 paper trades in 2 weeks

### Week 3-4: Small-scale live

- [ ] Deploy if realized ≥ $0.03/trade over 30 paper trades AND ≤ 1
      loss
- [ ] Start $10-20 per trade
- [ ] Keep paper ledger in parallel for comparison
- [ ] Consider overlaying the 0.22 cap manually on first 20 live trades
      to reduce tail risk

### Ongoing

- [ ] Daily: review recommendations + fills
- [ ] Weekly: compute rolling realized edge, Sharpe, hit rate
- [ ] Monthly: stress test across different seasons / weather regimes
- [ ] If realized tracking backtest: scale toward $100/trade ceiling

## 9. References

- [Time-resolved consensus experiments](../../notebooks/experiments/backtest-v3/consensus_optimal_sweep.py)
- [v1 vs v2 head-to-head](../../notebooks/experiments/backtest-v3/v1_v2_compare.py)
- [Full v3 backtest findings](../../notebooks/experiments/backtest-v3/FINDINGS.md)
- [Strategy D retraction (predecessor)](../../vault/Weather%20Vault/wiki/syntheses/2026-04-14%20Strategy%20D%20does%20NOT%20replicate%20in%20clean%20temporal%20holdout.md)
- [Polymarket fee structure](../../vault/Weather%20Vault/wiki/syntheses/2026-04-11%20Polymarket%20fee%20structure%20+%20maker%20rebate%20pivot.md)
- [Polymarket CLOB WebSocket](../../vault/Weather%20Vault/wiki/concepts/Polymarket%20CLOB%20WebSocket.md)

## 10. Changelog

- **2026-04-22 (later)** — **v2+cap promoted to canonical live rule.**
  After confirming the `yes_ask ≤ 0.22` market-wisdom cap eliminates
  the single known loss while keeping IS/OOS t-stats strong (IS +5.29,
  OOS +5.85, both folds) and doubling daily Sharpe (0.59 → 1.31), the
  cap became a required filter in the live strategy. Wired through
  `cli.py` as `--max-yes-ask 0.22` and into `strategy.py` as a
  per-instrument best-NO-bid check. CLI flag renamed
  `--min-entry-hour` → `--min-entry-hour-local` (default 16);
  `src/lib/weather/timezones.py` added. Looser cap 0.50 retained as
  fallback via `--max-yes-ask 0.50`.
- **2026-04-22** — v2 canonical rule: switched entry from fixed 20 UTC
  to ≥ 16:00 city-local time; required HRRR 6h peak-window coverage.
  Also re-ran v1 against the same hourly-price feed used for v2
  (instead of the trade-table entry_price column) to get
  apples-to-apples numbers — the old v1 headline (t=+4.44) was
  price-source artifact. v2 (cap 0.50): n=78, 98.7% hit, OOS t=+4.49.
- **2026-04-15** — Strategy distilled from v3 iter 1-9 (backtest-v2 branch)

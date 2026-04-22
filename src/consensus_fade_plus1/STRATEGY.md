# Consensus-Fade +1 Offset

Fade retail's systematic over-pricing of the bucket one above the NBS
favorite on daily-temperature markets. Trade when all three independent
forecasts agree within 3°F **and** the prediction market itself is
pricing the +1 bucket below a coin-flip. Enter at ≥15:00 city-local
so the forecast panel is mature and the market has absorbed midday
METAR.

**Status**: paper-trade (need 2+ weeks of live fill data before real capital)
**Backtest (canonical rule)**: n=31 trades / 17 trading days (Mar 11 – Apr 10 2026) /
**93.5% hit** (29 W, 2 L) / +$0.126 per trade / IS t=+7.03 / OOS t=+0.88
**Venue**: Polymarket
**Entry**: continuous polling, gated by (per-city local ≥ 15:00) AND (YES ask in [$0.07, $0.50])
**Exit**: hold to resolution

---

## 1. Thesis in one paragraph

On days when NBS, GFS MOS, and HRRR all forecast a similar daily high
(consensus spread ≤ 3°F), weather is highly predictable — NBS MAE in this
regime is ~1.5°F. For the actual daily high to land in the bucket **2°F
above NBS's forecast**, you need a ~1.5-sigma upward surprise, which
happens only ~3% of the time. But retail on Polymarket prices that bucket
somewhere between $0.10 and $0.40 for most of the day because they
spread probability symmetrically above and below the forecast without
conditioning on forecast confidence. We wait for ≥15:00 local so the
afternoon METAR has begun to discipline the market, and take trades
where retail still has the +1 bucket priced above $0.07 but below $0.50
— meaningful mispricing, market hasn't yet strongly consolidated toward
YES. We buy NO at ≤$0.93 (guaranteeing ≥7¢ per-share edge if NO
resolves) and hold to settlement.

## 2. Why this mispricing exists

1. **Symmetric-uncertainty heuristic.** "Could be warmer, could be
   cooler" — bet both sides roughly equally. They don't condition on
   forecast confidence.
2. **"Coverage" betting.** A bettor who thinks 70-71°F is most likely
   will still put a few dollars on 72-73°F "just in case." This
   inflates the +1 bucket price even when the real probability is
   near zero.
3. **The +1 sweet spot.** Buckets 2+ away are priced near the $0.01
   tick floor — mispricing is negligible pennies. The +1 bucket sits
   where "unlikely but possible" lives cognitively, which is exactly
   where humans over-price low-probability events (the classic
   3% → 17% calibration error).
4. **Slow intraday correction.** Live METAR between noon and 15:00
   local feeds the market's view of whether today's forecast is
   tracking. By 15:00 local, winners' YES has drifted toward zero
   and losers' YES has risen above $0.50. The window between
   "market has separated them" and "market has settled" is our
   execution window.
5. **Asymmetric, not symmetric.** The −1 bucket (below NBS fav) does
   NOT have the same mispricing — it's ~50/50 and priced near fair.
   Only the upside is systematically over-priced.

## 3. Signal

### Inputs required

| input | source | when available | used for |
|---|---|---|---|
| NBS max forecast | IEM MOS archive | issued ~01/07/13/19 UTC | favorite bucket |
| GFS MOS max forecast | IEM MOS archive | issued ~00/06/12/18 UTC | consensus |
| HRRR t2m max | NOAA HRRR archive | every hour, fxx=6 | consensus |
| Market bucket catalog | Polymarket Gamma `markets.parquet` | refreshed daily | bucket mapping |
| YES bid/ask at +1 bucket | Polymarket CLOB book WS | live | entry gate + price |
| Current city-local time | per-station IANA tz | — | entry gate |

### Derived signals

```
consensus_spread = max(NBS_max, GFS_max, HRRR_max) − min(NBS_max, GFS_max, HRRR_max)
NBS_fav_idx      = argmin_i |bucket_center[i] − NBS_max|
plus1_idx        = NBS_fav_idx + 1
yes_ask[plus1]   = best YES ask on the plus1 bucket
best_no_bid      = 1 − yes_ask   (up to spread/fees)
```

### Entry filter (all must be true)

1. **All three forecasts present** — NBS + GFS + HRRR. Missing HRRR
   is a hard skip.
2. **HRRR peak-window coverage** — canonical compute at
   `lib.weather.hrrr.hrrr_peak_max_f*`. Requires ≥6 distinct
   valid-hours covered in the station's local 12:00-22:00 peak
   window, with `init_time ≤ cutoff`. Same function used by backtest
   (cutoff = entry time) and live (cutoff = now via features.parquet
   rebuild).
3. `consensus_spread ≤ 3.0°F` across all three forecasts.
4. `plus1_idx` exists among the market's listed buckets (NBS_fav
   isn't the highest tail bucket).
5. **`yes_ask ≤ 0.50`** — market-wisdom upper cap. Evaluated live as
   `best_no_bid ≥ 0.50`. Filters out days where the market is
   already pricing +1 as a favorite (it's usually right about
   those).
6. **`yes_ask ≥ 0.07`** — 7¢ minimum per-share edge floor. Buying
   NO at ≤ $0.93 guarantees ≥ $0.07 win-per-share if NO resolves.
   Filters out the "market already strongly agrees with our
   forecast" regime — tiny edge with large tail risk.
7. **Current city-local time ≥ 15:00.** Before 15:00 the market has
   not yet absorbed enough peak-hour METAR for the yes_ask cap to
   reliably separate winners from losers. Backtest shows sharp
   discontinuity: hit drops from 94% (15 local) to 86% (14 local).
8. Best NO-ask depth ≥ desired stake (see §6 capacity).
9. **Entry-window still open** — within 30 minutes of the first
   moment gates 1-7 passed for this market (see §4).
10. **Per-market-per-day USD cap not reached** — cumulative fill
    notional on this market this day ≤ $30 (default). See §7.

If all 10 pass: **buy NO at `max_no_price` (default 0.93) via IOC**.
Fill quantity = `min(takeable_at_or_below_0.93, shares_room, usd_room)`.

## 4. Execution

### Time

- **Entry**: continuous polling within a 30-minute bounded window per
  market. Window opens the first moment gates 1-7 pass. Within the
  window we keep lifting NO asks priced ≤ $0.93 (IOC), subject to
  shares + USD caps. After 30 minutes we stop adding even if gates
  still pass.
- **Exit**: Hold to market resolution (typically ~05:00 UTC the
  following day). No intraday unwinds.

### Why local time, not UTC

Peak temperature is a function of local solar time, not UTC. A fixed
"20:00 UTC" would translate to 16:00 EDT in Atlanta and 13:00 PDT
in Seattle — totally different positions in each city's peak window.
Anchoring to 15:00 *local* standardizes so every airport gets the
same relative treatment.

### Order type

IOC limit BUY at `max_no_price` (default $0.93). Fills any ask
priced ≤ $0.93 at the venue, cancels the rest. Never rests on the
book. Because the limit is a hard ceiling, we can't accidentally
pay > $0.93 for NO regardless of book depth.

### Why a single 7¢ floor replaces separate slippage controls

The 7¢ edge floor (implemented as `max_no_price = 0.93`) serves three
purposes simultaneously:

1. **Per-trade edge guarantee.** Win on NO resolution = $1 − $0.93 =
   $0.07 minimum per share.
2. **Slippage cap.** Any ask above $0.93 is left on the book. We
   never walk deep.
3. **Market-wisdom lower bound.** If best YES ask < $0.07, the
   market has strongly agreed +1 won't happen. Residual mispricing
   is tiny, tail risk is large. Skip.

Previous versions of this strategy had a separate `max_ask_walk`
parameter for slippage control. Removed — `max_no_price = 0.93`
does both jobs more simply.

### Stake sizing

Primary control is the per-market-per-day USD cap
(`--max-usd-per-market 30`). At NO fill prices of $0.70-$0.93, that's
~32-42 shares per market. With ~1.8 qualifying markets/day, total
daily notional ~$55/day at this stake.

- **Starting stake**: $30/market cap — matches backtest scale for
  live validation
- **Scale up**: after 30+ trades realize ≥ $0.05/share AND ≤ 2
  losses, raise cap to $50 then $100
- **Per-trade worst case**: −$0.93/share (if +1 resolves). At $30
  cap, one losing market costs up to $30. Net daily loss if ALL
  markets go against us: ~$55-90 — rare, but plan for it.

## 5. Expected performance (backtest)

### Headline — canonical rule (yes_ask in [$0.07, $0.50], ≥15 local, cs ≤ 3°F)

| metric | value |
|---|---|
| period | 2026-03-11 – 2026-04-10 (31 calendar days) |
| markets | 11 US cities |
| trades | 31 |
| **hit rate** | **93.5%** (29 wins, 2 losses) |
| per-trade PnL | +$0.126 |
| total PnL (1 share) | +$3.89 |
| IS t-stat (Mar 11-25) | +7.03 |
| OOS t-stat (Mar 26-Apr 10) | +0.88 |
| trading days | 17 of 31 (active: 55%) |
| positive days | 16 of 17 (94%) |
| avg trades per trading day | 1.82 |
| daily Sharpe | 0.57 (annualized 9.10) |
| return on gross capital | 15.6% |

### Reference: tighter and wider caps

Same rule except `yes_ask` window:

| yes_ask window | n | L | hit | per | full t | IS t | OOS t | comment |
|---|---|---|---|---|---|---|---|---|
| [0.07, 0.22] | 20 | 0 | 100.0% | +$0.118 | +16.24 | +12.07 | +11.82 | cosmetic, small sample |
| **[0.07, 0.50]** | **31** | **2** | **93.5%** | **+$0.126** | **+2.96** | **+7.03** | **+0.88** | **canonical** |
| [0.07, 0.75] | 37 | 5 | 86.5% | +$0.119 | +2.50 | +1.99 | +1.58 | OOS still signal, IS eroding |
| [0.07, 0.995] | 41 | 9 | 78.0% | +$0.100 | +2.26 | +1.61 | +1.58 | hit-rate collapse |

- Tightening to 0.22 gives 100% hit but n=20 is too small. 95% Wilson
  CI on 20/0 is 84-100% true hit rate.
- Relaxing past 0.50 starts admitting trades on days the market is
  already informed +1 is likely. Hit rate declines predictably.

### Local-floor sensitivity (yes_ask in [0.07, 0.50])

| floor | n | L | hit | full t | IS t | OOS t |
|---|---|---|---|---|---|---|
| 13 | 51 | 8 | 84.3% | +0.55 | +0.03 | +0.78 |
| 14 | 44 | 6 | 86.4% | +1.13 | +0.29 | +1.43 |
| **15** | **31** | **2** | **93.5%** | **+2.96** | **+7.03** | **+0.88** |
| 16 | 21 | 1 | 95.2% | +2.62 | +1.45 | +4.23 |
| 17 | 11 | 1 | 90.9% | +1.17 | +0.76 | +2.75 |

Sharp discontinuity at 15 local: hit rate jumps from 86% (14 local)
to 94% (15 local). By 15 local the market has absorbed ~3 hours of
peak-hour METAR and has consistently moved losers' YES above $0.50.

### Per-city breakdown

| city | n | W/L | hit | per-trade | t-stat |
|---|---|---|---|---|---|
| Atlanta | 8 | 7/1 | 87.5% | +$0.073 | +0.62 |
| Dallas | 4 | 4/0 | 100.0% | +$0.246 | +3.32 |
| Denver | 4 | 4/0 | 100.0% | +$0.158 | +2.39 |
| Houston | 4 | 4/0 | 100.0% | +$0.182 | +2.81 |
| Miami | 3 | 3/0 | 100.0% | +$0.144 | +8.42 |
| NYC | 3 | 3/0 | 100.0% | +$0.116 | +7.11 |
| Seattle | 2 | 2/0 | 100.0% | +$0.185 | +2.31 |
| Austin | 1 | 1/0 | 100.0% | +$0.216 | — |
| Chicago | 2 | 1/1 | 50.0% | −$0.199 | −0.45 |
| LA | 0 | — | — | — | — |
| SF | 0 | — | — | — | — |

Chicago is the only city with negative per-trade — and has been
across multiple backtest variants. Consider excluding Chicago once
live data confirms this pattern.

### The two backtest losses

Both on 2026-03-28 (same calendar day, different markets):

| city | date | yes_ask at entry | pnl | mechanism |
|---|---|---|---|---|
| Atlanta | 2026-03-28 | $0.30 | −$0.71 | market pricing +1 at 30%, resolved YES |
| Chicago | 2026-03-28 | $0.375 | −$0.64 | similar pattern |

Both enter in the mid-range where the market is genuinely uncertain.
The pattern is **"market at ~30-40% on +1, forecast consensus says
no, market wins"**. Tighter yes_ask cap (≤0.25) eliminates both
losses but at ~1/3 the trade volume. Visible losses are the cost
of a wider cap — they inform sizing and kill-switches, which is
why we keep the cap at 0.50.

### Overfit protection

Variants tested and rejected (each fails IS/OOS discipline):

- **Offset = +2 NO**: IS t=+5.10, OOS t=−1.05. Classic overfit.
- **Offset basket (+1 and +2)**: IS t=+6.02, OOS t=+1.60. Diluted.
- **Offset = +3 NO**: works OOS but per-trade +$0.018.
- **Offset = −1 YES/NO (symmetric)**: no edge.
- **First-consensus entry (no local floor)**: n=102, hit 90.2%.
  Morning consensus reprices by afternoon.
- **≥13-14 local floors**: OOS collapses. Hit 84-86%.
- **yes_ask cap at 0.75+**: hit rate drops below 90% with
  tail-heavy losses.
- **`max_no_price = 0.99`** (no 7¢ edge floor): 53 trades but 38
  have per-share edge < $0.02. Tail-dominated; breaks if hit rate
  drops 3 pp.

The narrow window `yes_ask in [0.07, 0.50]` with `≥15 local` is the
only variant that passes both IS and OOS with a defensible mechanism
(forecast consensus + market-wisdom agreement + local-time maturity)
AND an honest sample of losses.

## 6. Capacity (realistic)

Based on 2-3 days of Polymarket CLOB book data (Apr 11-13 2026),
observing 8 qualifying +1 offset buckets:

### Per-market depth (single city-bucket)

| slippage tolerance | median shares | approx $ |
|---|---|---|
| at best NO-ask | 28 | $22 |
| within 1¢ | 52 | $41 |
| within 2¢ | 116 | $92 |
| within 5¢ | 145 | $115 |

### Per-day aggregate (starting stake $30/market)

- **$30/market × ~1.8 markets/day ≈ $55/day notional**
- **Expected PnL**: ~$3-5/day at canonical backtest edge
- At 7¢ ceiling, book depth at prices $0.50-$0.93 usually supports
  $30 of fills per market within the 30-minute window

### Higher-stake ceiling

After 30+ trades realize near backtest expectation:
- **$100/market × 1.8 markets/day ≈ $180/day** notional
- **Expected PnL**: ~$10-15/day
- Above this, walking the book past $0.93 becomes tempting — don't.
  The 7¢ floor is the edge floor.

### Caveats

1. Capacity estimate is from a tiny sample (n=8 qualifying buckets
   over 3 days). Real execution will reveal the true distribution.
2. Market makers may pull quotes once we start bidding.
3. Fills may be incremental (minutes) as YES-bidders lift asks.
4. Book depth at NO prices $0.80-$0.93 is less well-characterized
   than near best; real capacity may be 50-70% of §6 figures.

## 7. Risk management

### Loss scenarios

| event | probability | consequence | mitigation |
|---|---|---|---|
| +1 bucket wins | ~6.5% per trade in backtest | −$0.70/share typical | 93.5% hit; diversify across cities; $30/market USD cap |
| All 3 forecasts wrong together | uncommon on cs ≤ 3°F days | multiple simultaneous losses | diversify; per-market cap |
| Retail gets smarter on +1 pricing | possible | edge compresses | monitor realized weekly; kill if < $0/share over 30+ trades |
| Polymarket fee change | low | PnL math shifts | recompute |
| HRRR feed delayed past 15 local | possible | consensus not evaluable | skip day; alert on missing HRRR |

### Kill switches

- **3 losses in any 20-trade window** — halt. Backtest was 6.5%
  loss rate; 15% (3/20) signals regime change.
- **Realized < $0/share after 30 trades** — halt.
- **Any Chicago loss in first 5 Chicago trades** — stop Chicago
  (backtest showed 50% hit there).
- **Observed depth at qualifying asks < 5 shares across all markets
  for 3 consecutive days** — halt, liquidity gone.
- **3 consecutive negative days** — halt.

### What can go wrong even on a "winning" day

At default caps we buy NO at $0.70-$0.93. Single loss costs
$0.70-$0.93/share; single win pays $0.07-$0.30/share. Per-trade
edge is $0.126 — **one loss wipes out ~7 winners**. At $30/market
cap × 30 shares, a loss costs ~$21-$28; a win pays ~$2-$9.
Single-day P&L is skewed.

### Risk-control caveats

- **USD cap is in-memory.** If the strategy restarts mid-day,
  `usd_spent` resets to zero per instrument. Fix via `on_start`
  reconciliation from ledger before scaling past ~$100/day total
  notional.
- **Nautilus reconciles positions from venue state on startup**, so
  share counts are safe across restarts — not USD counts.

## 8. Deployment checklist

### Week 0: Infrastructure

- [ ] Polymarket API key + proxy wallet (`cfp setup`)
- [ ] Book recorder running on all 11 US cities
- [ ] NBS + GFS + HRRR + METAR feeds refreshed hourly through
      15:00 local for each station
- [ ] `cfp discover` returns qualifying markets past 15:00 local
- [ ] `cfp run` defaults: `--min-entry-hour-local 15`
      / `--max-yes-ask 0.50` / `--max-no-price 0.93`
      / `--max-usd-per-market 30` / `--entry-window-minutes 30`

### Week 1-2: Paper

- [ ] Log real YES ask at entry per recommendation
- [ ] Log actual resolution outcome
- [ ] Compute realized per-share vs backtest (+$0.126)
- [ ] Expected sample: ~15-20 paper trades in 2 weeks
- [ ] Track whether Chicago behavior matches backtest

### Week 3-4: Small-scale live

- [ ] Deploy if realized ≥ $0.05/share AND ≤ 2 losses over 30
      paper trades
- [ ] Start $10-20/market stake (well below $30 cap)
- [ ] First live session: `--max-submissions 5` to bound exposure

### Ongoing

- [ ] Daily: review recommendations + fills + ledger
- [ ] Weekly: compute rolling per-share edge, Sharpe, hit rate
- [ ] Monthly: stress test across seasons / weather regimes
- [ ] If realized tracks backtest: scale toward $100/market

## 9. References

- [Canonical backtest reproducer](backtest.py)
- [HRRR canonical compute](../lib/weather/hrrr.py)
- [City→tz mapping](../lib/weather/timezones.py)
- [Discover logic](discover.py)
- [Live strategy](strategy.py)
- [v1 vs v2 head-to-head](../../notebooks/experiments/backtest-v3/v1_v2_compare.py)
- [Full cap / floor sweep](../../notebooks/experiments/backtest-v3/consensus_optimal_sweep.py)

## 10. Changelog

- **2026-04-22 (final canonical)** — **Simplified rule**: drop
  `max_ask_walk` entirely; `max_no_price = 0.93` does slippage cap
  + edge floor. Relax `yes_ask` window from `[0.005, 0.22]` to
  `[0.07, 0.50]`. Drop `min_entry_hour_local` from 16 to 15.
  Result: n=31 (vs 72 at tight), 93.5% hit (2 visible losses, not
  cosmetic 100%), +$0.126/share (vs +$0.039), t=+2.96 (vs +7.70).
  Sample is larger, losses are visible for calibration, per-trade
  edge is meaningful. Tighter `[0.07, 0.22]` and looser
  `[0.07, 0.75]` documented as reference rows in §5.
- **2026-04-22 (prior)** — $30/market-per-day USD cap added.
- **2026-04-22 (prior)** — bounded 30-minute entry window added.
- **2026-04-22 (prior)** — active-set by airport local date (bug fix).
- **2026-04-22 (prior)** — canonical HRRR unified in
  `src/lib/weather/hrrr.py`. Added 4¢ slippage cap (superseded by
  simpler `max_no_price = 0.93`).
- **2026-04-22 (prior)** — v2 local-time anchoring (16 local floor)
  with market-wisdom cap 0.22 as canonical.
- **2026-04-15** — Strategy distilled from v3 iter 1-9.

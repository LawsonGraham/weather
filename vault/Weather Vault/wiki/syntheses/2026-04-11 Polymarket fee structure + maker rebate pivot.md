---
tags: [synthesis, polymarket, fees, edge-correction, maker-rebate, pivot]
date: 2026-04-11
related: "[[2026-04-11 Near-resolution ladder-bid arbitrage]], [[2026-04-11 Polymarket arb taker bot fingerprint]], [[Polymarket]]"
---

# Polymarket fee structure + pivot from taker-arb to maker-rebate (2026-04-11)

**Status**: **MAJOR correction to the ladder-arb synthesis and capacity
estimates.** Polymarket's weather fee of `C × 0.05 × p × (1-p)` USDC
per fill kills most of the "risk-free arb" opportunities we thought
we'd identified. The real edge is the **maker rebate** (25% of taker
fees paid to resting liquidity providers), which is ~10x more
lucrative but requires a market-making architecture.

## The fee formula

Polymarket charges a per-fill fee on weather markets:

```
fee_usdc = C × 0.05 × p × (1 - p)
```

- **C** = shares traded
- **0.05** = headline rate for weather (5% max)
- **p × (1-p)** = price-volatility multiplier (peak 0.25 at p=0.5)
- Paid by the **taker**
- 25% of fees go to makers as a daily rebate

### Effective rate by price

| price p | p(1-p) | effective rate (% of notional) |
|---------|--------|--------------------------------|
| 0.001   | 0.001  | 0.005%                         |
| 0.01    | 0.01   | 0.05%                          |
| 0.10    | 0.09   | 0.45%                          |
| 0.30    | 0.21   | 1.05%                          |
| **0.50**| **0.25**| **1.25%** (max)               |
| 0.70    | 0.21   | 1.05%                          |
| 0.90    | 0.09   | 0.45%                          |
| 0.99    | 0.01   | 0.05%                          |

**The peak fee is at p=0.5**, exactly where ladder-arb targets live.
Edge prices (tick floor, near-certainty) are essentially fee-free.

## Arb P&L correction

### Profitable arb (expI canonical)

Near-resolution april-11, 5 shares × 4 live buckets [0.89, 0.14, 0.008, 0.005]:

- Receipts: $5.215
- Fees: $0.058 (low because 0.89 is far from p=0.5)
- Max payout: $5.000
- **Net: +$0.157/cycle ✓**

### Unprofitable arb (expJ 21-share)

Pre-resolution april-11, 21 shares × [0.88, 0.12, 0.001, 0.004]:

- Receipts: $21.105
- Fees: $0.227 (symmetric 0.88/0.12 each pay $0.111)
- Max payout: $21.000
- **Net: -$0.122/cycle ✗**

### Unprofitable arb (11-bucket full sell at sum=1.011)

- Receipts: $5.055
- Fees: $0.180
- Max payout: $5.000
- **Net: -$0.125/cycle ✗**

### Not-an-arb (competitor bot expO actual trade)

Sold 10 of 11 buckets (excluded 57f at mid ≈ 0.31), sum_bid = 0.701:

- Receipts: $4.907
- Fees: $0.182
- Max payout: $7.000
- **Net: -$2.275 worst case**, but EV = **+$0.077** at P(57f) = 0.31

**The competitor wasn't running a risk-free arb** — they were running
a directional long on 57f via inverse-ladder short. Net EV-positive
only because they thought the market mispriced 57f's probability.

## Break-even analysis

For a 5-share × 11-bucket full-ladder sell:

- Break-even sum_bid = **1.036**
- Only ~5% of observed arb windows cross this threshold
- For a 21-share scale, break-even is higher yet (~1.011 minimum
  because fees scale with C)

## Revised capacity (final, fee-aware)

Applying the 5% qualification rate to expP's 15-22 windows/hour:

- Profitable arbs/hour (after fees): **2-4**
- Catchable at 500ms latency: **60%**
- Profit per profitable cycle: **$0.10-$0.30**
- Per city per day (4-6 active hours): **$0.20-$2.00**
- **8 cities: $2-$16/day gross, $1-$8/day net**

**10x reduction from the expP pre-fee estimate.** The ladder-bid arb
is a tiny mechanical edge, not a meaningful revenue stream.

## The maker-rebate edge — 10x larger

Polymarket redistributes 25% of taker fees to makers on weather
markets, paid daily in USDC.

### Per-fill rebate calculation

A taker buying 5 shares at $0.40 pays fee = 5 × 0.05 × 0.40 × 0.60 = **$0.060**.
The maker(s) on the other side earn 25% × $0.060 = **$0.015 rebate per fill**.

### Daily rebate potential

If a dominant MM has their quotes filled on ~100 taker trades per day
per active market, on 42 NYC slugs:

- **100 fills/day/slug × 42 slugs × $0.015/fill = $63/day in rebates alone**
- Plus ~$0.02/fill spread capture (tight 2c MM quotes): **$84/day** spread
- **Combined: ~$150/day NYC** for a dominant MM

**Maker rebate path: $60-150/day NYC**.
**Taker arb path: $1-8/day NYC.**

The maker path is 10-20x more lucrative. This completely flips the
strategy recommendation.

## Obstacles to making, not taking

1. **Quote management complexity**: post, cancel, re-post as book moves.
   Sub-second responsiveness required.
2. **Adverse selection**: get filled when market is about to move
   against you. The "dominant maker" in the data is probably someone
   running a weather forecasting model and adjusting quotes proactively.
3. **Capital commitment**: resting orders tie up capital. At $0.40
   × 5 shares × 2 sides × 11 buckets × 42 slugs = ~$1,850 per slug,
   thousands at scale.
4. **Competition**: the rebate is **shared proportionally** among all
   makers in a market. If a dominant MM is already earning most of
   the rebate pool, our share is their residual.
5. **Directional risk**: as a maker we're the one "sold to" on bad
   news. If we're long 60-61f and someone sells into us just before
   HRRR updates to "62°F peak", we lose.

## Execution stack requirements (updated)

For either path (taker or maker), we need:

- **py-clob-client** with batched submit (confirmed in docs)
- **API key set-up** (5 auth headers) — non-trivial setup
- **FAK orders** for the taker path, **GTC orders** for the maker path
- **Our watchman** for the taker path, **a quote-manager** for the
  maker path

The execution infrastructure is similar; the strategy code is very
different.

## Recommended pivot

1. **Deprioritize the ladder-arb taker** — it's a small-change edge
   now that fees are understood.
2. **Build a paper market-maker** targeting weather markets. Start with
   NYC (42 slugs). Post tight quotes around the favorite. Measure
   hypothetical rebate earnings + spread capture.
3. **If paper MM hits $20-50/day**, build a live version at small
   size ($100 per quote).
4. **Keep the watchman running** as a data collector — its JSONL
   stream is valuable for modeling / backtesting the MM strategy.

## What about Strategy D V1/V3?

Strategy D V1 is a DIRECTIONAL strategy (buy +2 bucket), not an arb.
The fee formula means:

- Entering at p=0.15 → fee = 0.05 × 0.15 × 0.85 = 0.64% per share
- Exit by resolution (either $0 or $1), no exit fee
- So Strategy D pays ~0.6% round-trip fees vs our 2% assumption
- **Strategy D PnL is BETTER than our backtest assumed** (fees were
  over-estimated by 3x)

Updated expected PnL: **+$3.40/trade** at Strategy D V1 (was +$3.36
with 2% fee assumption, actual 0.6% is lower).

**Strategy D V1 is still the single most reliable edge we've found.**
It outperforms both the taker-arb and the maker-rebate path on a
per-trade basis. Directional + weather-data edge > microstructure
edge.

## Action items

1. Append fee-aware P&L to [[2026-04-11 Near-resolution ladder-bid arbitrage]]
2. Mark expR "competitor = arb bot" narrative as partially wrong
3. Re-run expM Strategy D V1/V3 backtest with the correct fee formula
4. Research the "dominant maker" on weather markets — who earns the rebates?
5. Build the paper MM for a proof of concept

## Related

- [[2026-04-11 Near-resolution ladder-bid arbitrage]] — needs fee correction
- [[2026-04-11 Polymarket arb taker bot fingerprint]] — partially wrong
- [[Polymarket]] — parent entity, fee structure should be captured here

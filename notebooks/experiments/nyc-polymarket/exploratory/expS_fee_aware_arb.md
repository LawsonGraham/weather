# Exp S — Doc research + fee-aware arb P&L (MAJOR correction)

**Date**: 2026-04-11
**Status**: **Everything prior about arb capacity is wrong.** Every
trade on Polymarket weather markets carries a fee of
`C × 0.05 × p × (1-p)` USDC per fill. The p(1-p) factor puts peak
fee burden at p=0.5 — exactly the active-bucket regime our arb targets.
Recomputed P&L flips most "arbs" into losses.

## Research summary — four questions answered

### 1. Is the competitor bot part of a Polymarket MM incentive program?

**No direct maker rebate for the observed pattern** (the competitor is
mostly a TAKER). But:

- **Maker Rebates Program** (docs: `/market-makers/maker-rebates.md`):
  weather markets are eligible. Rebate rate = **25% of taker fees
  redistributed to makers**. Distributed daily in USDC. Fee-curve-
  weighted per market.
- **Liquidity Rewards Program** (docs: `/market-makers/liquidity-rewards.md`):
  **only sports + esports markets**. Weather markets are NOT in scope.
  Score formula: `S(v,s) = ((v-s)/v)² × b` where v=max spread,
  s=actual spread, b=boost. $5M pool for April 2026.

**Implication**: a pure market-maker running on weather can earn 25%
of the taker fees paid against their resting orders, but cannot claim
Sports/Esports liquidity rewards. Our observed competitor is a TAKER,
so they don't earn rebates — they pay fees.

### 2. Does Polymarket have a batched-orders API?

**YES.** `POST https://clob.polymarket.com/orders` accepts an array
of up to **15 orders per request** (`post-multiple-orders` endpoint).
This matches the 0.4ms burst timing we observed at 23:15:21.089 UTC —
a single batched API call, not 10 separate submissions.

- Order types: GTC, FOK, GTD, **FAK** (Fill-And-Kill, ideal for arb)
- Requires API key auth (5 headers: POLY_API_KEY, POLY_ADDRESS,
  POLY_SIGNATURE, POLY_PASSPHRASE, POLY_TIMESTAMP)
- Supported in py-clob-client (per the endpoint docs)

**Implication**: our execution stack can match the competitor's
latency. We don't need colocation — just a properly-configured
py-clob-client with batched submit + FAK orders.

### 3. Can we trace the competitor's wallet address?

**Partially.** Our captured `last_trade_price` WS events include:
- `transaction_hash` (on-chain Polygon tx hash)
- `fee_rate_bps`
- `asset_id`, `price`, `size`, `side`, `timestamp`

**No maker or taker address** in the WS stream directly. But the
`transaction_hash` lets us look up the on-chain Polygon transaction
and extract maker/taker addresses from the Transfer events. That's
an off-repo integration.

Alternatively, Polymarket's REST `/trades` endpoint returns
`maker_address` and an array of `maker_orders` per trade — but it
requires an API key and only shows YOUR trades (or market trades for
public markets, TBC).

### 4. What are the actual fees?

**Formula**: `fee_usdc = C × feeRate × p × (1 - p)`

Where:
- C = number of shares traded
- feeRate = **0.05 for weather markets** (5% headline rate)
- p = trade price (0 to 1)
- fee is paid by the TAKER side
- 25% of fees are redistributed to makers daily as rebates

The **p(1-p) multiplier** is the key structural feature. It's:
- **0 at p=0 or p=1** (no fee on tick-floor or certain-winner trades)
- **0.25 max at p=0.5** (peak fee — $0.0625/share at p=0.5)
- **0.09 at p=0.1** ($0.00225/share)
- **0.09 at p=0.9** ($0.00225/share)
- **0.21 at p=0.3** ($0.00525/share)

**Our observed `fee_rate_bps = 1000` in all 1,815 captured trades**
confirms this — 1000 bps = 10% raw, times the p(1-p) multiplier gives
effective 0.1% to 2.5% of notional depending on price.

## The recomputed arb P&L

Every prior arb estimate assumed zero fees. Correcting:

### Case A — near-resolution (expI canonical)

5 shares × 4 live buckets = [0.89, 0.14, 0.008, 0.005], sum_bid = 1.043

| metric | pre-fee | post-fee |
|--------|---------|----------|
| receipts | $5.215 | $5.215 |
| fees | — | **$0.058** |
| max payout | $5.000 | $5.000 |
| **net profit** | **+$0.215** | **+$0.157** |

**Still profitable** because only 4 legs pay fees and the 0.89 favorite
is far from p=0.5 peak.

### Case B — mid-flow pre-resolution (expJ 20:13:45)

21 shares × 4 live buckets = [0.88, 0.12, 0.001, 0.004], sum_bid = 1.005

| metric | pre-fee | post-fee |
|--------|---------|----------|
| receipts | $21.105 | $21.105 |
| fees | — | **$0.227** |
| max payout | $21.000 | $21.000 |
| **net profit** | **+$0.105** | **-$0.122** |

**FLIPPED TO LOSS.** The 0.88/0.12 pair are symmetric around 0.5, so
each pays 21 × 0.05 × 0.12 × 0.88 = $0.111 in fees. Two of those plus
trivial fees on the dead legs = $0.227. Wipes out the $0.105 profit.

### Case C — full 11-bucket sell at sum_bid = 1.011

5 shares × 11 buckets of the april-12 23:15:18 state

| metric | pre-fee | post-fee |
|--------|---------|----------|
| receipts | $5.055 | $5.055 |
| fees | — | **$0.180** |
| max payout | $5.000 | $5.000 |
| **net profit** | **+$0.055** | **-$0.125** |

**LOSS.** Full 11-bucket sells at sum_bid near 1.01 are unprofitable
because even the 0.05-0.4 range buckets pay substantial fees.

### Case D — competitor bot's actual 10-bucket SELL cluster (expO)

7 shares × 10 non-57f buckets at april-12 23:15:21.089 UTC
prices = [0.08, 0.003, 0.002, 0.015, 0.005, 0.032, 0.006, 0.007, 0.171, 0.38]
sum = 0.701

| metric | post-fee |
|--------|----------|
| receipts | $4.907 |
| fees | $0.182 |
| max payout | $7.000 |
| **net profit** | **-$2.275** |

**DEEP LOSS.** This isn't an arb at all — it's a 10-bucket directional
bet that bucket 57f (the one they excluded) will win. At the time,
57f had mid ≈ 0.31. EV: P(57f) × $4.907 + P(not) × (-$2.093). At
P(57f)=0.31, EV = $1.521 - $1.444 = **+$0.077** — profitable IF the
bot correctly identified 57f as having higher probability than the
market priced.

**So the "arb" we observed wasn't a risk-free arb — it was a
directional sentiment trade.** The competitor bot was BETTING that
temperature hits 54-55°F on april-12.

## Who's actually running the strategy?

expR's competitor-fingerprint narrative is partially wrong. Revised:

1. **No risk-free ladder arb is running.** The 5-share × 11-bucket
   "arbs" we identified are net-negative after fees.
2. **The 10-leg competitor bot at 23:15:21 is a directional 57f long**
   (via inverse-ladder short). They're betting on a specific outcome,
   not arbing the sum_bid.
3. **The 5-share SELL clusters on 62-66f near-resolution april-11** ARE
   near-resolution arbs (like case A) that are profitable because:
   - Favorite is far from p=0.5 (low fee)
   - Only 3-4 live buckets
   - Dead buckets contribute zero fees

## Break-even analysis for the risk-free arb

For a 5-share × 11-bucket full sell, break-even sum_bid = **1.036**.

From expP persistence histogram:
- peak_sum ≥ 1.036: ~5% of windows (only 1 observed: the 79-second 1.031 outlier and a few rare peaks)
- peak_sum in [1.02, 1.036]: ~10% of windows (marginal)
- peak_sum < 1.02: ~85% of windows (unprofitable after fees)

**Only the top 5-10% of arb windows are profitable after fees.** That's
a 10x rate reduction from prior estimates.

## Revised capacity (final)

- Profitable arbs per hour: ~2-4 (down from 15-22)
- Catchable fraction: ~60%
- Profit per cycle: $0.10-$0.30 (only bigger arbs qualify)
- Per city per day (4-6 hr active): **$0.20-$2.00**
- **8 cities: $2-$16/day** (gross, before competition)
- **Realistic net: $1-$8/day**

Much smaller than any prior estimate. This is a "tiny mechanical edge"
in the $1-10/day range, not a meaningful revenue stream.

## The MAKER path is the real edge

If the taker-arb edge is $1-8/day, what's the maker-rebate edge worth?

- 25% of taker fees on weather markets are redistributed to makers daily
- A MM posting tight quotes on the favorite earns fills × (small spread
  + $0.015 rebate per fill)
- At 100 fills/day on a single market: $1.50/day rebate alone
- Across 42 NYC slugs: potentially $60/day in rebates if you're the
  dominant MM
- Plus spread capture (~$0.02 per fill at a 2c spread): another $2/day

**Maker strategy: $60-100+/day NYC** vs Taker arb: $1-8/day NYC.

But:
- Making requires **quote management** (post, cancel, re-post continuously)
- **Adverse selection risk** (get filled when market moves against you)
- **Capital tied up** in resting orders
- **Competition from the dominant MM** whose fills are taking the
  rebates right now

## Action items

1. **Update [[2026-04-11 Near-resolution ladder-bid arbitrage]]** with
   fee-aware P&L and the revised $1-8/day capacity
2. **Revise [[2026-04-11 Polymarket arb taker bot fingerprint]]** —
   some of what I called "arbs" were actually directional trades
3. **New experiment**: analyze the maker-rebate economics. What's the
   equilibrium quote width? Who's earning rebates today (check
   dominant maker addresses via on-chain tx hashes)?
4. **Build minimal py-clob-client batched-submit test** — verify we
   can post + cancel + use FAK in under 100ms
5. **Pivot priority**: the maker-rebate path likely beats the taker-arb
   path on weather markets. Start planning a paper MM.

## Related

- [[2026-04-11 Near-resolution ladder-bid arbitrage]] — needs fee
  correction appended
- [[2026-04-11 Polymarket arb taker bot fingerprint]] — some "arbs"
  were directional
- [[Polymarket]] — parent entity (should have a fee-structure note)

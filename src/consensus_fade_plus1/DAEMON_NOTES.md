# Daemon implementation notes

## Status

### Done (this iteration)
- ✓ Weather data watchers: NBS, GFS, HRRR, METAR
- ✓ Polymarket markets catalog watcher
- ✓ Features rebuild watcher
- ✓ `cfp daemon` starts all 6 watchers concurrently
- ✓ `cfp watchers` shows last-poll status per watcher
- ✓ Persistent state across restarts (`data/processed/watchers/*.state.json`)
- ✓ Graceful shutdown on SIGINT/SIGTERM

### Not yet done — BLOCKS ON YOUR DECISIONS

#### BLOCK 1: Live order book architecture (pick ONE of 4 options)

You said: "if all three sources are in alignment ... fill all orders available
... if a new person places an order we should fill that as well ... capture the
most amount of upside possible."

That's a **continuous, reactive execution model**. We need a live L2 book kept
in memory, subscribed to the Polymarket CLOB WebSocket, and an execution loop
that reacts to book events.

Research (see research report in chat scroll-back) found **no drop-in library**.
Four realistic paths, ranked by time-to-working:

**(A) Fork `warproxxx/poly-maker`'s `poly_data/` module** (~1-2 days)
   - Real production bot, MIT licensed, 1088 stars, last push 2026-04-06
   - Has exactly the `SortedDict` L2 book + event dispatch we need
   - We'd strip the Google Sheets layer, keep the WSS + book + fill handler
   - URL: https://github.com/warproxxx/poly-maker/blob/main/poly_data/
   - **Risk**: adopting someone else's mental model; may need reworking
     to fit our resting-order strategy

**(B) Build on `py-clob-client` + raw `websockets`** (~2-3 days)
   - ~500 lines of Python for L2 book + fill handler + reconnect logic
   - Code we own end-to-end
   - We already have `scripts/polymarket_book/download.py` doing the raw
     WSS subscription — we'd just add book-state maintenance on top
   - **Cleanest path**; more upfront effort

**(C) Adopt `pmxt` framework** (~3-4 days + sidecar ops)
   - Unified API for Polymarket + Kalshi, 1544 stars, active
   - BUT requires running a Node.js sidecar server on localhost:3847
   - External dependency + multi-process complexity
   - **Only worth it if we want multi-venue later**

**(D) Adopt `papenshtross/polybot`** (~unknown)
   - Nautilus-based, explicitly labeled as "scaffold — not yet working"
   - Not viable today

**Recommendation: (B).** We already have the WSS feed. Need ~500 lines of
book state + cancel/replace logic. Zero external dependencies. But it's
your call — tell me which path when you're back and I'll build it.

#### BLOCK 2: Execution semantics under "in alignment → fill everything"

When consensus ≤ 3°F on a city-day, your thesis was "fill as much as you can
at the edge price." Concretely, three possible semantics:

**(a) Single resting limit at a fixed price.**
   - Place NO-buy at e.g. $0.85 (edge threshold)
   - Let retail hit it over time
   - Cancel at end of day if unfilled
   - **Simplest, matches backtest semantics**

**(b) Adaptive resting limit — stay on top of the book.**
   - Place NO-buy at `best_ask - 1 tick` (= best YES-bid + 1 tick on YES side)
   - When someone else outbids us, re-quote one tick better
   - When retail lifts our ask, replace at new top
   - **Higher fill rate, more fee rebate, more cancel-replace traffic**

**(c) Aggressive taker — hit new YES-bids instantly.**
   - Watch for new YES-bids at price ≥ $0.15 (= NO-ask ≤ $0.85)
   - Fire a market NO-buy immediately (FAK order)
   - Pay taker fee (1.25% peak, cuts our $0.083/trade edge to ~$0.067)
   - Guaranteed fill; no rebate
   - **Fastest fills, lowest edge per trade**

**(d) Hybrid (a+b+c):** rest a bulk limit, add aggressive top-of-book
    quote, fire taker at very good prices only.

My read of your message: **you want (b) — stay on top of the book at the
edge price, adapt as flow comes in.** Confirm when back.

#### BLOCK 3: Per-market position caps

You said "while they are in our range of profitability" — implies we keep
adding shares until we hit some cap. What should cap shares per (city, market_date)?

Options:
- Absolute dollar cap (e.g. $100 per market)
- Absolute share cap (e.g. 500 shares per market)
- Daily total cap across all markets (e.g. $500/day)
- Fraction-of-observed-depth cap (e.g. 25% of depth within 2¢)

**Recommendation: $100 per market, $500/day portfolio cap, with
25%-of-depth secondary guardrail.** Matches the capacity analysis we did
earlier. Confirm or overrule.

#### BLOCK 4: "Acceptable range" threshold

To decide if a fresh order is still in our edge zone, we need a threshold.
If NO ask moves from $0.85 to $0.92, is that still a buy?

My suggestion: **edge threshold = 5¢**. We buy at any NO ask where
`1 - implied_fair - slippage > 0.05`, where `implied_fair = 0.97`. That
means we buy up to a NO ask of `0.97 - 0.05 = $0.92`. Past that, edge is
too thin to justify tail risk.

Confirm when back.

## What to do WHEN YOU GET BACK

1. Read this file
2. Answer blocks 1-4 above (just "A/B/C" style is fine)
3. I'll build the live book + execution loop based on your answers

---
tags: [spec, execution, backtesting, polymarket, kalshi, sources]
date: 2026-04-10
related: "[[Project Scope]]"
---

# Execution Stack — Source Review & Build Plan

> Review of three open-source prediction-market repos and how they map to our weather-markets strategy. Source deep-dive happened 2026-04-10; this note is the actionable spec that comes out of it.
>
> **Strategy recap** (see [[Project Scope]] for full context): we are trading **daily weather contracts on Kalshi and Polymarket** for major US airports. Core alpha is **calibration, not point-forecast accuracy** — the output of our model is `P(threshold crossed)`, and we want to be better-calibrated than the market. None of the three repos reviewed here solve weather data ingest or the forecasting model; all three help with the trading-stack half: market data, backtesting, execution, post-trade analytics.

---

## TL;DR

Three repos, three different jobs. Use all three, but **don't commit to any one of them wholesale before validating the core thesis with repo 1's dataset**.

| Repo | One-line identity | Weight in our stack |
| --- | --- | --- |
| **`jon-becker/prediction-market-analysis`** | 36 GiB historical dataset of Kalshi + Polymarket markets & trades, plus microstructure analysis scripts | **Highest immediate value** — the dataset kills or confirms the thesis before we build anything else |
| **`evan-kolberg/prediction-market-backtesting`** | Serious NautilusTrader-based backtester with custom adapters for Kalshi + Polymarket | **Reference + late-stage** — steal the fee models & fill model now, adopt the full stack only if edge proves real |
| **`agent-next/polymarket-paper-trader`** | Lightweight Python + SQLite live paper trader; walks the real Polymarket book; MCP-server wrapped | **Spine of our execution layer** — fork `pm_trader/` and build on it |

**The plan, short form:**
1. Download repo 1's dataset → filter to weather → run the calibration/mispricing analyses → **go/no-go decision on the whole project**.
2. Fork repo 3's `pm_trader/` as the execution spine; add a Kalshi client and a `forecasts` table.
3. Backtest initially with repo 3's simple engine (same code path as live); upgrade to repo 2 only when we need higher fidelity, portfolio-level runs, or an optimizer.
4. Post-trade analytics: reuse repo 1's calibration + mispricing scripts on our own forecast/market history.

---

## Repo 1 — `jon-becker/prediction-market-analysis`

**URL:** https://github.com/jon-becker/prediction-market-analysis
**Language:** Python (98%)
**License:** (standard OSS; verify before forking)

### What it actually is

A **research data platform**, not a trading system. The headline asset is a **~36 GiB pre-collected Parquet dataset** of Kalshi + Polymarket market metadata and trade history, hosted on Cloudflare R2 and fetched via `make setup`. On top of that it ships indexers (to refresh the data) and ~20 analysis scripts that study microstructure.

### Structure (verified from the repo tree)

- `src/indexers/kalshi/` — REST client with `markets.py`, `trades.py`, `models.py`.
- `src/indexers/polymarket/` — REST **plus on-chain** via FPMM events: `blockchain.py`, `blocks.py`, `fpmm_trades.py`, `markets.py`, `trades.py`, `client.py`. The on-chain path is non-trivial to reimplement — worth preserving.
- `src/common/indexer.py` — abstract `Indexer` base class with dynamic subclass discovery, checkpointed `run()`. Clean pattern, reusable for our weather ingesters.
- `src/common/storage.py`, `src/common/client.py` — shared HTTP + Parquet helpers.
- `src/analysis/kalshi/` — ~20 scripts. The on-strategy ones:
    - `kalshi_calibration_deviation_over_time.py`
    - `mispricing_by_price.py`
    - `win_rate_by_price.py`, `yes_vs_no_by_price.py`, `ev_yes_vs_no.py`
    - `vwap_by_hour.py`, `returns_by_hour.py` (intraday regimes — relevant since HRRR updates hourly)
    - `maker_vs_taker_returns.py`, `maker_taker_gap_over_time.py` (who is on the right side of the spread)
    - `longshot_volume_share_over_time.py`, `market_types.py`
- `src/analysis/polymarket/` — much thinner: volume, trades over time, win rate by price.
- `docs/SCHEMAS.md` — canonical market/trade schema (template for our own).
- `main.py` + `Makefile` — `make setup | index | analyze | package`.

### Why it matters for us

1. **The dataset is free historical data we'd otherwise spend weeks scraping.** Filter 36 GiB for weather slugs (`highest-temperature`, `rainfall`, `snow`, airport strings) and we instantly have multi-year tick-level weather market histories for backtesting.
2. **The calibration analyses directly test our core thesis.** We assume weather markets are miscalibrated. `kalshi_calibration_deviation_over_time.py` and `mispricing_by_price.py` are exactly the lens to test that, rerun on the weather subset only. If the answer is "these markets look calibrated," that's a project-killer and we should know before building anything.
3. **The Polymarket on-chain FPMM indexer is worth preserving** for when Polymarket's REST history is incomplete.
4. **The `Indexer` base class** (checkpointed, auto-discovered) is a clean template to copy for HRRR / ASOS / METAR / Synoptic ingesters.

### What it does NOT have

No trading, no backtesting, no fill modeling, no live pricing. Don't look here for execution.

---

## Repo 2 — `evan-kolberg/prediction-market-backtesting` (branch: `v2`)

**URL:** https://github.com/evan-kolberg/prediction-market-backtesting
**Language:** Python 3.12+, Rust (via NautilusTrader)
**License:** mixed — **LGPL-3.0-or-later** on NautilusTrader-derived files (including `fill_model.py` and `strategies/core.py`). MIT on other files. Verify before any proprietary fork.

### What it actually is

A **production-grade backtesting stack** for Kalshi + Polymarket strategies, built on **NautilusTrader** (Rust-cored event-driven institutional backtester). By far the largest and most sophisticated repo of the three — ~80+ Python files, custom adapters, an optimizer, a data relay service, tearsheets with Brier scores.

### Structure (verified)

- `prediction_market_extensions/adapters/kalshi/` — full adapter: `config.py`, `data.py`, `factories.py`, **`fee_model.py`**, `loaders.py`, `market_selection.py`, `providers.py`, `research.py`.
- `prediction_market_extensions/adapters/polymarket/` — `execution.py`, **`fee_model.py`**, `gamma_markets.py`, `loaders.py`, `market_selection.py`, `parsing.py`, `pmxt.py`, `research.py`.
- `prediction_market_extensions/adapters/prediction_market/` — shared layer:
    - **`fill_model.py`** — `PredictionMarketTakerFillModel`. Applies a deterministic **one-tick adverse move** for non-limit orders, clamped to `[0, 1]`; limit orders use NT's exchange matching. For Kalshi the tick is $0.01; for Polymarket it uses the instrument's `price_increment`. This is the right slippage model when you only have trade/quote ticks (no L2 depth) — exactly our situation for weather markets.
    - `backtest_utils.py`, `replay.py`, `research.py`.
- `prediction_market_extensions/backtesting/` — runtime + experiment machinery: `_backtest_runtime.py`, `_experiments.py`, `_independent_multi_replay_runner.py`, `_isolated_replay_runner.py`, `_notebook_runner.py`, `_optimizer.py`, `_prediction_market_backtest.py`, `_prediction_market_runner.py`, `_replay_specs.py`, `_result_policies.py`, `_strategy_configs.py`, `_timing_harness.py`.
- `prediction_market_extensions/backtesting/data_sources/` — pluggable loaders: `pmxt.py` (public dataset), `kalshi_native.py`, `polymarket_native.py`, `registry.py`, `replay_adapters.py`, `vendors.py`.
- `prediction_market_extensions/analysis/` — `tearsheet.py`, `legacy_backtesting/plotting.py`, `legacy_plot_adapter.py`. Metrics include equity curve, drawdown, Sharpe, monthly returns, **cumulative Brier advantage**.
- `strategies/` — 10 reference strategies: `breakout`, `deep_value`, `ema_crossover`, `final_period_momentum`, `late_favorite_limit_hold`, `mean_reversion`, `panic_fade`, `rsi_reversion`, `threshold_momentum`, `vwap_reversion`.
    - **`strategies/core.py`** — the affordability helpers: `_estimate_entry_unit_cost`, `_cap_entry_size_to_free_balance` (with a 0.97 cash buffer), `_cap_entry_size_to_visible_liquidity`, and the "worst-case clear up to 1.0 when visible depth is absent" logic. Subtle and correct — copy the reasoning pattern directly.
- `backtests/` — runner scripts composing strategies with data sources: independent-multi-replay, joint-portfolio, 25-replay, EMA optimizer.
- `pmxt_relay/` — a FastAPI + systemd service that mirrors PMXT data in real time for live-like replay. Only useful if we commit to PMXT as our data source.
- `docs/execution-modeling.md`, `docs/pmxt-byod.md`, `docs/backtests.md` — the real documentation.

### Why it matters for us

1. **The fee models are the single most valuable thing to steal.** Both venues, normalized, with correct tick handling. If you roll your own backtest and your fees are wrong, the backtest lies. **Reference these even if we don't adopt NT.**
2. **`PredictionMarketTakerFillModel` is the correct slippage model for thin weather markets.** We won't have L2 depth; one-tick adverse move is honest.
3. **`strategies/core.py` affordability logic** solves a real problem nobody thinks about until they've been burned: how much YES can you afford when the book might clear anywhere up to $1.00 on a thin market?
4. **Multi-market / joint-portfolio runners** — we want to trade LAX + JFK + ORD + DFW simultaneously and need portfolio-level backtests. This is already solved here.
5. **`_optimizer.py`** — parameter sweeps over edge-threshold, Kelly fraction, regime filters.
6. **Tearsheet includes Brier score** — essential for a probability-forecasting strategy.

### Tradeoffs

- **NautilusTrader is a commitment.** Strategies are NT `Strategy` subclasses with a message-driven lifecycle. You can't casually dip in.
- **LGPL-3.0 on NT-derived files.** Fine for internal research; a real consideration if this ever becomes a proprietary product.
- **PMXT relay is only useful if we commit to PMXT.** We probably won't — repo 1's dataset + our own ingesters cover it.

---

## Repo 3 — `agent-next/polymarket-paper-trader`

**URL:** https://github.com/agent-next/polymarket-paper-trader
**Language:** Python 3.10+
**License:** (standard OSS; verify before forking)

### What it actually is

A **lightweight, Python-only, SQLite-backed live paper-trading engine** for Polymarket. Walks the **real** live order book level-by-level. ~15 source files. Also ships an **MCP server** so Claude agents can call it as a tool, and a backtest mode that reuses the same fill machinery.

### Structure (verified)

- `pm_trader/api.py` — Polymarket CLOB client: `get_market`, `get_order_book`, `get_fee_rate`, `get_midpoint`, `search_markets`.
- **`pm_trader/orderbook.py`** — `simulate_buy_fill` / `simulate_sell_fill` walk the ASK/BID side level-by-level. `calculate_fee` implements the **exact Polymarket fee formula**: `(fee_rate_bps / 10_000) * min(price, 1 - price) * size`, with a `0.0001` minimum enforced when `fee_rate_bps > 0`. Supports FOK and FAK; has a `max_price` limit guard.
- **`pm_trader/engine.py`** — single `Engine` facade wiring `api` + `db` + `orderbook` + `orders`. `buy()` validates outcome, fetches live book + fee rate, simulates fill, checks cash, updates position atomically. Typed error hierarchy (`InsufficientBalanceError`, `MarketClosedError`, `OrderRejectedError`, `NoPositionError`, etc.).
- `pm_trader/orders.py` — limit order state machine: create / cancel / expire / should_fill / mark_filled, GTC/GTD lifecycle.
- **`pm_trader/backtest.py`** — loads CSV/JSON `(timestamp, slug, outcome, midpoint)` snapshots, builds **synthetic 3-level order books** around each midpoint (configurable spread + depth), monkey-patches `engine.api.get_midpoint`, runs strategies through the **same `Engine` code path as live**. Output: Sharpe, win rate, max drawdown, ROI, PnL.
- `pm_trader/db.py` — SQLite schema (accounts, trades, positions).
- `pm_trader/analytics.py`, `card.py`, `export.py`, `benchmark.py`.
- **`pm_trader/mcp_server.py`** — exposes the engine as MCP tools (`backtest`, `buy`, `sell`, market search). Claude-in-the-loop trading is essentially free here.
- `pm_trader/cli.py` — `markets list --sort liquidity`, `markets search`, `watch SLUG`, `book SLUG --depth N`.
- `examples/momentum.py`, `mean_reversion.py`, `limit_grid.py` — strategies are plain functions: `def run(engine: Engine) -> None`.
- Full test suite including `tests/test_e2e_live.py`.

### Why it matters for us

1. **Easiest piece to adopt.** Fork the `pm_trader/` package, rename, done. ~15 files, zero Rust, zero NautilusTrader, plain SQLite.
2. **`orderbook.py` is gold.** Exact fee formula, level-by-level walking, FOK/FAK — 100% reusable.
3. **Live and backtest share the same `Engine`.** Zero sim-to-real gap. Matches our closed-loop iteration goal directly — debug one engine, not two.
4. **SQLite schema is trivial to extend.** Add a `forecasts(run_ts, market_id, threshold, p_model, p_market, edge_bps, source)` table and we have forecast → decision → fill → outcome → post-mortem in one DB.
5. **MCP server is unusually relevant.** Our project already treats Claude-in-the-loop as first-class (per [[Project Scope]] and the Research Chats); wiring our weather model into a Claude agent that can call `buy`/`sell`/`book` tools is nearly free here.

### Tradeoffs

- **Polymarket-only.** No Kalshi. Per [[Project Scope]], Kalshi looks like the deeper weather venue (LA daily: "Kalshi at 160k for today"). We need to write our own Kalshi live client — borrow repo 2's `adapters/kalshi/fee_model.py` and `loaders.py` as the reference.
- **Backtest fidelity is lower than repo 2.** Synthetic 3-level books from midpoint + fixed spread + fixed depth. For thin weather markets this will **overestimate fillability**. Repo 2's one-tick-adverse model is more honest when we only have trade data.
- **No portfolio-level backtesting.** One account, one loop. Fine for phase 1; we'd hit it at phase 5 (scale to multiple airports).

---

## Closed-Loop Architecture — Target State

```
  [ WEATHER DATA INGEST ]                        <- we build (Phase 1)
  HRRR via Herbie | ASOS 1-min | METAR           Indexer base class
  Synoptic | NEXRAD | TAF                        pattern borrowed from Repo 1
           │
           ▼
  [ FORECASTING MODEL ]                          <- we build (Phase 1)
  XGBoost on HRRR biases                         Output: calibrated
  HRRRx ensemble for P(threshold)                P(high > T) per airport/day
           │
           ▼
  forecasts.parquet  ──────────────────┐
                                       │
           ┌───────────────────────────┤
           ▼                           ▼
  [ BACKTEST ]                 [ LIVE PAPER TRADE ]
  Phase A: Repo 3 backtest.py  Fork Repo 3 pm_trader/
  + Repo 1 weather subset        - Engine + orderbook + db
  (synthetic books, fast)        - Add Kalshi client (port
                                   Repo 2 adapters/kalshi)
  Phase B: Repo 2 NT runners     - Add `forecasts` table
  (optimizer, Brier, portfolio)  - Strategy = forecast vs
                                   live midpoint → edge → buy
           │                           │
           └─────────────┬─────────────┘
                         ▼
  [ POST-TRADE ANALYTICS ]                      <- Repo 1 scripts, rerun on our data
  kalshi_calibration_deviation_over_time.py      Brier score (Repo 2 tearsheet)
  mispricing_by_price.py                         Daily calibration check
  win_rate_by_price.py, returns_by_hour.py       vs HRRR run timing
```

---

## Prioritized Action Plan

### Phase 0 — Thesis validation (DO THIS FIRST)

The rest of this plan is wasted effort if weather markets are already well-calibrated. Validate before building.

- [ ] Clone `jon-becker/prediction-market-analysis`, run `make setup` to download the 36 GiB Parquet dataset.
- [ ] Write a filter pass that extracts weather markets only (title/slug contains `highest-temperature`, `rainfall`, `snow`, `temperature`, city names of target airports). Store as a weather-subset Parquet.
- [ ] Run `kalshi_calibration_deviation_over_time.py` on the weather subset. **Are these markets systematically miscalibrated?**
- [ ] Run `mispricing_by_price.py` on the weather subset. **Where on the 0–1 probability curve is the mispricing concentrated?** (informs sizing and market-selection logic)
- [ ] Run `returns_by_hour.py` on the weather subset. **Are there intraday regimes?** (if yes, likely correlated with HRRR run times — key for the "react faster than market" edge from [[Project Scope]])
- [ ] Write a one-page go/no-go summary with the calibration result. If edge is visible: proceed. If not: pivot or kill.

### Phase 1 — Execution spine

- [ ] Fork `agent-next/polymarket-paper-trader` `pm_trader/` package into this repo under `src/execution/` (or rename to `weather_trader/`).
- [ ] Verify the license permits our intended use.
- [ ] Extend `db.py` schema with a `forecasts` table: `(run_ts, airport, contract_date, threshold, p_model, p_market_at_decision, edge_bps, model_version, source)`.
- [ ] Extend `db.py` schema with a `resolutions` table for ground-truth outcomes vs forecasted probabilities (needed for Brier score + calibration checks).
- [ ] Port the Kalshi client: base on `evan-kolberg/prediction-market-backtesting` adapters — `adapters/kalshi/fee_model.py` (copy wholesale), `adapters/kalshi/data.py`, `adapters/kalshi/loaders.py`, `adapters/kalshi/market_selection.py`. Wrap in a `KalshiClient` that mirrors `pm_trader/api.py`'s `PolymarketClient` interface.
- [ ] Add Polymarket fee-model cross-check against repo 2's `adapters/polymarket/fee_model.py` to make sure repo 3's formula matches (it should — `(bps/10_000) * min(p, 1-p) * size`).
- [ ] Write a minimal `WeatherStrategy(engine)` function: read latest `forecasts`, look up live midpoints, compute edge in bps, size with fractional Kelly, submit via `engine.buy`.

### Phase 2 — Backtest loop (fast iteration)

- [ ] Write an adapter from repo 1's Parquet schema to repo 3's `PriceSnapshot(timestamp, slug, outcome, midpoint)` format.
- [ ] Run the `WeatherStrategy` against historical weather-market snapshots from repo 1's dataset, using repo 3's `backtest.py`.
- [ ] Record metrics: Sharpe, drawdown, ROI, win rate, **Brier score vs realized outcomes**.
- [ ] Establish a baseline: "trade when model disagrees with market by ≥ X bps" — sweep X.
- [ ] Document the sim-to-real gap (synthetic books are optimistic).

### Phase 3 — Weather data + model

Covered in [[Project Scope]] — HRRR via Herbie, ASOS 1-min, METAR, etc. Runs in parallel with Phase 1/2 (different sub-stream of work; parallelize per standing preference).

- [ ] Build the HRRR ingester using repo 1's `Indexer` base class pattern (checkpointed, dynamic discovery).
- [ ] XGBoost bias-correction model, producing calibrated `P(high > T)` for market thresholds.
- [ ] Write forecasts into the `forecasts` table on each HRRR run (hourly, ~15 min after cycle start).

### Phase 4 — Live paper trade

- [ ] Wire the real execution spine to live Polymarket + Kalshi CLOB data.
- [ ] Deploy the loop: new HRRR run → model inference → write forecasts → strategy pass → simulated orders via real books.
- [ ] Dashboard: live P&L, open positions, edge over market, Brier score tracking.

### Phase 5 — Higher-fidelity backtest (optional, if Phase 2 edge looks real)

- [ ] Import repo 2's `PredictionMarketTakerFillModel` and re-run the backtests under one-tick-adverse fills. Compare Sharpe/PnL delta to the synthetic-book version. **That delta is the sim-to-real gap estimate.**
- [ ] If portfolio-level metrics become load-bearing (multi-airport correlated positions): graduate strategies to NautilusTrader `Strategy` subclasses and use repo 2's `_independent_multi_replay_runner.py` / joint-portfolio runners.
- [ ] Use `_optimizer.py` for edge-threshold and sizing parameter sweeps.

### Phase 6 — Post-trade analytics (ongoing)

- [ ] Daily job: rerun repo 1's `kalshi_calibration_deviation_over_time.py` logic against our forecasts vs realized outcomes.
- [ ] `mispricing_by_price.py` equivalent on our own fills — are we winning at specific probability buckets?
- [ ] Regime breakdowns: per-airport, per-threshold, per-HRRR-cycle-age, per-weather-regime (marine layer, cold front, convective).

---

## What to Steal Immediately (regardless of phase)

Three files to pull into our repo as reference even before we start building, so our own code has something correct to check against:

1. **`evan-kolberg/prediction-market-backtesting` / `prediction_market_extensions/adapters/kalshi/fee_model.py`** — canonical Kalshi fee formula.
2. **`evan-kolberg/prediction-market-backtesting` / `prediction_market_extensions/adapters/polymarket/fee_model.py`** — canonical Polymarket fee formula (cross-check vs repo 3).
3. **`evan-kolberg/prediction-market-backtesting` / `prediction_market_extensions/adapters/prediction_market/fill_model.py`** — `PredictionMarketTakerFillModel` for honest slippage on thin books.

Plus read (don't necessarily copy) **`strategies/core.py`** from repo 2 — the affordability / `[0,1]` clamping logic is subtle and non-obvious.

---

## Open Questions / Risks

- **Primary venue is Kalshi but repo 3 (the easy fork) is Polymarket-only.** How much work is a Kalshi live client? Probably 1–2 days using repo 2's adapter as reference, but it's unvalidated.
- **Repo 2 licensing (LGPL-3.0 on NT-derived files)** needs a real legal read if this becomes proprietary. For now, internal research use should be fine.
- **Thin-book fill realism.** Repo 3's synthetic 3-level books will make our backtest look better than reality. Phase 5's one-tick-adverse rerun is the reality check — plan for a meaningful PnL haircut.
- **Data freshness.** Repo 1's 36 GiB dataset is a snapshot. For validation it's perfect. For live trading we need fresh market data — repo 3's `api.py` handles Polymarket live; Kalshi live is unsolved.
- **Weather market liquidity varies wildly by airport and contract type.** [[Project Scope]] notes LA daily at ~160k on Kalshi, Shanghai at 150k on Polymarket; airports outside top tier may be untradeable at scale. Need a market-selection filter in Phase 1.
- **HRRR cycle timing.** Per [[Project Scope]], alpha comes in the **~15–45 min window** after a new HRRR run before the market reprices. Our live loop needs to be running against real market data at sub-minute latency to capture that — important for infrastructure planning.
- **TAF benchmark still matters.** Per [[Project Scope]], we need to beat TAF skill, not just the market consensus. Post-trade analytics should include a TAF comparison column.

---

## Licensing summary

| Repo | License (headline) | Implication |
| --- | --- | --- |
| jon-becker/prediction-market-analysis | Needs verification before forking indexers; dataset is separate | Verify dataset terms too |
| evan-kolberg/prediction-market-backtesting | **LGPL-3.0-or-later on NT-derived files** (incl. `fill_model.py`, `strategies/core.py`), MIT on other files, mixed NOTICE file | Safe for internal research; legal review required if commercial |
| agent-next/polymarket-paper-trader | Standard OSS (verify before fork) | Verify before bulk fork |

**Action:** before Phase 1, do a 30-minute licensing pass across all three and document what's safe to fork wholesale vs copy-with-attribution vs reference-only.

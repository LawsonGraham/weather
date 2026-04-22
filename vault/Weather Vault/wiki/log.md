# Wiki Log

> Chronological append-only log of wiki activity. Ingests, queries, lint runs. Used by Claude to understand what's been done recently.
>
> Format: every entry starts with `## [YYYY-MM-DD] <op> | <title>` so it's grep-able:
>
> ```
> grep "^## \[" log.md | tail -5
> ```

---

## [2026-04-10] bootstrap | wiki scaffolding created

- Scaffolded `wiki/` and `raw-sources/` directories per Karpathy LLM Wiki pattern
- `Project Scope.md` already in place at vault root
- `Research Chats/` moved under `raw-sources/chats/`
- `.claude/` skills and agents configured (pipeline, vault-*, weather-data, model-training)
- Awaiting first ingest

## [2026-04-11] capture | IEM

- Page: wiki/entities/IEM.md
- Trigger: backfill for `scripts/iem_asos_1min/` (new data source, no entity page yet)
- Related: [[ASOS 1-minute]], [[KNYC]], [[KLGA]], [[Project Scope]]

## [2026-04-11] capture | ASOS 1-minute

- Page: wiki/concepts/ASOS 1-minute.md
- Trigger: backfill for `scripts/iem_asos_1min/` (new data source, no concept page yet)
- Related: [[IEM]], [[KNYC]], [[KLGA]], [[Project Scope]]

## [2026-04-11] capture | KNYC

- Page: wiki/entities/KNYC.md
- Trigger: backfill — Central Park is the [[Kalshi]] resolution station; surprisingly has 1-minute data in the IEM network
- Related: [[IEM]], [[Kalshi]], [[ASOS 1-minute]], [[KLGA]]

## [2026-04-11] capture | KLGA

- Page: wiki/entities/KLGA.md
- Trigger: backfill — LaGuardia is the [[Polymarket]] resolution station and is pulled by `scripts/iem_asos_1min/`
- Related: [[IEM]], [[Polymarket]], [[ASOS 1-minute]], [[KNYC]]

## [2026-04-11] capture | Polymarket

- Page: wiki/entities/Polymarket.md
- Trigger: backfill for `scripts/polymarket_weather/` (new data source family, no entity page yet)
- Related: [[Kalshi]], [[KLGA]], [[Polymarket weather market catalog]], [[2026-04-11 Polymarket schema corrections]]

## [2026-04-11] capture | Kalshi

- Page: wiki/entities/Kalshi.md
- Trigger: backfill — Kalshi is the other target venue for NYC weather markets (no downloader yet; resolution station difference from [[Polymarket]] is critical context)
- Related: [[Polymarket]], [[KNYC]], [[Project Scope]], [[Execution Stack — Source Review]]

## [2026-04-11] capture | Polymarket weather market catalog

- Page: wiki/concepts/Polymarket weather market catalog.md
- Trigger: backfill for `scripts/polymarket_weather_slugs/` (new data source, no concept page yet)
- Related: [[Polymarket]], [[2026-04-11 Polymarket schema corrections]]

## [2026-04-11] capture | METAR concept page added

- Created `wiki/concepts/METAR.md` — Layer 3 data-source documentation
- Covers: IEM source, Phase 1 download sizing, pipeline file layout, full 46-column schema (30 IEM-decoded + 16 RMK-decoded), RMK-group decoding examples (SLP/T/1/2/4/5/6/7/PRESRR/PRESFR/TSB/TSE), relationship to Layers 1 / 2 / 6, schema quirks hit (`#DEBUG:` preamble, SPECI RMK omissions, `press_tendency` not a python-metar attribute)
- Market-relevance shortcuts section: maps each contract shape (daily high/low, rain-today, frontal timing, convective onset, threshold temperature) to the specific column that answers it
- Index updated under Concepts section
- Produced in conjunction with `scripts/iem_metar/` Phase 1 pipeline landing (commits 56eed97 + 4355def on `wt/iem-metar-layer3`)

## [2026-04-11] methodology | Data Validation skill + concept page + historical bug record

- Codified the paranoid first-principles audit methodology as `.claude/skills/data-validation/SKILL.md` (reusable procedure) + `wiki/concepts/Data Validation.md` (project memory / rationale / bug record)
- **Six-level rigor ladder**: (1) manifest & disk, (2) row + column fidelity, (3) value-level fidelity, (4) schema invariants & cross-column consistency, (5) fresh upstream re-fetch, (6) invariant stress tests. Levels 1–4 live in every source's `validate.py`; level 5 runs before a source is called done; level 6 graduates into validate.py on first real bug
- Historical bug record covers the real findings from the iem_metar + iem_asos_1min Phase 1 audits: ice-accretion trace sentinels, temp_c naming, slp_mb_rmk redundancy, and the IEM half-open-interval boundary bug that levels 1–4 missed entirely
- Index updated under Concepts

## [2026-04-11] audit | METAR fidelity audit + v3 transform fixes

- Ran exhaustive raw-CSV ↔ parquet fidelity audit after the Phase 1 land
- **Found 3 real issues:**
  1. **Silent data loss in `ice_accretion_{1,3,6}hr` columns.** The trace sentinel `T` was handled for `p01i` but not for the ice columns — 6 rows of trace icing during January 2026 freezing rain events were lost. Fix: v3 transform broadens trace-handling to a `TRACE_COLS` list covering all 4 trace-eligible columns. Confirmed via sweep: no other columns use the sentinel.
  2. **`temp_c_rmk` / `dewpt_c_rmk` were mislabeled.** python-metar's `m.temp.value('C')` returns the RMK T-group 0.1°C when present, but falls back to the main-body integer °C field (`TT/TD`) on SPECIs without a T-group. 113 rows were showing integer °C from the fallback; the `_rmk` suffix implied strict RMK provenance. Renamed to `temp_c` / `dewpt_c` and documented the fallback behavior. Verified `temp_c` matches `tmpf` within 1°F rounding across all 6198 rows where both are non-null.
  3. **`slp_mb_rmk` is 100% redundant with IEM's `mslp`.** 0 value disagreements, identical null coverage across 5381 rows. Kept as a cross-check column rather than dropped; documented the guaranteed equality.
- **Caught by the audit but not bugs:** SPECI detection (IEM strips the `SPECI`/`METAR` type marker from the raw string, so SPECIs must be detected by non-standard minute ≠ `:51`. Phase 1 window has 5383 routine + 817 SPECI = 13.2% SPECI rate, healthy for a winter-to-spring period).
- Extended `validate.py` with 7 new fidelity checks: raw CSV ↔ parquet row + column fidelity, null-count parity (detect lost values to cast failure), timestamp parse coverage, report-type mix, inter-observation gap distribution, temp_c ↔ tmpf consistency, slp_mb_rmk ↔ mslp redundancy. All new checks pass against the v3 output.
- Vault page updated with corrected column semantics, SPECI detection quirk, trace sentinel history, and `_rmk` naming rationale

## [2026-04-11] capture | Strategy D deployment refinements (second synthesis of the day on this thesis)

- Page: wiki/syntheses/2026-04-11 Strategy D deployment refinements.md
- Follow-up to the iter-10 discovery synthesis [[2026-04-11 NYC Polymarket upward-bias Strategy D]]; captures the exp16–exp36 deployment-readiness chain + the live recommender pipeline
- **Headline**: Strategy D is deployable today. Real-ask cost model roughly doubles backtest PnL (mid == ask, median spread 0.000). Optimal entry hour is 16 EDT primary / 18 EDT bonus (not 12 EDT); 08–10 EDT LOSES. Market is human-driven (peak volume 14–15 EDT, not HRRR windows) → edge persistence is months. Universal upward bias decaying ~34% across sample (deploy now). Retail flow is 100% bullish both buckets (we slip in alongside, not fade). Flat favorites never win but aren't an actionable entry filter. Winner is priced ≤38¢ all day (market is probability-spreading, not winner-picking). Multi-bucket basket dilutes edge from +94% → +1% (don't basket). Lag-1 autocorrelation 0.04 (no carryover filter).
- **Live test @ 13:40 EDT on 2026-04-11**: `live_now.py` caught intraday repricing in real time — favorite shifted from 62–63°F @ 0.39 this morning to 60–61°F @ 0.495 now; new +2 target is 62–63°F @ 0.305 with 3¢ spread (wider than historical 0¢ median). Direct live confirmation of the exp32 intraday rebalancing mechanism.
- **Today's trade recommendation**: buy 612.75 YES on `highest-temperature-in-nyc-on-april-11-2026-62-63f` at limit ≤ $0.3264, stake $200 (2% Kelly on $10k), profit +$412.75 / loss −$200.
- **Deployable artifacts** (on `wt/nyc-polymarket-backtest` branch): `notebooks/experiments/nyc-polymarket/exp01–36*.py`, `scripts/polymarket_weather/{live_recommender.py, live_now.py, paper_ledger.py}`
- **Deployment blockers**: HRRR backfill (~96% complete, blocks exp30 HRRR-conditional analysis + Phase 2); 14 days paper-trade validation before real capital
- Entities touched: [[Polymarket]], [[KLGA]]
- Concepts touched: [[METAR]], [[ASOS 1-minute]], [[Polymarket weather market catalog]]
- Contradictions flagged: entry-hour finding (16–18 EDT optimal) supersedes the 12 EDT anchor from the iter-10 synthesis — not a data contradiction, a refinement from extended hour sweep. Cost model correction (mid == ask) supersedes the placeholder cost assumption in the iter-10 chain and roughly doubles all previously reported Strategy D PnL headlines.
- Index updated under Syntheses

## [2026-04-14] backtest | Strategy D retracted in clean IS/OOS holdout

- Page: wiki/syntheses/2026-04-14 Strategy D does NOT replicate in clean temporal holdout.md
- Pre-registered 2/3-1/3 temporal split: IS Mar 11-31 (126 usable MDs), OOS Apr 1-10 (102 usable MDs), 11 US cities, entry 20:00 UTC, 1 share/trade, fee `C × 0.05 × p × (1-p)`
- **Result**: NONE of 9 pre-registered strategies (S0-S4) survive OOS. Key: S1 +2°F offset (Strategy D V1 equivalent) = -$0.028/trade IS → -$0.086/trade OOS. Exploratory market-fav−1 strategy had IS t=+2.23 → OOS t=+0.02 (100% edge decay).
- NBS forecast bias per-city in IS: 10 of 11 cities show NBS over-forecasts (opposite of prior "warm bias" thesis). Chicago the only underforecast (+1.5°F).
- Depth data from book recorder starts 2026-04-13 — post-OOS window. Indicative capacity at fav prices ~$500-1000/trade; thinner at tail buckets. No direct measurement for backtest period.
- Actionable: **stop paper-trading Strategy D** (prior deployment claim invalid); re-focus on maker-rebate + fee-structure microstructure edges; retrain daily_max_model without leakage
- Wiki index updated: Strategy D retraction pinned at top of Syntheses
- Branch: `wt/backtest-v2`; code + data at `notebooks/experiments/backtest-v2/` + `data/processed/backtest_v2/`
- Contradictions: supersedes all claims in [[2026-04-11 Strategy D deployment refinements]] for Mar 11+ sample; the geographic OOS in [[out_of_sample_validation.md]] tested portability, not persistence — leaves the period-decay gap now measured here

## [2026-04-16] capture | Polymarket CLOB execution reference

- Page: wiki/concepts/Polymarket CLOB execution.md
- Trigger: need to wire up live order submission for [[strategies/consensus_fade_plus1]] (Strategy C'). Current recommender is a display-only tool; next step is actual execution.
- Canonical library: `py-clob-client` v1 (mature, documented). v2 exists but examples target v1.
- Auth: L1 (EIP-712 private-key) for signing, L2 (5 HMAC headers) for API. `client.create_or_derive_api_creds()` is one-time deterministic derive per wallet.
- Key operational facts: `min_order_size=15` shares on recent markets (not 5); tick usually 0.01 but variable; weather markets are NegRisk; `buy NO = BUY side on NO token_id`, not a flag.
- No meaningful testnet — smoke-test with $1-5 post-only orders on deep-liquidity non-weather markets.
- Rate limits are generous (3500 POST/order per 10s) — bot at ~5 orders/day is never binding.
- Watch out for: chain_id=POLYGON (not Amoy default), neg_risk=True for weather, US IP geoblock.
- Related: [[Polymarket]], [[Polymarket CLOB WebSocket]]


## [2026-04-22] synthesis | Consensus-Fade v2 local-time anchoring

Rewrote Consensus-Fade +1 from "20 UTC fixed" to "≥16:00 city-local".
Caught a v1 price-source bug — old headline (n=94 / 98.9% / t=+4.44)
used `trade_table.entry_price`; hourly-prices replay gives n=84 / 97.6%
/ t=+3.14. Same price source across variants now. Canonical v2:
n=78 / 98.7% / +$0.046 / IS t=+1.85 / OOS t=+4.49. The 0.22-cap overlay
(100% hit, t=+7.70) documented as optional tightening in STRATEGY.md §5.1,
NOT canonical — 72/0 is statistically indistinguishable from 77/1.
Updated: `src/consensus_fade_plus1/STRATEGY.md`, `backtest.py`. Added
synthesis: [[2026-04-22 Consensus-Fade v2 local-time anchoring]].

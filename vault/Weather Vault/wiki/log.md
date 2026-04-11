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

## [2026-04-11] capture | NYC Polymarket upward-bias Strategy D (deployable)

- Page: wiki/syntheses/2026-04-11 NYC Polymarket upward-bias Strategy D.md
- Trigger: 8-iteration exploration loop on NYC Polymarket daily-temp markets (exp01–exp14 on `wt/nyc-polymarket-backtest`) produced a deployable thesis
- Headline: 12 EDT favorite's low edge is ~4°F too cold 80% of days. Strategy D buys `fav_lo + 2` bucket, earns +81.59 cum PnL per $1 across 44 bets, hit rate 29.5%, chronological OOS test (+69.79) > train (+11.81). Deploy at 2% Kelly with `p_entry ≥ 0.02` filter after 30-day paper-trade.
- Entities touched: [[Polymarket]], [[KLGA]], [[IEM]]
- Concepts touched: [[Polymarket weather market catalog]], [[ASOS 1-minute]], [[METAR]]
- Companion negative-result: [[2026-04-11 NYC Polymarket intraday sniping backtest]] (cross-ref added to both directions of index; back-ref into the sniping page itself is pending — file not yet on disk at time of capture)
- Anti-findings recorded: ASOS threshold sniping (no reaction window), paired underdog hedges (miss magnitudes too big), running-max chase (outlier lottery), solo favorite fade (median −$1, strictly worse than Strategy D)
- Discovered facts recorded: ladder is prob-normalized (no overround), ASOS 1-min LGA has 15°F gaps (use METAR instead), `end_date` is not market close, DuckDB naive-ts timezone gotcha
- Contradictions flagged: none
- Blocked on: Exp18 HRRR backfill (~42%) — will test whether HRRR shares the under-forecast bias or is closer to truth (direct alpha vs market)

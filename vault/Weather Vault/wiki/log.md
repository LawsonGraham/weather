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

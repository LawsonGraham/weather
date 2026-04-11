---
name: data-validation
description: >
  Paranoid first-principles audit methodology for every data source added to
  this repo. Use BEFORE marking any new data source "done", AFTER any
  non-trivial change to a download or transform script, and as a scheduled
  re-verification for production-critical data. Codifies a 6-level rigor
  ladder from manifest presence checks to bit-for-bit upstream re-fetch
  comparison. Every level has specific, reusable checks — no hand-waving, no
  "looks fine", no trusting the manifest. When in doubt, climb the ladder.
---

# Data-validation contract

Every data source under `scripts/<source>/` must pass a rigorous audit before it's trusted. This skill exists because **prior rounds of "it passes validate.py" missed real bugs**, including a silent half-open-interval boundary that dropped the last minute of every month file from the `iem_asos_1min` downloader. The lesson: shape-only checks are insufficient. Data fidelity must be verified *against the source*, not against itself.

This skill defines **six levels of audit rigor**. Every new data source runs at least levels 1–4 before being called complete. Levels 5–6 run for production-critical data, after schema or downloader changes, and on a recurring basis for long-running feeds. **When in doubt, climb the ladder.**

## The six levels

### Level 1 — Manifest & disk (cheap, required)

Purpose: verify the downloader didn't silently fail or crash mid-run.

Checks:

1. **Manifest exists.** `data/raw/<source>/MANIFEST.json` is present and parses as JSON.
2. **Status is `complete`.** Not `in_progress`, not `failed`, not missing.
3. **Manifest-declared contents match disk.** `manifest["target"]["contents"]` enumerates every file in the source dir, no orphans either direction.
4. **Manifest byte counts match disk.** `archive_bytes` and `extracted_bytes` are within 1% of `sum(f.stat().st_size for f in source_dir.rglob('*'))`.
5. **Processed manifest (if transform exists) is also complete**, and its `rows_written` equals the actual total rows across all parquets.

Implementation: these are 20 lines of Python against the manifest JSON. Run them first because they catch whole categories of failure for free.

### Level 2 — Row + column fidelity (cheap, required)

Purpose: verify the transform didn't silently drop or duplicate rows, and didn't silently drop a column.

Checks:

1. **Row count 1:1 per file.** For every raw CSV under `data/raw/<source>/`, the corresponding parquet under `data/processed/<source>/` must have the exact same number of data rows. If the raw format has comment preamble (e.g. IEM's `#DEBUG:` lines), strip those consistently between both sides.
2. **Column set preserved.** Every column in the raw CSV appears in the parquet (modulo explicit renames like `valid(UTC)` → `valid`). Any raw column missing from the parquet output → **FAIL**, the transform is dropping data.
3. **Column order stable run-to-run.** Run the transform twice with `--force` and compare the output parquet's `columns` list. Any reordering is a warning; unstable dtypes are a fail.
4. **No duplicate rows.** `full.group_by(["station", "valid"]).len().filter(len > 1)` must be empty. If the source has a compound key, use it.
5. **Month-file containment.** Every row in `YYYY-MM.parquet` has a `valid` timestamp whose year and month match the file name.
6. **No cross-file overlap.** For partitioned output, every `(key, valid)` row appears in exactly one file.

Failure modes this catches: comment-stripping bugs that eat legitimate rows, cast-error clips that drop columns, rename drift, duplicate handling of month boundaries, trailing whitespace rows, CRLF splits.

### Level 3 — Value-level fidelity (cheap-to-moderate, required)

Purpose: verify that every raw value is preserved in the parquet exactly, not just counted.

Checks:

1. **Bit-for-bit join comparison.** Build a lookup from raw CSV rows keyed by `(primary_key)` (e.g. `(station, valid)`). For every raw row, find the parquet row at the same key, then compare each column value-to-value. For numeric columns, allow ε = 1e-6 for float repr conversion. For string columns, require exact match with the single normalization rule `"" / "M" → None`. Any mismatch → **FAIL**.
2. **Null-count parity.** For every column, `parquet_nulls >= raw_nulls`. The transform can convert non-null values to null on cast failure (which should be flagged), but **never invent a non-null value** — that would be a fabrication. One explicit exception: sentinel-replacement rules (e.g. trace precipitation `T → 0.0001`) can legally reduce nulls. Document every exception in the transform and in validate.py.
3. **Timestamp parse coverage.** Every raw row must produce a non-null `valid` datetime in the parquet. If `strict=False` silently drops a timestamp, it's a data loss bug.
4. **Round-trip test.** Read one parquet, write it back, read again — results must be byte-equal at the cell level.

The null-count parity check is subtle but critical. It was the check that caught the `ice_accretion_{1,3,6}hr` trace-sentinel bug in `iem_metar/transform.py` — the transform was casting `"T" → null` for ice columns while correctly handling it for `p01i`. Nothing earlier in the audit noticed because the row counts, column sets, and schema shape were all fine.

### Level 4 — Schema invariants & cross-column consistency (cheap, required)

Purpose: verify decoded fields agree with their source columns and with physical reality.

Checks:

1. **Dtype assertions.** Every required column has its expected polars dtype. `valid: Datetime[us, UTC]`, booleans are `pl.Boolean` and never null, ints are `pl.Int64`, floats are `pl.Float64`.
2. **Range sanity.** Every numeric column falls inside plausible bounds: temperature in reasonable °F/°C, wind direction in `[0, 360]`, pressure in its expected band, precipitation non-negative, etc. Bounds are documented in `validate.py` constants.
3. **No NaN.** Every Float64 column: `(col.is_not_null() & col.is_nan()).sum() == 0`. NaN is a parse failure masquerading as a value and must be null.
4. **Monotonic ordering invariants.** If a source has an intrinsic ordering (e.g. sky layer heights `skyl1 ≤ skyl2 ≤ skyl3 ≤ skyl4`), verify it on every row.
5. **Cross-column consistency.** Decoded fields should agree with their source columns within rounding. E.g. `temp_c` × 9/5 + 32 must round to `tmpf` on every row where both are non-null; `slp_mb_rmk` must equal `mslp` when both are non-null; an RMK-derived 1-hour precip must equal the IEM-decoded `p01i` to 0.005 in.
6. **Redundancy equality.** For every column that's a re-derivation of another column (like `slp_mb_rmk` re-decoding what IEM already wrote as `mslp`), verify exact agreement. A divergence here is either upstream parsing drift or your own decoder breaking.

Failure modes this catches: bad trace-sentinel handling (creates NaN), decoder unit mismatches, typo in a formula, mis-ordered fields.

### Level 5 — Fresh upstream re-fetch (moderate cost, required before "done")

Purpose: verify what you have on disk actually matches what the upstream serves **right now**. This is the level that catches silent boundary bugs, CGI parameter quirks, and query-building typos — things that pass every shape check because the test data is self-consistent.

Checks:

1. **Re-fetch a complete window from upstream.** Pick a day or two that are representative (include a high-activity day with SPECIs, storms, or special observations). Hit the upstream endpoint fresh using the same parameters your downloader builds.
2. **Row count comparison.** Fresh fetch row count must match the saved data for the same window. If it differs, **investigate**. Don't assume upstream data changed — first check whether your downloader is using a different query shape than you thought.
3. **Row-set comparison.** Build a lookup of `(key, full_row_string)` on both sides and verify every fresh row is present in your saved data. Any missing row → **FAIL**.
4. **Boundary probe.** Deliberately test the inclusive/exclusive semantics of every time or index parameter your downloader uses. For time ranges: query `[start, end]` and `[start, end-1]` and see if the row count changes — if not, the CGI is half-open and you have a potential boundary bug. For offset/limit pagination: do two consecutive fetches with overlapping ranges and verify the overlap rows are identical.
5. **Historical scope probe.** Fetch one day from the oldest end of your window and one from the newest — do they both come back? Some CGIs silently cap lookback.

This level caught the `iem_asos_1min` half-open interval bug. Levels 1–4 all passed because the saved data was internally consistent; the data-loss was invisible until we compared against upstream. **Run level 5 before you call any new data source done.**

### Level 6 — Invariant stress tests (moderate-to-expensive, recurring)

Purpose: test things that only show up at scale, at edge cases, or over time.

Checks:

1. **Date coverage — every day.** For each station/key, every calendar day in the requested window has ≥1 observation. Missing days → investigate against upstream: is it a real outage, or did your downloader clip the window?
2. **Hour/minute coverage for high-frequency data.** For 1-minute data, verify ~60 obs/hour average; for hourly data, verify one routine report per hour; for METAR, check both routine (`:51`) and SPECI mix. Use the upstream to verify any missing hours are real gaps vs download bugs.
3. **Duplicate-timestamp / corrected-report handling.** METAR has `COR` corrections that can duplicate a routine report's timestamp. Verify the transform handles them correctly — ideally both appear as separate rows with a flag column, not silently deduplicated.
4. **Second-offset check.** Timestamps at exactly `:00` seconds (clean minute grid) vs scattered fractional seconds. If your source emits on a clean grid, anything else is a parse quirk to investigate.
5. **Encoding check.** Raw text columns (METAR strings, station names, free text): scan for non-ASCII bytes. Unexpected encodings are often a sign that the CGI's `delim` or `format` parameter is emitting something weird.
6. **Idempotency.** Running the transform (or downloader) twice without `--force` must be a no-op. Running with `--force` must produce byte-identical output (modulo any non-determinism in parquet compression, which is fine if the *cells* are equal).
7. **Independent decoder cross-check.** For every non-trivial decoder (e.g. METAR remark parsing via `python-metar`), implement a minimal regex or hand-parser yourself and compare. Agreement → both are correct (or both are wrong the same way, which is recoverable). Disagreement → investigate both. This was how we verified `slp_mb_rmk`, `max_temp_24hr_c`, `precip_6hr_in`, and `presrr`/`presfr` in iem_metar all decoded correctly — each has an independent regex cross-check in its audit.
8. **Cross-source join sanity.** If two sources resolve to the same key (e.g. METAR and ASOS 1-min both have `(station, valid_utc)`), verify joinability and agreement on overlapping fields.

## The audit cadence

| Moment | Minimum rigor |
|---|---|
| First time a new data source is added | **Levels 1–5, then levels 6 at random** on a representative sample |
| After *any* change to the downloader's query params or schema | Levels 1–5, *including the boundary probe* |
| After *any* change to the transform's cast rules or column list | Levels 1–4 |
| After an upstream schema migration is detected | Levels 1–6 |
| Weekly / monthly for production-critical feeds | Levels 1–4 via `validate.py`, level 5 spot-checks monthly |

The cost is linear-ish in data size. For a few-hundred-MB source like METAR or ASOS 1-min, the full levels 1–6 run in under 5 minutes of wallclock. For GB-scale sources (HRRR, NEXRAD) levels 1–4 are still cheap, level 5 needs to be done on a sampled window, and level 6 is mostly done against indexed metadata.

## How to implement each level

### In `validate.py` (levels 1–4, always required)

Every source's `validate.py` must include:

- Manifest presence + status checks (level 1)
- Row + column fidelity between raw CSV and parquet (level 2)
- Bit-for-bit value comparison on a representative sample, or full comparison if data fits in memory (level 3)
- Null-count parity with documented exceptions (level 3)
- Timestamp parse coverage (level 3)
- Required-column schema assertions + range sanity (level 4)
- Any cross-column consistency checks the source admits (level 4)

Fail loudly on any error. Warn on ambiguity. `validate.py` is the permanent guardian of the source's data integrity — it runs on every rebuild and catches regressions before they land.

### Ad-hoc Python scripts (levels 5–6, for deep audits)

Levels 5 and 6 are run interactively during audits, not on every build. They live as short single-purpose Python scripts, executed from the repo root. When a check at level 5 or 6 surfaces a real bug, **port the check down into `validate.py` at an appropriate level** so it runs forever after.

## What "complete and accurate" actually means

For this repo, a data source is "complete and accurate" when **all of these are true**:

- Manifests reflect reality (level 1)
- No raw row is dropped, no column dropped, no value silently cast to null without an explicit rule (levels 2–3)
- Every decoded field agrees with its source column on every row (level 4)
- A fresh upstream fetch for a representative day matches the saved data byte-for-byte at the row level (level 5)
- Every decoder has an independent cross-check, and both agree (level 6, where applicable)
- `validate.py` encodes levels 1–4 as permanent checks, so future rebuilds can't regress

Anything less is "shape-only-correct" and hasn't actually been verified.

## Anti-patterns to reject

- **"It parses with polars, therefore it's fine"** — polars with `strict=False` silently nulls on cast failure. Null-count parity catches this; nothing else does.
- **"The row count matches, therefore the data is preserved"** — tells you nothing about column values. Value-level comparison is the only proof.
- **"The schema is stable run-to-run, therefore the transform is correct"** — a buggy transform that always loses the same rows is stably wrong. Fresh upstream re-fetch is the only proof that the downloader isn't lying.
- **"The CGI is well-documented, so the boundary is inclusive"** — assume nothing. Probe the boundary with the exact query your downloader sends. We found two IEM CGIs silently using half-open semantics despite documentation that doesn't mention it.
- **"It's been working for months, so it must be correct"** — latent bugs in boundary handling, trace sentinels, and decoder fallbacks can persist invisibly for arbitrary time. The ones we caught today were present from day one.
- **"We'll add validate.py later"** — the canonical tool for detecting regressions. If it doesn't exist, every future change is unverified.

## Lessons learned — real findings from past audits

(Keep this section updated. Every real bug caught by an audit is a case study for the next person.)

### `iem_metar` (2026-04-11, Phase 1 walking-skeleton audit)

- **Ice accretion trace sentinels** — `ice_accretion_{1,3,6}hr` was cast to null for 6 rows of trace icing because the trace-sentinel handling only covered `p01i`. Caught by level 3 null-count parity against the sentinel-stripped raw CSV. Fix: `TRACE_COLS` tuple in transform.py covers all 4 trace-eligible columns.
- **Mislabeled `temp_c_rmk`** — python-metar's `.temp.value('C')` falls back to the main-body integer `TT/TD` field when the RMK T-group is absent. 113 rows were carrying main-body fallback values despite the `_rmk` suffix promising RMK-only. Caught by level 4 cross-column consistency (`temp_c × 9/5 + 32` must equal `tmpf`, which was true — the naming was the only thing wrong). Fix: renamed to `temp_c` / `dewpt_c`, documented fallback behavior.
- **`slp_mb_rmk` redundancy** — verified equal to IEM's `mslp` on all 5381 non-null rows, 0 disagreements. Kept as an integrity cross-check column.
- **IEM CGI is half-open `[start, end)`** — both `asos.py` (METAR) and `asos1min.py` (1-min) drop the exact end-boundary minute. The old downloader used `hour2=23, minute2=59` for month-end queries, dropping every month's final minute. Caught at **level 5 by comparing a fresh single-day fetch against saved data**. For METAR there was 0 actual row loss in the Phase 1 window (no `:59` METARs existed), for 1-min we recovered 11 rows across the 10-month window. Fix: use `[first_day 00:00, (last_day+1) 00:00)` half-open interval in both downloaders.

### `iem_asos_1min` (2026-04-11)

- Same half-open-interval bug as `iem_metar` (fixed in the same commit).
- Level 6 date-coverage check surfaced 27 LGA + 14 NYC missing calendar days across the 2025-06 → 2026-04 window. Verified against upstream: all are real station outages, not download bugs. Documented, not fixed (can't fix a real station outage, but the audit proves they're real).

## References

- [`scripts/iem_metar/validate.py`](../../../scripts/iem_metar/validate.py) — reference implementation of levels 1–4
- [`.claude/skills/data-script/SKILL.md`](../data-script/SKILL.md) — the data-script contract every source follows
- [`vault/Weather Vault/wiki/concepts/Data Validation.md`](../../../vault/Weather%20Vault/wiki/concepts/Data%20Validation.md) — the project memory companion to this skill, including the why and the historical bug record

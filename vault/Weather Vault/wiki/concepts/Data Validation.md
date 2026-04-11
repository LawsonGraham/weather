---
tags: [concept, data-quality, validation, audit, methodology]
date: 2026-04-11
related: "[[METAR]], [[Project Scope]], [[index]]"
---

# Data Validation

The paranoid first-principles audit methodology for every data source in this repo. Companion to the [`.claude/skills/data-validation/SKILL.md`](../../../.claude/skills/data-validation/SKILL.md) reusable-procedure skill. This page is the project memory — it keeps the **why**, the **historical bug record**, and the lessons learned; the skill keeps the **how** as a reusable checklist.

## Why this exists

Two complementary forms of failure tend to sneak into data pipelines:

1. **Silent upstream data loss.** The downloader's query is *technically valid* but the upstream CGI has quirks — inclusive vs exclusive time ranges, row limits, pagination, schema drift — and rows disappear before they ever reach disk. Our internal consistency checks can't see this; only comparing against a fresh upstream fetch can.
2. **Silent transform corruption.** The transform does a cast that fails on an unusual sentinel and leaves a null, or a regex misses an edge case, or a rename drops a column, or a trace-sentinel substitution only applies to some columns. Shape-only checks pass; value-level comparison against the raw file is the only thing that catches these.

This project is a trading stack where **edge vs market-implied probability is the target metric**. An undetected data loss at the boundary of a month file, or a mis-decoded max-temp-24hr value, would bleed directly into every downstream model and every live trade. We cannot afford to trust data we haven't paranoidly verified.

## The six-level rigor ladder

Full detail in the skill. Summary here so vault readers can follow along:

| Level | Name | What it catches | Cost |
|---|---|---|---|
| 1 | Manifest & disk | Crashed or half-completed downloads, orphaned files | trivial |
| 2 | Row + column fidelity | Row drops, column drops, duplicates, cross-file overlap | cheap |
| 3 | Value-level fidelity | Cast failures, inventions, timestamp losses, strip/encoding bugs | cheap-moderate |
| 4 | Schema invariants & cross-column consistency | Dtype drift, range violations, decoder errors, NaN, redundancy divergence | cheap |
| 5 | Fresh upstream re-fetch | Downloader query-building bugs, boundary/inclusivity quirks, lookback caps | moderate (1 HTTP roundtrip per probe) |
| 6 | Invariant stress tests | Edge cases, duplicate timestamps, encoding, idempotency, decoder cross-checks | moderate-expensive |

**Rule:** every new data source passes levels 1–5 before it's called done. Levels 1–4 are encoded in the source's `validate.py` and run forever after. Level 5's upstream probes re-run any time the downloader changes. Level 6 checks graduate into `validate.py` after the first real bug that one of them catches.

## Historical bug record

This section is the compounded wisdom of every paranoid audit the project has run. Every entry is a real bug that was caught, documented, and fixed — and the level of rigor that caught it.

### 2026-04-11 — `iem_metar` Phase 1 walking-skeleton audit

**Caught at level 3 (null-count parity):** `ice_accretion_{1,3,6}hr` columns were silently losing 6 rows of trace icing data to the `T` sentinel. The transform only handled `T → 0.0001` for `p01i`; ice columns were casting `"T" → null`. Fix: `TRACE_COLS` tuple covering all 4 trace-eligible columns. **Lesson:** every sentinel must be handled exhaustively by column, not just on the one column you first noticed.

**Caught at level 4 (cross-column consistency):** `temp_c_rmk` and `dewpt_c_rmk` were mislabeled. python-metar's `.temp.value('C')` writes the main-body integer `TT/TD` first and only overwrites with the RMK T-group value when the T-group is present. For SPECIs without a T-group, 113 rows were carrying main-body fallback under a column name promising strict RMK origin. The cross-check `round(temp_c × 9/5 + 32) == tmpf` passed on all rows — it was the naming that was wrong, not the values. Fix: rename to `temp_c` / `dewpt_c`, document the fallback semantics. **Lesson:** column names are part of the data contract. If the name lies, the data is wrong even if the values are right.

**Caught at level 4 (redundancy equality):** `slp_mb_rmk` was found to be exactly equal to IEM's `mslp` on all 5381 non-null rows, 0 disagreements, identical null coverage. IEM already decodes RMK SLP-groups into its `mslp` column; our pipeline re-decoded the same group. Not a bug — the two columns agree — but documented as a guaranteed-equal cross-check column so future audits can detect upstream parsing drift. **Lesson:** redundancy isn't always waste. Well-documented redundancy is a free integrity checkpoint.

**🎯 Caught at level 5 (fresh upstream re-fetch) — this was the critical one:** both IEM CGIs (`asos.py` for METAR, `asos1min.py` for 1-min) use **half-open `[start, end)` semantics** for their time-range parameters. Our downloaders passed `hour2=23, minute2=59` for the last day of each month range, which silently dropped the `:59` boundary minute. Verified empirically:

```
asos1min.py LGA 2026-03-12 [00:00, 23:59]: 700 rows, last = 23:58
asos1min.py LGA 2026-03-12 [00:00, 23:58]: 699 rows, last = 23:57
asos.py     LGA 2026-03-12 [00:00, 11:35]: 24 rows, last = 10:51
asos.py     LGA 2026-03-12 [00:00, 11:34]: 24 rows, last = 10:51
```

The probe trick: query your documented window, then query `window - 1 end unit` and compare row counts. If the row count didn't change, the CGI is half-open and your boundary query is losing the edge.

Impact: `iem_metar` lost 0 rows (no `:59` METARs existed in the window — routine reports are at `:51`, SPECIs never landed at `:59`), but the bug was latent and would have lost rows in any future window that contained a `:59` SPECI. `iem_asos_1min` lost 11 rows across the 10-month window — every month file where both `:58` and `:59` existed was missing the `:59`. Fix: use `[first_day 00:00, (last_day+1) 00:00)` explicit half-open interval in both downloaders. The fix is exactly one line per downloader.

**Lessons compounded:**
- **Never trust CGI boundary semantics.** Probe them explicitly. Document what you find.
- **"Zero rows lost in this window" doesn't mean "the bug isn't there."** A latent bug that happens to not fire on current data is still a bug, and will bite under the next window you pull.
- **Level 5 fresh re-fetch is NOT optional.** Levels 1–4 passed cleanly; it was only the direct upstream comparison that exposed the boundary issue.
- **One HTTP roundtrip to a date we've already downloaded is cheap insurance.** Always do it.

### 2026-04-11 — `iem_asos_1min` Phase 1 comprehensive audit

**Same half-open-interval bug as `iem_metar`** (fixed in the same commit). Recovered 11 rows across 8 month files where `:58` and `:59` both existed.

**Caught at level 6 (date-coverage check):** 27 LGA days + 14 NYC days with zero observations across the 2025-06 → 2026-04 window. **Not a bug** — verified every missing day against a fresh IEM fetch, they're real station outages. Documented as "expected gaps" in the source notes; no fix possible.

**Also documented but not a bug:** LGA mean observations/hour is 57 (vs expected 60), NYC is 58. Station-level gaps are normal; the audit confirms our downloader captures every observation that the station actually emitted.

## How levels 1–4 are encoded in `validate.py`

Every source's `validate.py` should implement the level 1–4 checks as permanent guardians. Reference implementation: [`scripts/iem_metar/validate.py`](../../../scripts/iem_metar/validate.py), which has:

- `check_manifests` (level 1)
- `check_file_completeness` + `check_schema_and_rows` (level 2)
- `check_csv_to_parquet_fidelity` + `check_csv_null_parity` + `check_timestamp_coverage` (level 3)
- `check_rmk_temp_consistency` + `check_slp_redundancy` + `check_report_type_mix` + `check_gap_distribution` (level 4)

Every new source copies this skeleton and specializes it.

## How levels 5–6 get run

These are interactive audits, not automated. Typical audit procedure when adding a new source or after a downloader change:

1. Run `validate.py` — must be clean first (levels 1–4).
2. Write a scratch Python file at the repo root that:
   - Picks 1–2 representative days, including at least one with "interesting" data (SPECIs, storms, special events)
   - Hits the upstream endpoint fresh with the same parameters the downloader uses
   - Compares the fresh fetch's row-set against the saved data by key
   - **Runs a boundary probe**: same query with `end - 1 unit` vs `end` — if the row count doesn't change, flag the half-open issue
   - Samples the column-level agreement
   - Runs any decoder cross-checks (independent regex parse of RMK groups, etc.)
3. If anything fails at level 5, **do not mark the source done**. Fix the downloader, re-run, repeat.
4. If level 6 checks catch something, fix it and graduate the check into `validate.py` so it can't regress.

## Cost estimates (for planning)

For a few-hundred-MB source with < 10 files and tens of thousands of rows (METAR, ASOS 1-min):

- Levels 1–4 via `validate.py`: ~5 seconds
- Level 5 fresh-fetch for one representative day: ~5 seconds of wallclock (1 HTTP roundtrip)
- Level 5 boundary probe: ~2–4 HTTP roundtrips, ~10 seconds
- Level 6 date-coverage + invariant scan: ~30 seconds

Total for a complete paranoid audit of a small source: **well under 5 minutes**. There is no excuse for skipping this.

For a GB-scale source:

- Levels 1–4: 30 seconds to 2 minutes
- Level 5: same cost per sampled day, done for 3–5 days across the window
- Level 6: runs against indexed metadata, ~1 minute

Total: still under 10 minutes. Still no excuse.

## What "complete and accurate" actually means in this repo

A data source is *complete and accurate* when all of these are true simultaneously:

1. Manifests reflect reality (level 1 passes)
2. No raw row is dropped, no column dropped, no value silently cast to null without an explicit documented rule (levels 2–3 pass)
3. Every decoded field agrees with its source column and with physical reality (level 4 passes)
4. A fresh upstream fetch for at least one representative day matches the saved data at the row level (level 5 passes)
5. Every non-trivial decoder has at least one independent cross-check and they agree (level 6 where applicable)
6. `validate.py` encodes levels 1–4 as permanent regression guards

Anything less is "shape-only-correct" and has not been verified in a way that can be trusted for live trading.

## Related

- [[METAR]] — Layer 3 source whose audit discovered and drove this methodology
- [`.claude/skills/data-validation/SKILL.md`](../../../.claude/skills/data-validation/SKILL.md) — the reusable procedure skill; this page is its history/rationale
- [`.claude/skills/data-script/SKILL.md`](../../../.claude/skills/data-script/SKILL.md) — the data-script contract every source follows
- [`scripts/iem_metar/validate.py`](../../../scripts/iem_metar/validate.py) — reference implementation of levels 1–4

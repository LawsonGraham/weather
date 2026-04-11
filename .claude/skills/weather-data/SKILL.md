---
name: weather-data
description: "Repo conventions for weather-data scripts — storage layout, station identifiers, causal alignment rules, recommended libraries, and common pitfalls. Read when writing or reviewing a download / transform / validate under scripts/<source>/. For the data-source content itself (6-layer stack, upstream URLs, variable lists, phase ordering) read vault/Weather Vault/Project Scope.md and the relevant entity / concept pages under wiki/."
allowed-tools: Read, Grep, Glob, WebFetch
---

# Weather data — repo conventions

This skill is a **repo conventions reference**, not a data-source reference. For the 6-layer data stack content (what's in HRRR, ASOS, METAR, Synoptic, NEXRAD, TAF; upstream URLs; variable lists), read:

- [`vault/Weather Vault/Project Scope.md`](../../../vault/Weather%20Vault/Project%20Scope.md) — canonical scoping doc
- Vault entity pages — `wiki/entities/` for providers and stations (`[[IEM]]`, `[[Polymarket]]`, `[[Kalshi]]`, `[[KNYC]]`, `[[KLGA]]`, …)
- Vault concept pages — `wiki/concepts/` for dataset types and methods (`[[ASOS 1-minute]]`, `[[METAR]]`, `[[Data Validation]]`, …)

This skill covers only the **in-repo conventions** every data script must follow.

## Storage layout — see data-script skill for the canonical contract

- **`data/raw/<source>/`** — immutable originals. Directory name matches `scripts/<source>/`.
  - Every `raw/<source>/` must have `MANIFEST.json` (schema v1, in the data-script skill) and a `download.log`.
  - Download scripts must be idempotent: check `MANIFEST.json.download.status` before doing work. Copy `.claude/skills/data-script/template.py` for every new source.
- **`data/interim/<step>/`** — cleaned, filtered, or decoded intermediates. Organized by transformation step, not by source.
- **`data/processed/<task>/`** — final model- or backtest-ready artifacts. Organized by downstream task.
- The whole `data/` tree is gitignored. **Never commit** GRIB2, Parquet, CSV, or NetCDF files.
- Never hand-edit anything in `raw/`. If you need a transformation, write `scripts/<source>/transform.py` that emits to `data/interim/` or `data/processed/`.

## Identifiers

- **Airports / stations**: **bare** IEM codes — no `K` prefix. Use `JFK`, `NYC`, `LGA`, not `KJFK`. The wiki entity pages use the `K`-prefixed ICAO as the canonical page name (`[[KNYC]]`, `[[KLGA]]`), but request parameters and raw-data subdirectory names are always bare.
- **Time**: UTC internally, always. Convert to local only at the market-resolution boundary.
- **HRRR run identifier**: `init_time` (UTC datetime) + `fxx` (forecast hour int).

## Alignment (causality is load-bearing)

- Join HRRR forecasts to ASOS observations by `(station, valid_time)` where `valid_time = init_time + fxx hours`.
- **Strictly causal**: no observation from `t >= valid_time` may leak into features predicting the observation at `valid_time`.
- Use `init_time` as the "as-of" reference when constructing feature sets — whatever was known at `init_time` is fair game; anything after is leakage.

## Recommended libraries

Declared in `pyproject.toml`:

- `herbie-data` — HRRR / GFS GRIB2 subset via byte-range (don't reinvent)
- `metar` — METAR string parsing
- `xarray` + `cfgrib` — gridded data (cfgrib bundles eccodes wheels on macOS)
- `polars` (preferred) or `pandas` — tabular data
- `duckdb` — local analytical queries over Parquet

Deferred — add with `uv add <pkg>` when the phase comes:

- `nexradaws` + `arm-pyart` — NEXRAD Level 2 (Phase 4)
- `SynopticPy` — Synoptic API (Phase 3, paid)

## Conventions enforced by other skills

- **Data-script contract** — [`.claude/skills/data-script/SKILL.md`](../data-script/SKILL.md). Required CLI flags, MANIFEST.json lifecycle, idempotency gate, ruff/pyright-clean bar. Every new source starts by copying `template.py`.
- **Data-validation** — [`.claude/skills/data-validation/SKILL.md`](../data-validation/SKILL.md). The 6-level audit ladder every new source must pass before being called "done." Already has a historical bug record of real issues caught.
- **Vault capture** — [`.claude/skills/vault-capture/SKILL.md`](../vault-capture/SKILL.md). Every new source gets a wiki entity page + concept pages for novel methods, cross-linked and logged.
- **Worktree-first** — [`.claude/skills/worktree-first/SKILL.md`](../worktree-first/SKILL.md). All data work happens in a worktree with `data/` symlinked to main so downloads always land in the canonical place.

## Common pitfalls

1. **Don't pull full HRRR domain files** — use byte-range via Herbie. Full files are ~200MB each.
2. **CONUS only** — HRRR does not cover Shanghai, London, etc. Use GFS or ECMWF open data for international targets.
3. **Time-based splits only** — never random splits for time series. Per the model-conventions section of `CLAUDE.md`.
4. **Don't mock the data layer in tests** — mock/prod divergence is a project risk per vault notes. Test against real (sampled) data.
5. **Market resolution windows matter** — a Kalshi "daily high" might be midnight-to-midnight local, which is NOT the same as UTC-day. Resolve this per market in config, not in pipeline code. See `[[Kalshi]]` and `[[Polymarket]]` entity pages for the specific resolution rules.
6. **Use the HRRRx ensemble** — 36 members give a free empirical distribution for calibrated probabilities. This is the single highest-value upgrade vs a deterministic pipeline per vault scoping.
7. **Trust the skill ladder, not your instinct.** When a source feels "done," run the `data-validation` skill's levels 1–5. It's caught bugs every shape-only check missed.

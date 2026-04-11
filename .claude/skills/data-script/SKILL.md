---
name: data-script
description: >
  Canonical contract for every data script in scripts/{download,transform}. Defines
  the 2-stage architecture, required CLI flags, MANIFEST.json v1 lifecycle, idempotency
  requirements, logging convention, and the ruff/pyright-clean bar. Every new data
  source script MUST start from .claude/skills/data-script/template.py and MUST pass
  the quality bar on commit. Invoke when creating, reviewing, or debugging a data
  script, or when designing a new data source pipeline.
---

# Data-script contract

Every script in `scripts/download/` and `scripts/transform/` follows this contract. No exceptions.

## 2-stage architecture

- **`scripts/download/`** — pull raw data from upstream into `data/raw/<source>/`. This covers catalog discovery (slug/lookup tables — small, infrequent) AND bulk data retrieval (large, batched) — both are "download." Output is immutable.
- **`scripts/transform/`** — read something under `data/raw/` and produce `data/interim/<step>/` or `data/processed/<task>/`. Always reproducible from `raw/`.

**Validators** live alongside the downloader they validate — `scripts/download/<source>/validate.py` is a sibling of `script.py`, not a separate stage.

## File layout per source

```
scripts/<stage>/<source>/
├── script.py          # the script (REQUIRED)
├── validate.py        # optional post-run validator
└── <helper>.py        # optional source-specific helpers, only if used by this source
```

**No `README.md` in the source subdir** — see the `minimal-docs` skill. The script file is the doc (top docstring + `--help`).

**No shared `_common.py`.** Each script is self-contained. This is deliberate — keeps scripts independently reviewable, runnable, and editable by parallel Claude sessions without import-graph collisions.

## Standard CLI flags

Every script MUST accept these flags (names must be exact):

| Flag | Behavior |
|---|---|
| `--force` | bypass the "already complete" idempotency check; keep any partial state so the next run can resume |
| `--fresh` | delete any partial state before retrying (implies `--force`) |
| `--dry-run` | print the plan and exit without mutations |
| `--verbose` / `-v` | more log output |

Source-specific flags (`--station`, `--start-date`, `--city`, `--limit`, etc.) come after the standard flags and are source-specific.

## MANIFEST.json contract (v1)

Every download writes `data/raw/<source>/MANIFEST.json`. Every transform writes `data/{interim|processed}/<target>/MANIFEST.json`. Schema:

```json
{
  "manifest_version": 1,
  "source_name": "<source>",
  "description": "one sentence",
  "upstream": {"repo": "...", "url": "..."},
  "script": {"path": "scripts/<stage>/<source>/script.py", "version": N},
  "download": {
    "started_at": "ISO UTC",
    "completed_at": "ISO UTC or null",
    "archive_bytes": null,
    "extracted_bytes": null,
    "status": "in_progress" | "complete" | "failed"
  },
  "target": {"raw_dir": "data/<stage>/<source>", "contents": []},
  "notes": ""
}
```

**Status lifecycle:**

1. Script enters → writes initial manifest with `status: in_progress`
2. Work happens
3. Script calls `manifest.complete(...)` → `status: complete`
4. Any exception OR forgotten `complete()` → `status: failed`, error reason appended to `notes`

The manifest is a context manager. Use it. Don't hand-roll JSON writes.

## Required main() flow

```python
def main():
    args = parse_args()
    configure_logging(log_path, verbose=args.verbose)

    if not args.force and manifest_already_complete():
        log.info("already complete — skip")
        return 0

    check_preconditions(log)       # required binaries, disk space, env vars

    if args.fresh:
        wipe_partial_state()

    with DownloadManifest(...) as manifest:
        do_work(args, manifest, dry_run=args.dry_run)
        manifest.complete(...)
    return 0
```

## Exit codes

- `0` — success (including "already complete, skipped")
- `1` — error (any unhandled exception or explicit SystemExit)
- `2` — reserved if you want to distinguish "skipped" from fresh success

## Idempotency rules

- Manifest `status == "complete"` → skip without doing work (unless `--force`)
- Manifest `status == "in_progress"` without `--force` → refuse to run with a clear error (another run may be active OR previous run crashed mid-flight)
- Manifest `status == "failed"` without `--force` → refuse to run with a clear error (previous run failed, investigate first)
- `--force` bypasses the idempotency gate but keeps partial state (to enable resume)
- `--fresh` additionally wipes partial state before running

## Quality bar — enforced on commit

Every data script MUST pass these before the commit lands:

- `uv run ruff check .` → clean
- `uv run ruff format --check .` → clean
- `uv run pyright` → clean (or explicit, narrow `# pyright: ignore[rule]` comments with justification)

No commits land with dirty data scripts. See the pre-commit hook (when it exists) or run manually before committing. The `minimal-docs` skill applies here too: no per-source README, no bloated docstrings, only targeted comments.

## When adding a new data source — checklist

1. `mkdir scripts/<stage>/<source>/`
2. `cp .claude/skills/data-script/template.py scripts/<stage>/<source>/script.py`
3. Update the top docstring (what / upstream / output / flags — 4-6 lines)
4. Update the `# --- source metadata ---` block constants
5. Fill in `do_work()` with the source-specific logic
6. `uv run ruff check scripts/<stage>/<source>/` → clean
7. `uv run ruff format scripts/<stage>/<source>/` → clean
8. `uv run pyright scripts/<stage>/<source>/` → clean
9. Test with `--dry-run` first, then a small real run, then the full run
10. Commit (in a worktree per Rule 8) with a focused message

That's it. No README. No separate architecture doc. No "usage" markdown. Script + `--help` covers it all.

## When in doubt

- Is my script missing a standard flag? Add it — the skill is strict.
- Does my script need a README? No.
- Should I write a shared helper module? No — self-contained.
- Can I skip the MANIFEST? No — idempotency and provenance both depend on it.
- Does this fit `download` or `transform`? If it reads from upstream → `download`. If it reads from `data/raw/` → `transform`.

# `scripts/transform/` — transformation script convention

Scripts that turn `data/raw/` into `data/interim/` or `data/processed/`. Each
transformation has its own folder, same per-source-folder pattern we use under
`scripts/download/`.

## Layout

```
scripts/transform/
├── README.md                                (this file)
└── <step_name>/                             (one folder per transformation step)
    ├── README.md                            (step-specific: inputs, outputs, rationale)
    └── build.py                             (the actual script)
```

**Rule:** `<step_name>` should match the directory name the script writes to
under `data/interim/<step_name>/` or `data/processed/<step_name>/`. Same
convention as downloaders — matching names make the relationship obvious.

## Contract — every transform must

1. **Be reproducible** from raw data and a tracked script. If a transform can't
   be rebuilt from scratch, it doesn't belong under `interim/` or `processed/`.
2. **Read from `data/raw/`** (or other tracked intermediates) and write to
   `data/interim/<step>/` or `data/processed/<task>/`. **Never modify
   `data/raw/`.**
3. **Write a `MANIFEST.json`** in its output directory with provenance:
   input paths, script version, timestamp, row counts, hash/identifier of
   the raw source snapshot where possible.
4. **Be idempotent and re-runnable.** A second run should overwrite cleanly
   or skip if nothing changed.
5. **Support versioning** — bump an output suffix (e.g. `slugs_v1.csv`,
   `slugs_v2.csv`) when the transformation logic changes materially. Old
   versions stay on disk until someone deletes them.
6. **Write a human-readable report** alongside the data artifact when the
   transformation involves filtering/classification. The report is the proof
   that the logic is correct.

## Dependencies

Same rule as `scripts/download/`: stdlib-preferred until we have a project
virtualenv. `pyarrow`/`pandas` are already installed at system Python level
for the existing downloaded data, so lightweight analytics scripts can use
them without waiting for the uv setup.

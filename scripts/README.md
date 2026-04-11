# `scripts/` — conventions

Standalone scripts for operational tasks: downloading data, transforming data, running one-off jobs.

Scripts live here when they:
- are run from the CLI (not imported),
- produce or transform files under `data/`,
- are too small or too orthogonal to justify a package under `src/`.

Anything that becomes a reusable library or a long-running service graduates out of `scripts/` into `src/` (not yet created).

## Layout

```
scripts/
├── README.md                    (this file)
├── download/                    (ingest upstream sources → data/raw/)
│   ├── README.md
│   ├── _common.sh               (shared bash helpers)
│   └── <source>.sh | <source>.py
├── transform/                   (raw → interim / interim → processed)   (future)
└── ops/                         (ad-hoc maintenance: rebuild, gc, etc.) (future)
```

## Global rules

1. **One purpose per script.** Don't stuff two unrelated jobs into one file.
2. **Idempotent by default.** Re-running a script should be safe and fast (skip if already done) unless `--force` is passed.
3. **Fail loudly.** Bash scripts set `set -euo pipefail` and trap failures; Python scripts raise and log.
4. **Log to stdout AND to a file** under the output directory (e.g. `data/raw/<source>/download.log`).
5. **Absolute paths.** Compute paths relative to the repo root (`REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"`), never rely on the current working directory.
6. **No secrets in scripts.** Read credentials from `.env` via a loader; never hardcode.
7. **Versioned behavior.** When you change what a script produces in a non-trivial way, bump its `script_version` (recorded in any manifests it writes).

## Language choice

- **Bash** for: simple download + extract flows, file orchestration, things that shell does well.
- **Python** for: paginated API ingest, checksum/manifest manipulation, anything that needs real data structures, anything involving HTTP auth flows.

Scripts in either language must follow the same conventions (logging, idempotency, manifest schema). See `scripts/download/README.md` for the download-specific contract.

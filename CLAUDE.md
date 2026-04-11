# CLAUDE.md — Weather Prediction Markets Project

This repo is a solo quantitative trading project targeting **prediction-market weather contracts** on Kalshi and Polymarket at major US airports. It is NOT a generic weather-forecasting product. The target metric is **edge vs market-implied probability**, not RMSE vs TAF.

Start every non-trivial session by reading [vault/Weather Vault/Project Scope.md](vault/Weather%20Vault/Project%20Scope.md) and [vault/Weather Vault/wiki/index.md](vault/Weather%20Vault/wiki/index.md).

## Core principles

- **Trading, not forecasting.** Probabilistic outputs (`P(high > threshold)`) and calibration matter more than point forecasts. Only trade when `|edge| > transaction costs`.
- **CONUS-first.** HRRR covers CONUS only. Shanghai and international markets need a different stack and are out of scope for v1.
- **Airports-specific.** Models are trained per-airport on that station's ground truth (IEM ASOS 1-min). Local microclimate patterns are where alpha lives.
- **Real-time pipeline is load-bearing.** Core alpha comes from reacting to new HRRR runs within a 15–45 min window before the market reprices.
- **Python-first, everywhere.** Every script, data ingest, feature pipeline, model, backtest, analysis, and real-time component in this repo is Python via **`uv`**. The weather (Herbie, xarray, cfgrib, metar, arm-pyart, SynopticPy) and ML (scikit-learn, xgboost, lightgbm, statsmodels) ecosystems are Python-dominant — no TypeScript or Node in this repo. Bash is permitted only for tiny shell-native download/extract flows under `scripts/download/`; anything with real logic, parsing, or data structures goes to Python. See [Language and tooling](#language-and-tooling) for the full stack.

## How to work in this repo

### 1. Always seed from the vault

Before planning, researching, designing, or coding anything project-related, load context from the Obsidian vault at `vault/Weather Vault/`. The `vault-seed` skill does this automatically — prefer invoking it over manual reads. At minimum read:
1. `vault/Weather Vault/Project Scope.md`
2. `vault/Weather Vault/wiki/index.md`
3. Any entity or concept page relevant to the task

The vault is the project memory. It compounds across sessions. Never answer project questions from scratch when the vault has the answer.

### 2. Parallelize aggressively

Default to **parallel subagent fan-out + resolution step** when work can be decomposed. Use a single message with multiple Agent tool calls. Always end a fan-out with an explicit merge/synthesis step. Never serialize work that can run in parallel.

### 3. Ingest learnings into the vault

When research, design decisions, failures, or analyses produce durable knowledge, invoke `/ingest` to file it into the wiki. The `vault-scribe` subagent handles bookkeeping (summary, entity/concept updates, cross-references, log entry). This is how the knowledge base compounds.

### 4. Build cautiously with clean structure

This project is expected to grow significantly (data ingest, models, backtests, live trading). The user has explicitly flagged "don't let this devolve into ad-hoc scripts and scattered data" as a standing preference.

- Before adding a new category of artifact (data, scripts, models, notebooks), propose a folder convention and a README that documents it. Don't just dump files.
- Scripts should share a skeleton (common logging, idempotency, error handling) rather than each being a one-off.
- For large, slow, or irreversible actions (multi-GB downloads, deleting files, touching shared state): show the plan first, get explicit go-ahead, then act.
- Extend existing conventions over adding parallel ones.

### 5. Commit after milestones

When a discrete task, feature, milestone, or meaningful chunk of work is complete, **create a commit** rather than letting uncommitted changes accumulate into one mega-diff. Small, focused commits make git history useful as a debugging and context tool, and they protect against losing work to `git clean` or editor mishaps.

**Commit-worthy moments include:**

- Finishing a data download or transform script
- Completing a feature-extraction or alignment pipeline
- Landing a working baseline model or a meaningful eval improvement
- Substantial edits to `CLAUDE.md`, vault wiki content, or repo conventions
- A clean refactor or renaming pass
- Any change the user explicitly says is "done" or "good"

**Commit hygiene** (per the standard Claude Code commit flow):

- Prefer specific `git add <path>` over `git add -A` to avoid accidentally including secrets, `.env`, data files, or unrelated in-flight work.
- Commit messages should explain the *why*, not just the *what*. Use the HEREDOC + `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>` trailer pattern.
- **Never commit** `.env`, credentials, data files, model artifacts, or `pipeline-workspace/` contents.
- **Never** skip pre-commit hooks (`--no-verify`) or bypass signing unless the user explicitly asks.
- For hard-to-reverse git operations (`reset --hard`, force push, branch deletion), confirm with the user before running.
- Don't create commits the user didn't ask for — but **do** proactively offer to commit when a milestone is clearly complete, and explain what you'd stage and why.

### 6. Delete as you go — no stale code, no dead docs, no leftover patterns

**When you replace something, delete the old version in the same commit.** This project expects heavy iteration, and without aggressive cleanup the repo will rot into a graveyard of dead scripts, half-migrated patterns, and obsolete docs within a month. This rule is intentionally aggressive. **Err on the side of deletion — git history is the backup.**

**The rule:**

- When you introduce a new version of a script / module / skill / agent / convention / pattern, **delete the old one in the same commit**. Never the next commit, never "for now." No `_old`, `_v1`, `_deprecated`, `.bak`, or `prev_` variants in the working tree. Use `git log` / `git blame` / `git show <sha>:path` as the archive, not the filesystem.
- **No commented-out code blocks.** If you might want it back later, git has it.
- **No "TODO: remove this later" comments.** Remove it now or don't mention it. Future-you will not find the TODO.
- **No dead docs.** README, SKILL.md, CLAUDE.md, and vault pages must describe how things *currently* work. If an edit makes a doc section stale, update or delete that section in the same commit as the behavior change. A doc that lies is worse than no doc.
- **Delete unused imports, functions, variables, and classes as you find them.** Ruff catches most of these — run `uv run ruff check --fix .` after any substantial edit.
- **When a convention is superseded, scrub references to the old one everywhere.** Grep for it. If the new convention is "downloaders live in `scripts/download/<source>/script.py`", no doc should still mention bash versions at the root.
- **When a skill, subagent, or vault page becomes obsolete, delete it.** Stale skills and agents are strictly worse than no skills — they mislead future sessions.

**The cleanup pass** — run at the end of any non-trivial change:

1. **Grep for references** to whatever you removed: function names, file paths, old flag names, old config keys, old module names. Any orphaned reference → delete or fix.
2. **Run `uv run ruff check .`** across the repo. New `F401` (unused import), `F841` (unused variable), or `RUF100` (unused `noqa`) are cleanup targets introduced by your change.
3. **Scan changed files for commented-out code, TODO comments, stub functions, and "temporary" hacks.** Delete or promote — never leave for later.
4. **Ask yourself: "what did I just make obsolete that I haven't deleted yet?"** Make the list. Delete everything on it in the same commit as the new work.

**When in doubt: delete.** If a deletion turns out to be premature, `git revert <sha>` or `git show <sha>:path > path` restores it — a cheap round trip. A repo full of fossils is expensive to fix later, and every stale thing makes the next cleanup pass bigger. Default posture: aggressive removal, not cautious accumulation.

## Data conventions

- Data lives in `data/` which is **gitignored** (`data/*` + `!data/README.md`). See [`data/README.md`](data/README.md) — it is the authoritative source for layout and conventions.
- Layout: `data/raw/<source>/` (immutable originals with `MANIFEST.json` + `download.log`), `data/interim/<step>/` (cleaned/decoded), `data/processed/<task>/` (model- or backtest-ready artifacts). Never hand-edit `raw/`.
- Every `raw/<source>/` needs a `MANIFEST.json` (schema in `data/README.md`) and download scripts must be idempotent — check `status: complete` and skip if so.
- Downloads go through a tracked script under `scripts/download/<source>.sh` that tees stdout to `download.log`.
- Never commit GRIB2, Parquet, CSV, NetCDF, or other data files — only tracked scripts and the README.
- HRRR access via Herbie (`pip install herbie-data`). Byte-range subset, never full-domain downloads.
- Ground truth from IEM ASOS 1-min (airport station) unless explicitly overridden.
- **Time-based splits only.** Never random train/test splits — weather has strong autocorrelation and random splits give fraudulent metrics.
- Alignment of HRRR forecast-valid-time to ASOS observation-time must be strictly causal — no future information in features.
- All timestamps UTC internally. Convert to local only at the market-resolution boundary.

## Model conventions

- Probabilistic outputs preferred over point forecasts. `P(high > threshold)` is the target shape.
- Calibration is evaluated separately from accuracy (reliability curves, Brier score, log loss).
- HRRR ensemble (HRRRx, 36 members) provides a free empirical distribution — use it.
- See `.claude/skills/model-training/SKILL.md` for the full conventions.

## Language and tooling

**Python is the primary language for this project.** The weather and ML ecosystems are Python-first — Herbie (HRRR), xarray/cfgrib (GRIB2), metar, arm-pyart (NEXRAD), SynopticPy, scikit-learn, xgboost, lightgbm, statsmodels are all Python-only or Python-dominant. Committing to Python removes an entire class of subprocess-bridge headaches.

### The stack

| Concern | Tool | Why |
|---|---|---|
| Package manager + venv | **uv** | fast, unified, handles Python toolchain too |
| Python version | **3.13** (pinned via `.python-version`) | latest stable during project start; uv can install it |
| Project config | **`pyproject.toml`** | single source of truth for deps, ruff, pyright, pytest, uv |
| Linter + formatter | **ruff** | one tool, fast, replaces flake8/black/isort |
| Static type checker | **pyright** | strict mode available; pragmatic defaults for weather libs w/o stubs |
| Test runner | **pytest** | standard; `pythonpath = ["."]` lets any top-level folder be importable |
| CLI framework | **typer** | typed, ergonomic, built on Click |
| Terminal UX | **rich** + **tqdm** | readable output, progress bars |
| Structured logging | **structlog** | project convention per `scripts/README.md` |
| Notebooks | **Marimo** | reactive, git-friendly `.py` files, runs as script or web app — see [Notebooks](#notebooks--marimo-as-first-class-research-surface) |

### Running things

```sh
uv sync                       # create .venv and install deps (run once, or after pyproject.toml changes)
uv add <pkg>                  # add a runtime dep to pyproject.toml and install
uv add --group dev <pkg>      # add a dev dep
uv remove <pkg>                # remove
uv run python scripts/foo.py  # run a script
uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format .          # format
uv run pyright                # type check
```

**Always** use `uv run` to execute Python code — it ensures the right venv is active without needing to `source .venv/bin/activate`.

### Notebooks — Marimo as first-class research surface

**Use [Marimo](https://marimo.io/), not Jupyter.** Marimo notebooks are reactive Python files (`.py`, not JSON) that:

- **Re-run dependent cells automatically** when you edit something — no stale-cell bugs where `cell 4` disagrees with `cell 7` because you ran them out of order
- **Live as real Python files** — git-friendly diffs, lintable by ruff, type-checkable by pyright, importable from other code
- **Run as scripts** (`uv run python notebooks/foo.py`) — the reactive DAG just runs top-to-bottom
- **Run as interactive web apps** (`uv run marimo run notebooks/foo.py`) — shareable dashboards without leaving the notebook file
- **Edit in the browser** (`uv run marimo edit notebooks/foo.py`) — live-reloading reactive editor

Notebooks live in `notebooks/` at the repo root. Naming conventions, graduation path, and anti-patterns are in [`notebooks/README.md`](notebooks/README.md).

**Commands:**

```sh
uv run marimo edit notebooks/foo.py    # edit in browser (reactive)
uv run marimo new notebooks/foo.py     # scaffold a new notebook
uv run python notebooks/foo.py         # run end-to-end as a script
uv run marimo run notebooks/foo.py     # serve as an interactive web app
```

**When to use a notebook:**

- Exploratory data analysis — loading DuckDB or polars over `data/` and poking around
- Schema discovery on new datasets (e.g. Phase 0 weather-subset filtering against `data/raw/prediction_market_analysis/`)
- Validation — sanity-checking an alignment, a pipeline output, or a join key
- Calibration and diagnostic checks on forecasts vs observations
- Model experimentation before committing to a training script
- Any "I want to see this" moment where interactivity beats a script

**When NOT to use a notebook:**

- Production pipelines → `scripts/`
- Reusable utilities → graduate to a module that scripts can import
- Anything that needs to run unattended on a schedule

### Code layout philosophy — no forced `src/` dogma

This project does NOT have a single monolithic `src/weather/` package. Instead:

- **Scatter `.py` files across whatever top-level folder makes sense for the job**: `scripts/`, `experiments/`, `analysis/`, `notebooks/`, etc.
- **The repo root is on `pythonpath`** (via `[tool.pytest.ini_options]` and uv's implicit behavior), so any top-level folder can be imported as a namespace package.
- **Shared utilities graduate into a small package at the root** (e.g. `weatherlib/` or `common/`) only once they're actually reused — not preemptively.
- Per `scripts/README.md`: scripts stay in `scripts/` until they graduate into a library. Don't pre-create the library.

### Type hint discipline

- **Use type hints on public functions and module-level APIs.** Leave obvious-trivial locals untyped.
- `pyright` is configured in `standard` mode with unknown-member/argument noise silenced for weather libraries. Tighten per-file with `# pyright: strict` at the top of files you want held to a higher bar.
- If you import a weather library and pyright complains about missing stubs, do not add ignores everywhere — just keep the pragmatic global setting.

## Directory layout

```
weather/
├── CLAUDE.md                     # this file
├── .claude/
│   ├── settings.json             # project tool permissions
│   ├── agents/                   # subagent definitions (vault-scribe, weather-data-expert)
│   └── skills/                   # skill definitions (vault-*, weather-data, model-training)
├── vault/Weather Vault/          # Obsidian vault — first-class project surface
│   ├── Project Scope.md          # canonical scoping doc
│   ├── wiki/                     # LLM-maintained synthesis (Karpathy pattern)
│   │   ├── index.md              # catalog
│   │   ├── log.md                # chronological
│   │   ├── entities/             # airports, markets, providers, competitors
│   │   ├── concepts/             # HRRR, MOS, calibration, Kelly, ensemble spread
│   │   └── syntheses/            # cross-source analyses
│   └── raw-sources/              # immutable source material
│       ├── chats/                # Claude/ChatGPT/Cursor research conversations
│       ├── articles/             # web clippings
│       ├── papers/               # academic papers
│       └── notes/                # hand-written notes
├── data/                         # gitignored: raw/ | interim/ | processed/ (see data/README.md)
├── scripts/                      # tracked scripts (downloads, one-offs, utilities)
├── notebooks/                    # Marimo reactive notebooks (research, analysis, validation)
├── pyproject.toml                # deps + ruff + pyright + pytest config
├── .python-version               # pinned Python (3.13)
└── .venv/                        # gitignored, managed by uv
```

## Safety

- Never commit `.env`, credentials, API keys. Use `.env.example` for templates.
- Never run destructive git operations (`reset --hard`, force push, branch deletion) without explicit approval.
- Never skip hooks or bypass signing unless explicitly requested.
- When in doubt, ask before acting.

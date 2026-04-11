# CLAUDE.md — Weather Prediction Markets Project

This repo is a solo quantitative trading project targeting **prediction-market weather contracts** on Kalshi and Polymarket at major US airports. It is NOT a generic weather-forecasting product. The target metric is **edge vs market-implied probability**, not RMSE vs TAF.

Start every non-trivial session by reading [vault/Weather Vault/Project Scope.md](vault/Weather%20Vault/Project%20Scope.md) and [vault/Weather Vault/wiki/index.md](vault/Weather%20Vault/wiki/index.md).

## Core principles

- **Trading, not forecasting.** Probabilistic outputs (`P(high > threshold)`) and calibration matter more than point forecasts. Only trade when `|edge| > transaction costs`.
- **CONUS-first.** HRRR covers CONUS only. Shanghai and international markets need a different stack and are out of scope for v1.
- **Airports-specific.** Models are trained per-airport on that station's ground truth (IEM ASOS 1-min). Local microclimate patterns are where alpha lives.
- **Real-time pipeline is load-bearing.** Core alpha comes from reacting to new HRRR runs within a 15–45 min window before the market reprices.
- **Python-first, everywhere.** Every script, data ingest, feature pipeline, model, backtest, analysis, and real-time component in this repo is Python via **`uv`**. The weather (Herbie, xarray, cfgrib, metar, arm-pyart, SynopticPy) and ML (scikit-learn, xgboost, lightgbm, statsmodels) ecosystems are Python-dominant — no TypeScript or Node in this repo. Bash is permitted only as a `subprocess` invocation from inside a Python script (e.g. shelling out to `aria2c`, `curl`, `zstd`, `tar`) — never as a top-level entry point. See [Language and tooling](#language-and-tooling) for the full stack.

## How to work in this repo

### 1. Always seed from the vault

Before planning, researching, designing, or coding anything project-related, load context from the Obsidian vault at `vault/Weather Vault/`. The `vault-seed` skill does this automatically — prefer invoking it over manual reads. At minimum read:
1. `vault/Weather Vault/Project Scope.md`
2. `vault/Weather Vault/wiki/index.md`
3. Any entity or concept page relevant to the task

The vault is the project memory. It compounds across sessions. Never answer project questions from scratch when the vault has the answer.

### 2. Parallelize aggressively

Default to **parallel subagent fan-out + resolution step** when work can be decomposed. Use a single message with multiple Agent tool calls. Always end a fan-out with an explicit merge/synthesis step. Never serialize work that can run in parallel.

### 3. Capture knowledge into the vault as you produce it

The vault is the project's persistent memory. Every piece of durable knowledge the project produces should end up there — as structured entity / concept / synthesis pages under `vault/Weather Vault/wiki/`, not as prose README files.

Every new piece of durable knowledge — a data source added, a schema gotcha discovered, an architectural decision made, a failure diagnosed, a phase milestone completed — is captured via [`vault-capture`](.claude/skills/vault-capture/SKILL.md). The skill writes structured pages under `wiki/entities/`, `wiki/concepts/`, or `wiki/syntheses/`, updates `wiki/index.md`, and appends to `wiki/log.md`. External raw-source files dropped into `raw-sources/` (chats, articles, papers) get summarized into a synthesis page inline — no separate ingest skill needed.

**Invoke capture proactively and often.** A session that produces durable knowledge and doesn't write it down has failed at Rule 3 — future sessions will re-derive the same conclusions cold. The knowledge base only compounds if we actually deposit into it.

**Automated reminders (structural enforcement via hooks):**

- **`SessionStart` hook** at `.claude/hooks/vault_health.py` prints a vault-repo alignment report every time a session starts — counts of entities / concepts / syntheses, count of `scripts/<source>/` folders, the gap, and the last `wiki/log.md` entry. You see the backlog immediately and can close it opportunistically as you work.
- **`PostToolUse` hook** at `.claude/hooks/vault_capture_reminder.py` fires after `Bash` tool calls (targets `git commit` invocations specifically). If the just-committed change touched `scripts/` but nothing under `vault/`, it prints a reminder to capture. Non-blocking — surfaces the reminder in session context without failing anything.

Both hooks are wired in `.claude/settings.json`. They're advisory (always exit 0), never block a session or a commit. Their purpose is to make the vault-repo gap visible so it stays top-of-mind, not to enforce by failure.

### 4. Build cautiously with clean structure

This project is expected to grow significantly (data ingest, models, backtests, live trading). The user has explicitly flagged "don't let this devolve into ad-hoc scripts and scattered data" as a standing preference.

- Before adding a new category of artifact (data, scripts, models, notebooks), propose a folder convention and document it in `CLAUDE.md` or a `.claude/skills/<name>/SKILL.md`. **Never via a subdirectory `README.md`** — see the [`minimal-docs`](.claude/skills/minimal-docs/SKILL.md) skill.
- Every data script follows the canonical contract in [`.claude/skills/data-script/SKILL.md`](.claude/skills/data-script/SKILL.md). Copy `.claude/skills/data-script/template.py`; don't reinvent CLI flags, manifest lifecycle, or logging.
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
- **Never commit** `.env`, credentials, data files, or model artifacts.
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
- **When a convention is superseded, scrub references to the old one everywhere.** Grep for it. If the new convention is "every data source lives at `scripts/<source>/` with stages as files", no doc should still reference the old `scripts/download/` / `scripts/transform/` layout.
- **When a skill, subagent, or vault page becomes obsolete, delete it.** Stale skills and agents are strictly worse than no skills — they mislead future sessions.

**The cleanup pass** — run at the end of any non-trivial change:

1. **Grep for references** to whatever you removed: function names, file paths, old flag names, old config keys, old module names. Any orphaned reference → delete or fix.
2. **Run `uv run ruff check .`** across the repo. New `F401` (unused import), `F841` (unused variable), or `RUF100` (unused `noqa`) are cleanup targets introduced by your change.
3. **Scan changed files for commented-out code, TODO comments, stub functions, and "temporary" hacks.** Delete or promote — never leave for later.
4. **Ask yourself: "what did I just make obsolete that I haven't deleted yet?"** Make the list. Delete everything on it in the same commit as the new work.

**When in doubt: delete.** If a deletion turns out to be premature, `git revert <sha>` or `git show <sha>:path > path` restores it — a cheap round trip. A repo full of fossils is expensive to fix later, and every stale thing makes the next cleanup pass bigger. Default posture: aggressive removal, not cautious accumulation.

### 7. Communicate in caveman-full by default

**Default communication style for this repo is [caveman-full](.claude/skills/caveman/SKILL.md)** — terse, fragment-heavy, ~75% fewer output tokens while keeping full technical accuracy. The canonical ruleset lives in `.claude/skills/caveman/SKILL.md` (vendored from [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman), MIT). Summary:

- **Drop** articles (a/an/the), filler (just/really/basically/actually/simply), pleasantries (sure/certainly/happy to), hedging.
- **Fragments OK.** Short synonyms ("big" not "extensive", "fix" not "implement a solution for").
- **Technical terms exact. Code blocks unchanged. Error messages quoted verbatim.**
- **Pattern:** `[thing] [action] [reason]. [next step].`

**Auto-clarity exceptions — revert to normal prose for:**

- **Security warnings** and **irreversible / destructive action confirmations** (`git reset --hard`, force push, `rm -rf`, dropping tables, deleting branches)
- **Multi-step sequences** where fragment order risks misread
- **User confused** or explicitly asking for more detail
- **Commit messages, PR descriptions, code comments, vault/wiki content, decision docs, planning docs** — write in normal prose
- **Data / schema / column definitions** where ambiguity would be costly

**Override on request:** `/caveman lite` | `/caveman full` | `/caveman ultra` | `stop caveman` | `normal mode`. Default intensity is `full`. Level persists until changed or session end.

### 8. Worktree-first — every session works in a git worktree

**Default to a git worktree for all non-trivial work.** This repo is expected to run parallel Claude sessions, and the worst failure mode is two sessions thrashing on the main checkout at the same time. Worktrees eliminate that: each session gets its own working tree + branch, the `.git/` database is shared, merges happen at the end. The [`worktree-first`](.claude/skills/worktree-first/SKILL.md) skill has the full workflow, commands, and conventions.

**The rule — every change in a worktree; every worktree's `data/` is a symlink into main's `data/`:**

- **Every Claude session that changes files should be working in a worktree**, not the main checkout. Two ways:
    1. **`Agent` tool with `isolation: "worktree"`** — ephemeral, cleanest for bounded subagent tasks. Claude Code creates + cleans up automatically.
    2. **Manual worktree with a `data/` symlink to main** — longer-lived, for work spanning multiple commits. **Canonical 3-line creation** from the main checkout:
       ```sh
       git worktree add ../weather-wt/<name> -b wt/<name>
       mkdir -p data                                            # ensure main's data/ exists
       ln -sfn "$(pwd)/data" ../weather-wt/<name>/data          # symlink wt/data → main/data
       ```
       Branch convention: `wt/<purpose>`. Path convention: `../weather-wt/<name>` (sibling to main repo, outside its tree).
- **`data/` is shared across all worktrees via the symlink.** Every downloader writes to main's `data/` regardless of which worktree ran the script — scripts compute `REPO_ROOT / "data"` and the symlink resolves transparently. **No data duplication on disk. No porting at cleanup. No risk of losing downloaded data when a worktree is removed.** Code changes still flow through git normally (commit in worktree → merge to main); only `data/` short-circuits via the symlink.
- **Never `rm -rf <wt>/data/*`** or glob-delete inside the symlinked `data/` — it deletes files from main's `data/`. Safe cleanup is `git worktree remove <wt>`, which unlinks the symlink without touching the target.
- **Worktree lifecycle — commit, merge, clean up (always in this order):**
    1. Commit every change to the worktree's branch (`wt/<purpose>`). **Never commit to master from inside the worktree.** Let the merge do that.
    2. When the worktree's work is fully done, from the main checkout: `git merge --ff-only wt/<name>`. Prefer fast-forward. Rebase the branch onto master first if it has diverged.
    3. `git worktree remove ../weather-wt/<name>` — safe: the symlink goes, main's `data/` stays.
    4. `git branch -d wt/<name>` — delete the merged branch.
    5. Verify: `git worktree list`, `git log --oneline -3`, and check that the new data is visible in main's `data/` (it always is — the symlink made it land there during the download).
- **Commit discipline inside the worktree:**
    - Commit at each milestone per Rule 5. Multiple small commits in the branch are fine; they squash or fast-forward at merge time.
    - A worktree may span multiple Claude sessions — keep committing to the branch, don't merge incrementally.
    - Fast-forward only. If master has moved and a fast-forward isn't possible, rebase the worktree branch onto master, resolve conflicts in the worktree, then try the fast-forward again. No merge commits unless justified.
- **Read-only operations** (questions, vault queries, `git log`, lint checks, running tests that don't mutate tracked files) can happen on the main checkout without a lock or worktree.
- **Trivial edits carveout:** single-file changes under ~20 lines, typo fixes, and doc corrections can skip the worktree *if* the `.main-repo-lock` is not held. Anything bigger → worktree.

**When you must use the main checkout — the `.main-repo-lock` file:**

Some operations genuinely need to run against the main checkout (coordinating a merge, cleaning up uncommitted state, something that depends on main-repo working-tree state). In that case:

1. **Check for the lock first:** `cat .main-repo-lock` at repo root. If present → don't touch the main checkout, use a worktree instead.
2. **Acquire the lock** before doing any main-checkout work:
   ```sh
   cat > .main-repo-lock <<EOF
   {"session": "<short-identifier>", "acquired_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "reason": "<one-line why>"}
   EOF
   ```
3. **Release the lock** (`rm -f .main-repo-lock`) the instant your main-checkout work is done — even on error. Wrap in `trap 'rm -f .main-repo-lock' EXIT` in bash, or a try/finally equivalent elsewhere.
4. **Lock scope is minimal** — hold it only while actively editing main-checkout files. Release before long operations (downloads, model training, anything > ~1 minute).
5. **Lock is advisory, not enforced.** It's a coordination hint between cooperating sessions, not a security primitive. Respect other sessions' locks; don't unilaterally steal a stale one — check with the user first.

The lock file is gitignored (`.main-repo-lock` in `.gitignore`); it's runtime state, not a repo artifact.

**Default posture:** use a worktree. Lock the main checkout only when you actually cannot.

## Data conventions

- Data lives in `data/` which is **gitignored in its entirety**. Layout: `data/raw/<source>/` (immutable originals with `MANIFEST.json` + `download.log`), `data/interim/<step>/` (cleaned / decoded intermediates), `data/processed/<task>/` (model- or backtest-ready artifacts).
- **Every `data/raw/<source>/` MUST have a `MANIFEST.json` (schema v1).** The canonical schema, idempotency rules, required CLI flags, and full download-script contract live in [`.claude/skills/data-script/SKILL.md`](.claude/skills/data-script/SKILL.md). Copy [`.claude/skills/data-script/template.py`](.claude/skills/data-script/template.py) for every new source — it's the single canonical skeleton.
- **Never hand-edit anything in `data/raw/`.** If you need a transformation, write `scripts/<source>/transform.py` that reads from `raw/` and emits to `data/interim/` or `data/processed/`.
- **Source-first layout with stages as files:** every data source is one folder at `scripts/<source>/`. Inside: `download.py` (stage 1, upstream → `data/raw/<source>/`), optional `transform.py` (stage 2, `raw/` → `data/{interim,processed}/`), optional `validate.py` (post-run sanity check). A source may have one, two, or all three stage files. No `scripts/download/` or `scripts/transform/` top-level folders.
- **Never commit** GRIB2, Parquet, CSV, NetCDF, or other data files — only tracked scripts.
- **Slug-catalog carveout:** `weather-market-slugs/polymarket.csv` is the one committed CSV exception — small (~8 MB), semi-permanent, source-of-truth identifier list that every downstream script depends on. Kept as plain CSV at the repo root. No separate README.
- HRRR access via Herbie (`pip install herbie-data`). Byte-range subset, never full-domain downloads.
- Ground truth from IEM ASOS 1-min (airport station) unless explicitly overridden.
- **Time-based splits only.** Never random train/test splits — weather has strong autocorrelation and random splits give fraudulent metrics.
- Alignment of HRRR forecast-valid-time to ASOS observation-time must be strictly causal — no future information in features.
- All timestamps UTC internally. Convert to local only at the market-resolution boundary.

## Model conventions

- Probabilistic outputs preferred over point forecasts. `P(high > threshold)` is the target shape.
- Calibration is evaluated separately from accuracy (reliability curves, Brier score, log loss).
- HRRR ensemble (HRRRx, 36 members) provides a free empirical distribution — use it.
- **Time-based splits only.** Never random train/test splits on time-series data.

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
| Structured logging | **structlog** (available) / stdlib `logging` (default) | stdlib `logging.Formatter` is the data-script convention; `structlog` when structured JSON context helps |
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

Notebooks live in `notebooks/` at the repo root. Name them with a category prefix: `expl_` (exploration), `val_` (validation), `calib_` (calibration), `diag_` (diagnostic), `train_` (model experimentation). Graduate to a script under `scripts/` once the notebook runs more than once a week and reactivity is getting in the way.

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
- Data scripts stay in `scripts/<source>/` until they graduate into a library. Don't pre-create the library.

### Type hint discipline

- **Use type hints on public functions and module-level APIs.** Leave obvious-trivial locals untyped.
- `pyright` is configured in `standard` mode with unknown-member/argument noise silenced for weather libraries. Tighten per-file with `# pyright: strict` at the top of files you want held to a higher bar.
- If you import a weather library and pyright complains about missing stubs, do not add ignores everywhere — just keep the pragmatic global setting.

## Directory layout

```
weather/
├── CLAUDE.md                     # this file — project rules
├── README.md                     # minimal repo entry point
├── .claude/
│   ├── settings.json             # project tool permissions + hook registration
│   ├── hooks/                    # vault_health (SessionStart) + vault_capture_reminder (PostToolUse)
│   └── skills/                   # data-script, data-validation, minimal-docs, vault-capture, vault-seed, weather-data, worktree-first, caveman
├── vault/Weather Vault/          # Obsidian vault — first-class project surface
│   ├── Project Scope.md          # canonical scoping doc
│   ├── Execution Stack — Source Review.md   # execution-stack decision doc
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
├── data/                         # gitignored — raw/ | interim/ | processed/
├── scripts/<source>/             # one folder per source; stages as files inside
│   ├── download.py               # stage 1: upstream → data/raw/<source>/
│   ├── transform.py              # stage 2 (optional): raw → data/{interim,processed}/
│   └── validate.py               # optional post-run sanity check
├── notebooks/                    # Marimo reactive notebooks (expl_, val_, calib_, ...)
├── weather-market-slugs/         # committed slug catalogs (carveout from no-CSV rule)
├── pyproject.toml                # deps + ruff + pyright + pytest config
├── .python-version               # pinned Python (3.13)
└── .venv/                        # gitignored, managed by uv
```

## Safety

- Never commit `.env`, credentials, API keys. Use `.env.example` for templates.
- Never run destructive git operations (`reset --hard`, force push, branch deletion) without explicit approval.
- Never skip hooks or bypass signing unless explicitly requested.
- When in doubt, ask before acting.

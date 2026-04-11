# CLAUDE.md — Weather Prediction Markets Project

This repo is a solo quantitative trading project targeting **prediction-market weather contracts** on Kalshi and Polymarket at major US airports. It is NOT a generic weather-forecasting product. The target metric is **edge vs market-implied probability**, not RMSE vs TAF.

Start every non-trivial session by reading [vault/Weather Vault/Project Scope.md](vault/Weather%20Vault/Project%20Scope.md) and [vault/Weather Vault/wiki/index.md](vault/Weather%20Vault/wiki/index.md).

## Core principles

- **Trading, not forecasting.** Probabilistic outputs (`P(high > threshold)`) and calibration matter more than point forecasts. Only trade when `|edge| > transaction costs`.
- **CONUS-first.** HRRR covers CONUS only. Shanghai and international markets need a different stack and are out of scope for v1.
- **Airports-specific.** Models are trained per-airport on that station's ground truth (IEM ASOS 1-min). Local microclimate patterns are where alpha lives.
- **Real-time pipeline is load-bearing.** Core alpha comes from reacting to new HRRR runs within a 15–45 min window before the market reprices.

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
└── src/weather/                  # Python package (to be created)
```

## Safety

- Never commit `.env`, credentials, API keys. Use `.env.example` for templates.
- Never run destructive git operations (`reset --hard`, force push, branch deletion) without explicit approval.
- Never skip hooks or bypass signing unless explicitly requested.
- When in doubt, ask before acting.

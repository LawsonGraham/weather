# weather

Quantitative trading on prediction-market weather contracts (Kalshi, Polymarket) at major US airports.

This is a solo project. It is **not** a generic weather forecasting product — the target metric is *edge vs market-implied probability*, not RMSE vs TAF.

See [`CLAUDE.md`](CLAUDE.md) for project conventions and the working rules Claude Code follows when operating in this repo.
See [`vault/Weather Vault/Project Scope.md`](vault/Weather%20Vault/Project%20Scope.md) for the canonical scoping doc.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv can install Python for you).

```sh
uv sync               # create .venv and install runtime + dev deps
cp .env.example .env  # fill in API keys as needed
```

## Running things

```sh
uv run python scripts/some_script.py    # run a script
uv run pytest                            # run tests
uv run ruff check .                      # lint
uv run ruff format .                     # format
uv run pyright                           # type check
```

## Layout

- `CLAUDE.md` — project conventions
- `vault/Weather Vault/` — Obsidian vault (Karpathy LLM Wiki pattern); project memory
- `data/` — gitignored data tree (`raw/`, `interim/`, `processed/`); see `data/README.md`
- `scripts/` — standalone CLI tasks (downloads, transforms, ops); see `scripts/README.md`
- `.claude/` — Claude Code project config (agents, skills, settings)

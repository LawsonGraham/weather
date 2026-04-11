# `notebooks/` — Marimo research surface

First-class home for exploratory analysis, validation, diagnostics, calibration checks, and model experimentation.

**Tool: [Marimo](https://marimo.io/)**. NOT Jupyter. Marimo notebooks are reactive `.py` files — git-friendly, lintable, type-checkable, and runnable as either scripts or interactive web apps. See [CLAUDE.md § Notebooks](../CLAUDE.md#notebooks--marimo-as-first-class-research-surface) for the "why" and the full list of commands.

## Commands (quick reference)

```sh
uv run marimo edit notebooks/foo.py    # edit in browser
uv run marimo new notebooks/foo.py     # scaffold a new notebook
uv run python notebooks/foo.py         # run end-to-end as a script
uv run marimo run notebooks/foo.py     # serve as an interactive web app
```

## Naming convention

Use a **category prefix** + descriptive name:

| Prefix | Purpose | Example |
|---|---|---|
| `expl_` | Exploration — open-ended poking at a new dataset | `expl_prediction_market_dataset.py` |
| `val_` | Validation — sanity-check a pipeline, alignment, or schema | `val_hrrr_asos_alignment.py` |
| `calib_` | Calibration analysis — reliability curves, Brier, mispricing | `calib_weather_markets_kalshi.py` |
| `diag_` | Diagnostic — investigate a specific failure, outlier, or regression | `diag_klax_marine_layer_bias.py` |
| `train_` | Model experiments not yet promoted to a training script | `train_xgb_klax_daily_high.py` |

Optionally prefix with `YYYY-MM-DD_` for strictly time-bound exploratory work that shouldn't be reused (e.g. `2026-04-10_expl_foo.py`).

## Import rules

Notebooks can import from anywhere in the repo — the repo root is on `pythonpath` (see `[tool.pytest.ini_options]` in `pyproject.toml`).

```python
import polars as pl
import duckdb
# Once scripts/download/_common.py exists:
from scripts.download._common import load_manifest
```

## Data access

- Read freely from `data/raw/`, `data/interim/`, `data/processed/`.
- **Never mutate `data/raw/`** from a notebook. It's immutable.
- **DuckDB over Parquet** is the preferred query pattern — no need to load the whole dataset into memory:
  ```python
  import duckdb
  con = duckdb.connect()
  con.sql(
      "SELECT * FROM 'data/raw/prediction_market_analysis/kalshi/**/*.parquet' LIMIT 10"
  ).pl()
  ```

## Output rules

- **Persistent artifacts** (charts, Parquet summaries, aggregated tables) should be written to `data/interim/<notebook_slug>/` or `data/processed/<task>/`, **not** inline in the notebook.
- The notebook file itself is the analysis record; outputs are reproducible from it.
- `data/` is gitignored — artifacts never get committed. Only the notebook does.

## Graduation path

```
ad-hoc notebook (notebooks/expl_foo.py)
    ↓  becomes a repeatable question
evergreen notebook (notebooks/calib_weather_markets.py)
    ↓  runs more than once a week / reactivity is in the way
CLI script (scripts/transform/foo.py)
    ↓  imported from multiple places
module (scripts/... or a root-level package)
```

Don't rush it. A notebook that lives for weeks as a notebook is fine. Graduate only when you're re-running it routinely and the interactivity has become a cost rather than a feature.

## Anti-patterns

- **Mega-notebooks** trying to be "the analysis doc" for a whole phase. One question per notebook.
- **Secrets in notebooks.** Read from `.env` via `pydantic-settings` or `python-dotenv`, same as scripts.
- **Mutating `data/raw/`** from a notebook.
- **Production logic hiding in a notebook.** If other code regularly imports from a notebook, move that code to a script or module.
- **Unchecked state.** Marimo is reactive, so this is less of a problem than in Jupyter, but: avoid global singletons, avoid hidden side-effects in import-time code.

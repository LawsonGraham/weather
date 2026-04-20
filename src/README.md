# src/

Installable Python packages. This is the canonical location for any code that
graduates from exploratory notebooks to production.

## Layout

```
src/
├── lib/                        # shared libraries, reusable across strategies
│   ├── polymarket/             # CLOB client, auth, order placement
│   └── weather/                # forecast loaders, consensus helpers
│
└── <strategy_name>/            # one folder per deployable strategy
    ├── STRATEGY.md             # design doc (thesis, signal, backtest, risks)
    ├── strategy.py             # signal generation (data in → recommendations out)
    ├── cli.py                  # command-line entry point (subcommand pattern)
    └── backtest.py             # reproduces historical stats in STRATEGY.md
```

## Active strategies

- [`consensus_fade_plus1/`](consensus_fade_plus1/STRATEGY.md) — fade retail's
  over-pricing of the bucket one above NBS favorite when all three weather
  forecasts agree. Buy NO, 98.9% hit, +$0.083/trade in backtest. Paper-trade status.
  - Entry point: `uv run cfp {setup,recommend,submit,cancel-all,status}`

## Conventions

- Strategies IMPORT from `lib/` — they don't re-implement forecast loading or
  CLOB plumbing. The strategy file is pure signal logic.
- Each strategy ships a `cli.py` with at least these subcommands:
  - `setup` (if one-time wallet/auth setup is required)
  - `recommend` (dry-run / display recommendations, no order submission)
  - `submit` (place orders, supports `--dry-run`)
  - `cancel-all` (emergency halt)
  - `status` (open orders, balance)
- CLI entry points are registered in `pyproject.toml` under `[project.scripts]`.

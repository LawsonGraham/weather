# Strategies

Deployable trading strategies for weather prediction markets. Each strategy has
its own folder with a `STRATEGY.md` design doc, `recommender.py` for live
use, and `backtest.py` for reproducibility.

Contents of each strategy folder:

- `STRATEGY.md` — thesis, signal definition, execution rules, expected performance, risks
- `recommender.py` — given today's data, emits recommended trades
- `backtest.py` — reproduces the historical backtest stats in the design doc

Strategy folders are organized by edge, not by asset or timeframe.

## Active strategies

- [`consensus_fade_plus1/`](consensus_fade_plus1/STRATEGY.md) — fade retail's
  over-pricing of the bucket one above NBS favorite when all three weather
  forecasts agree. Buy NO, 98.9% hit, +$0.083/trade in backtest. Paper-trade status.

## Retracted / inactive

- **Strategy D V1** (`scripts/polymarket_weather/live_recommender.py`, deleted
  2026-04-15) — retracted after v2 backtest showed +$1.94/trade claim was a
  period+city-specific artifact that doesn't replicate in clean temporal
  holdout. See `vault/Weather Vault/wiki/syntheses/2026-04-14 Strategy D does
  NOT replicate in clean temporal holdout.md`.

## Relationship to the rest of the repo

- **Data scripts** (`scripts/<source>/`) download + transform the raw feeds
  (NBS, GFS MOS, HRRR, METAR, Polymarket prices + book)
- **Strategies** (`strategies/<name>/`) consume the processed data to generate
  trade recommendations
- **Exploratory notebooks** (`notebooks/experiments/<topic>/`) are where
  strategies get discovered and stress-tested before graduating here

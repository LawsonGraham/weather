"""Weather forecast access for trading strategies.

Reads the processed feature parquet at
``data/processed/backtest_v3/features.parquet`` (produced by
``notebooks/experiments/backtest-v3/build_features.py``) and exposes
a simple API for querying NBS / GFS MOS / HRRR daily-max forecasts per
(city, local_date).
"""
from lib.weather.consensus import consensus_spread
from lib.weather.forecasts import DailyForecast, get_forecast, load_features_for_date

__all__ = ["DailyForecast", "consensus_spread", "get_forecast", "load_features_for_date"]

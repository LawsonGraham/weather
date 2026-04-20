# Watchers

Persistent background pollers that keep `data/processed/` fresh so the
strategy engine always has up-to-date inputs.

## What each one does

| Watcher | Interval | What it pulls | Script it wraps |
|---|---|---|---|
| `NBSWatcher` | 10 min | NBS text forecasts (NBM, 4x/day native) | `scripts/iem_mos/download.py --models NBS` |
| `GFSWatcher` | 10 min | GFS MOS text forecasts (4x/day native) | `scripts/iem_mos/download.py --models GFS` |
| `HRRRWatcher` | 10 min | HRRR hourly grids, subset to airport points | `scripts/hrrr/download.py --fxx 6` |
| `METARWatcher` | 5 min | METAR observations (catches SPECIs) | `scripts/iem_metar/download.py` |
| `MarketsWatcher` | 15 min | Polymarket slug catalog + market metadata | `scripts/polymarket_weather_slugs/download.py` + `polymarket_weather/{download,transform}.py` |
| `FeaturesWatcher` | 10 min | Rebuilds unified `features.parquet` | `notebooks/experiments/backtest-v3/build_features.py` |

## Storage

State at `data/processed/watchers/<name>.state.json` — persists across
restarts so a freshly-started daemon knows when the last successful run was
and doesn't re-poll pointlessly.

## Correctness notes

- **`iem_mos` downloader writes ONE CSV per (station, model) for the full
  requested range**, and `--force` overwrites. So NBS/GFS watchers pull the
  FULL history (Nov 30 → today) every 10 min. The pull is ~40 MB from IEM's
  cache and takes ~30 seconds. Correctness > bandwidth.

- **HRRR downloader is incremental** (reads existing parquet, skips cycles
  already present), so a rolling 2-day window is safe.

- **METAR downloader is per-month**: skips fully-complete past months,
  re-fetches the current (partial) month. So we only ask for the current
  month on each poll.

- **Polymarket markets refresh is a 3-step chain**: slug catalog →
  per-slug Gamma+Goldsky pull → transform. Wrapped as a single watcher.

## Usage

```sh
uv run cfp daemon       # start all watchers
uv run cfp watchers     # show last-poll status of each
```

Use `Ctrl-C` for clean shutdown — in-flight polls finish, state is
persisted, sockets close cleanly.

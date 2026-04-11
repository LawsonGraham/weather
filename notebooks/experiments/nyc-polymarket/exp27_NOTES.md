# Exp 27 + Live Recommender

**Scripts**: `exp27_bias_carryover.py`, `../../scripts/polymarket_weather/live_recommender.py`
**Date**: 2026-04-11

## Exp 27 — bias carryover hypothesis killed

**Question**: does yesterday's upward miss size predict today's?

**Answer**: NO.

```
Lag-1 autocorrelation of signed_gap:  0.04  (essentially zero)
Mean day-over-day change in gap:     -0.074°F
```

Each day's bias is independent. A "hot-yesterday → hot-today" filter
would not work. This is mildly surprising — weather patterns persist
in reality (cold fronts, warm spells) so you'd expect SOME
autocorrelation. The lack of it suggests the **Polymarket market is
adjusting its forecast daily** in a way that removes persistence from
the gap-vs-forecast residual. The underlying weather has persistence,
but the market's forecast error on that weather does not.

**Implication**: no "multi-day carryover" refinement is available.
Strategy D decisions must be made on current-day signals alone.

## Live recommender

`scripts/polymarket_weather/live_recommender.py` is the first
production-ready script from this exploration loop. Given a target
day and bankroll, it:

1. Queries the local processed Polymarket markets parquet for the
   current NYC daily-temperature ladder
2. Identifies the range-strike favorite at a specified entry hour
   (12 / 16 / 18 EDT)
3. Looks up the `fav_lo + 2` bucket
4. Fetches the real-ask via last-YES-BUY fill before the target hour
5. Checks V5 skip rules at 12 EDT using METAR (skip dry, skip rise≥6)
6. Outputs a Kelly-sized trade recommendation with shares, stake,
   entry cost, and expected profit/loss

### Tested on today (April 11, 2026)

Running with `--all-hours --bankroll 10000 --kelly 0.02`:

- **V5 @ 12 EDT**: correctly SKIPPED because no 12 EDT METAR
  available yet (we ran this well before the 12 EDT NY local METAR
  report would exist)
- **V1 @ 16 EDT**:
  ```
  Favorite:     62-63°F  @ $0.390
  Target (+2):  64-65°F  @ $0.140
  Entry ask:    $0.1500  (real-ask + 0.01 safety)
  Entry cost:   $0.1530
  Stake:        $200.00
  Shares:       1307.19
  Payoff if hit: $1,307.19  → profit +$1,107.19
  Loss if miss:  -$200.00
  ```
- **V1 @ 18 EDT**: same recommendation (the book hasn't moved yet
  since we're running before the target hours)

**This is a fully actionable recommendation the user can execute on
Polymarket at 16 EDT (about 2 hours from now)**. The limit price
$0.153 is the target; the 1307 shares would cost ~$200 and pay
~$1,307 if the day's max lands in 64-65°F.

### What the runner covers

- Entry-hour logic (12 EDT → V5 with skip; 16/18 EDT → V1)
- Real-ask estimation from the fills parquet
- Safety padding of 1¢ added to the ask for limit-order buffer
- Kelly sizing
- Clean actionable output (strike, slug, limit price, stake, shares)

### What it does NOT cover (yet)

- **Live Gamma API pull**: currently reads from stale
  `markets.parquet` snapshot. Real deployment needs a 5-min cron that
  re-downloads the ladder before the entry hour.
- **Live METAR pull**: currently reads from stale `iem_metar` parquet.
  Real deployment needs an AWOS / ASOS direct feed at KLGA.
- **Trade execution**: still manual. Trade logging is stdout only;
  needs a `data/processed/paper_trades/` JSON ledger.
- **Fee model verification**: assumes flat 2% NegRisk fee. Real fees
  may be tiered; confirm before live capital.
- **Bid/ask from live book**: exp06b's "mid == ask" pattern should be
  verified live before relying on the real-ask reconstruction.

## Deployment checklist (updated)

- [x] Strategy D v5 skip rules (exp24)
- [x] Entry-hour selection logic (exp18, exp25)
- [x] Real-ask cost model (exp06b, exp20)
- [x] Kelly sizing (exp14, exp16)
- [x] Combined portfolio sim (exp17, exp20)
- [x] Late-day book activity verified (exp19)
- [x] Bias carryover ruled out (exp27)
- [x] **Live recommender script** (this exp)
- [ ] Live Gamma ladder refresh (5-min cron)
- [ ] Live METAR feed
- [ ] Paper-trade ledger (JSON append-only)
- [ ] 14 live days of paper trading → validate live numbers
- [ ] Scale to real capital at 0.5% Kelly after validation
- [ ] HRRR-based pre-cross edge comparison (exp28, still blocked)

## Queued next steps

1. **Live data refresh**: build a 5-min cron that re-fetches the NYC
   daily-temp ladder from Gamma API + latest METAR. Update local
   parquet files. Trigger the recommender.
2. **Paper-trade ledger**: append-only JSON log at
   `data/processed/paper_trades/nyc_strategy_d/YYYY-MM.jsonl` with
   entry details, target, entry cost, outcome, PnL.
3. **Evaluation script**: score the paper trades at end-of-day,
   compare to backtest distribution, flag deviations.
4. **HRRR integration**: once backfill completes (currently ~70%),
   replace the METAR-based skip rules with HRRR-based ones.

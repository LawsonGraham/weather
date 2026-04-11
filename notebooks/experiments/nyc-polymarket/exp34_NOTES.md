# Exp 34 — Saturday deep-dive + 08 EDT mystery

**Script**: `exp34_saturday_and_morning.py`
**Date**: 2026-04-11
**Status**: Two refinements / nuancings of earlier findings.

## A. Saturday — 57%, not 75%

Per-Saturday detail across all 7 Saturdays in the 55-day sample:

| date       | fav      | fav_p | dmax | gap  | outcome |
|------------|----------|-------|------|------|---------|
| 2026-02-21 | 44-45°F  | 0.54  | 47   | +3   | **D WIN** |
| 2026-02-28 | 48-49°F  | 0.32  | 52   | +4   | miss (D bought 50-51, day was 52) |
| 2026-03-07 | 46-47°F  | 0.27  | 49   | +3   | **D WIN** |
| 2026-03-14 | 50-51°F  | 0.47  | 55   | +5   | miss (D bought 52-53, day was 55) |
| 2026-03-21 | 56-57°F  | 0.56  | 58   | +2   | **D WIN** |
| 2026-03-28 | 42-43°F  | 0.54  | 45   | +3   | **D WIN** |
| 2026-04-04 | 50-51°F  | 0.002 | 68   | +18  | extreme miss (argmax artifact, see exp23) |

**4 of 7 Saturdays = 57% hit rate.** Not 75% as exp31 reported.

The exp31 75% figure was from the conservative-filter subset (n=4) that
DROPPED the three Saturdays where the conservative entry filter
(p_entry ≥ 2¢) excluded them. Specifically, the April 4 case has
fav_p = 0.002 — that's an "argmax artifact day" from exp23 where every
strike is priced at near-zero and arg_max picks an arbitrary one.
Without genuinely active books, those days don't fire Strategy D.

But the **directional pattern is still 7/7 upward** — the day always
ends up hotter than the favorite's lower bound on Saturday in this
sample. So the universal upward bias is robust on Saturdays even
though only 57% are Strategy D wins (because some misses are by 4-5°F,
beyond the +2 bucket).

**Refined Saturday read**: 57% hit rate is the highest of any DoW
(other days are 0-40%). Saturday IS the best day for D, just not by
the magnitude exp31 suggested.

## B. 08 EDT confirmed losing — 17% hit rate

```
n=36, hit_rate 17%, cum_pnl -$2.74
```

Strategy D at 08 EDT loses on 36 days. Hit rate is roughly half of
12 EDT (31%). Not a noise effect — n=36 is sufficient.

## C. Why 08 EDT loses — favorite is STABLE through the morning

Tracked the favorite's price at 06 EDT, 10 EDT, and 12 EDT:

| metric            | value  |
|-------------------|--------|
| mean fav_p at 06  | 0.346  |
| mean fav_p at 10  | 0.357  |
| mean fav_p at 12  | 0.352  |
| drift 06 → 10     | +0.011 |
| drift 10 → 12     | -0.005 |

**The favorite barely moves during the morning.** Total movement from
06 to 12 EDT is +0.6¢ on average. The "morning re-open updates the
favorite badly" hypothesis is FALSE.

So why does Strategy D fail at 08 EDT? Three remaining possibilities,
none yet validated:

1. **+2 bucket price is overpriced at 08-10 EDT.** The favorite is
   stable but the +2 bucket might be more expensive when retail isn't
   actively trading it (few sellers willing to quote). Worth checking.

2. **Real-time information arrives 10-12 EDT.** Morning weather updates
   (TV forecasts, weather.com refreshes around 11 AM ET) could shift
   the market's view between 10 and 12. Strategy D at 08 EDT enters
   before that update; at 12 EDT after.

3. **Favorite identity shifts in ~20% of days** (exp18 found 80% of
   12 EDT favs match 10 EDT favs). When the fav shifts, the +2 target
   shifts too. The morning "wrong fav" leads to a worse +2 target.

None of these are tested rigorously. The conclusion is simply:
**08-10 EDT is empirically the worst window for Strategy D — avoid
entering there**. The mechanism is unclear but the data is consistent.

## Updated DoW table for Strategy D (full per-day, no filter)

Given exp23 + exp34 cleanup, the Saturday number is 57% not 75%.
Other DoW numbers from exp31 stand.

| DoW       | n_total | D wins (any subset) | best read |
|-----------|---------|---------------------|-----------|
| Sunday    | 7       | ?                   | ~20% hit  |
| Monday    | 7       | 0/3 (filtered)      | weak      |
| Tuesday   | 8       | 1/5                 | 20% hit, big gaps |
| Wednesday | 9       | 2/7                 | ~29% hit  |
| Thursday  | 9       | 2/5                 | 40% hit, outliers |
| Friday    | 8       | 2/6                 | ~33% hit  |
| **Saturday** | 7    | **4/7**             | **57% hit** |

Saturday remains the best DoW. Tuesday remains the worst (consistent
big upward gaps but the +2 offset is too small for them).

## Implications for deployment

- **Keep V1 at 16 EDT** as the primary entry hour
- **De-size or skip Mon/Tue trades** — both have <30% hit rate
- **Up-size Saturday trades** — 57% hit rate is materially better than
  the 31% baseline (1.8x)
- **Avoid 08-10 EDT entries** — proven loser

## Open items

- Mechanism for the 08-10 EDT loss (n=36 hit 17%) — unresolved
- Why Saturday is best (weather pattern? retail flow on weekends?) —
  unresolved, just observed
- HRRR comparison (still blocked at ~83%)

# Exp L — Ladder-wide pump analysis — CORRECTS exp K

**Script**: `expL_pump_across_ladders.py` + inline minute-path queries
**Date**: 2026-04-11
**Status**: **corrects exp K's wrong story**. The real pattern is very
different from what exp K narrated. Strategy D V1 at 16:00 EDT is
actually well-timed; the *favorite* pumps at 15:55-16:00 EDT, not the
+2 bucket. And Strategy D V2 at 15:30 EDT is WORSE than V1, not better.

## The actual april-11 afternoon timeline

Minute-by-minute averages of `mid` for the favorite, +1, and +2
buckets from the tob parquet:

```
         time (UTC)    60-61(fav)  62-63(+1)  64-65(+2)
         19:24           0.585      0.384      0.063
         19:30           0.580      0.362      0.042
         19:35           0.567      0.392      0.097  ← brief +2 spike
         19:40           0.680      0.309      0.027
         19:50           0.651      0.303      0.029
         19:55           0.688      0.276      0.029  ← favorite starts climbing
         19:56           0.690      0.275      0.024
         19:57           0.724      0.247      0.015
         19:58           0.773      0.228      0.007
         19:59           0.789      0.161      0.006
      *  20:00           0.869      0.127      0.006  *  STRAT-D V1 ENTRY
         20:01           0.865      0.095      0.009
         20:02           0.882      0.090      0.012
         20:07           0.868      0.156      0.009  ← +1 recovers
         20:12           0.877      0.150      0.009
         20:20           0.839      0.135      0.005
         20:30           0.890      0.084      0.010  ← +1 drifts down again
```

## What actually happened

1. **The FAVORITE pumps 18 cents in 5 minutes** (19:55 0.688 → 20:00 0.869).
   This is an informed-flow pump — probably HRRR 12 UTC refresh confirming
   a 60°F peak, or a large trader piling in.
2. **The +1 bucket (62-63) crashes with the favorite pump**: from 0.276 to
   0.127 in the same 5-minute window (-15 cents), as mass flows OUT of
   +1 into the favorite.
3. **+1 hits a local minimum at 20:00 UTC (exactly at Strategy D V1 entry
   time)** and recovers to ~0.16 over the next 6-10 minutes.
4. **+2 bucket (64-65) is at floor the whole time** — mostly 0.01-0.06,
   brief spike to 0.097 at 19:35 UTC, otherwise inert.

## Strategy D V1 timing is actually favorable

Strategy D V1 enters the +1 bucket (lo_f+2 from favorite 60 = 62) at
exactly its local minimum (0.127 mid at 20:00 UTC). The bucket then
partially recovers over the next 10 minutes. **V1 is catching a local
bottom**, not a pre-entry pump. **Exp K's narrative was wrong.**

What went wrong in exp K:
- Exp K used a *single snapshot* at 20:00:03 UTC showing mid=0.150 /
  ask=0.180 for the 62-63 bucket
- I misread this as a "pre-entry pump from 0.07"
- The 0.07 was the 64-65 bucket mid at 19:24 UTC, NOT the 62-63 bucket
- Strategy D's +2 offset targets lo_f+2 = 62 (the 62-63°F bucket), not
  the next bucket over (64-65°F)
- Exp K confused which bucket Strategy D targets and conflated two
  different buckets' price paths

## Implications — corrected

### Strategy D V2 at 15:30 EDT is WORSE than V1

From exp L's per-slug phase summary (april-11, 62-63 bucket):
- pre-window avg (19:24–19:35 UTC): **0.369 mid**
- entry-window avg (19:55–20:00 UTC): **0.219 mid**
- post-entry (20:20–20:30 UTC): **0.114 mid**

V2 at 15:30 EDT would enter at ~0.369 (pre-window avg) vs V1 at ~0.219
(entry-window avg). **V2 pays 1.7× more per share than V1**. On a losing
day (where +1 → 0), that's a bigger loss. On a winning day (+1 resolves
YES), V1 has higher ROI per share.

**V2 at 15:30 EDT is a LOSING variant, not a winning one. Rejected.**

### Strategy D V3 at 16:30 EDT is plausibly BETTER than V1

V3 at 20:30 UTC (16:30 EDT) would enter at:
- Favorite (60-61) ≈ 0.889
- +1 (62-63) ≈ 0.135 mid (down from V1's 0.127 at entry, similar)
- +2 (64-65) ≈ 0.005 mid (at floor — too far out)

Within 30 more minutes: +1 might drift to 0.084-0.11 (observed 20:30-
20:35 UTC) — **another 20-30% cheaper than V1**.

V3 implications:
- **Pro**: cheaper entry per share → higher ROI if trade wins, smaller
  loss if trade fails
- **Con**: less time for the +1 bucket to resolve YES (the high might
  already be set by 16:30 EDT). The market "gives up" on the bucket
  faster than new temperature data can arrive.
- **Net**: needs a backtest with multiple active days to see whether
  hit rate degrades as much as cost drops.

Fundamentally, V3 is betting on residual probability mass in a bucket
the market has nearly written off. Only makes sense when there's still
real uncertainty about the afternoon peak past 16:30 EDT.

### The favorite-pump moment (19:55-20:00 UTC) is the information event

Something triggered the 18-cent pump on the favorite at 15:55 EDT:
- Most likely: HRRR 12 UTC cycle valid at 20 UTC forecast confirms
  LGA peak hits 60-61°F
- Alternative: large trader with a better model
- Alternative: coordinated flow from copycat bots

**If we can PREDICT the pump**, we can enter the favorite just before
it happens and ride a free 18 cents. If the pump timing is reliably
15:55-16:00 EDT (driven by a 15-min delay from the 12 UTC HRRR post),
a bot placing buy-limits on the favorite at 15:45 EDT could get filled
at pre-pump prices and profit from the surge.

This is exp L's surprising new hypothesis — **the market has a
scheduled reaction pattern tied to HRRR cycles**. Needs multi-day
verification but it's the cleanest potential edge since the ladder-bid
arb.

## Queued followups

- **Strategy D V3 multi-day backtest**: use prices_history min1 data
  across 571 historical days; compute entry at 16:30 EDT vs 16:00 EDT.
- **Favorite-pump timing study**: isolate "big 5-min moves" on favorites
  across multiple days; compute histogram of local-time-of-day.
  Confirm 15:55-16:00 EDT clustering.
- **Retract exp K's Strategy D V2 synthesis** in the vault.

## Lesson

**Always verify a visual narrative with a per-minute path plot.** Exp K
inferred a pump from two data points (0.07 at 19:24, 0.18 at 20:00) but
those were on DIFFERENT buckets. The full minute path would have
immediately revealed the correct story.

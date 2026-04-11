---
tags: [concept, data-source, polymarket]
date: 2026-04-11
related: "[[Polymarket]], [[Polymarket weather market catalog]], [[Polymarket CLOB WebSocket]]"
---

# Polymarket `/prices-history` endpoint

`https://clob.polymarket.com/prices-history?market=<yes_token_id>&interval=<X>&fidelity=<Y>`

Returns midpoint price time series for a single CLOB token. For NYC daily-temperature markets this is the cheapest way to reconstruct per-bucket price paths without touching Goldsky fills. **Hourly fidelity is retained for the entire lifetime of every closed market.** 1-minute fidelity is available only for the past 24 h of active markets.

## Parameter matrix (empirically probed 2026-04-11)

| `interval` | `fidelity` | closed market | open market | coverage |
|---|---|---|---|---|
| `max` | `60` (hourly) | ✅ returns | ✅ returns | full lifetime |
| `1d` | `1` (1-min) | ❌ 400 / empty | ✅ returns | last 24 h |
| `6h` | `1` | ❌ 400 | ✅ returns | last 6 h |
| `max` | `1` | ❌ 400 | ❌ 400 | — |
| any | `startTs`/`endTs` | ❌ 400 | ❌ 400 | — |

The API silently downgrades anything except the combinations above. Probe before trusting a new combination.

## Response shape

```json
{"history": [{"t": 1775935409, "p": 0.405}, ...]}
```

- `t` — UNIX epoch seconds
- `p` — midpoint price of the YES token (0 to 1). Not a bid or an ask — the **midpoint**.
- Empty response for unresolved edge cases is `{"history": []}`, not a 404.

## Error handling

- **`400 Bad Request`** — the canonical "no data for this combination" signal. Don't retry; treat as empty. The downloader implements exactly this.
- **`429 Too Many Requests`** — back off 5 × attempt seconds. Serialize, don't parallelize.
- Everything else: three-retry exponential backoff.

## Our implementation

`scripts/polymarket_prices_history/download.py` pulls:
- `interval=max&fidelity=60` for **every** slug (closed + open)
- `interval=1d&fidelity=1` for **open** slugs only (skipped on closed to avoid the 400)

Output: one JSON per slug at `data/raw/polymarket_prices_history/<slug>.json` with both histories in-line, plus the slug catalog fields. ~200 ms inter-request delay.

`scripts/polymarket_prices_history/transform.py` emits two partitioned parquet datasets:
- `data/processed/polymarket_prices_history/hourly/year=/month=/part-0.parquet`
- `data/processed/polymarket_prices_history/min1/year=/month=/part-0.parquet`

Schema (both variants):

| column | type |
|---|---|
| `timestamp` | TIMESTAMPTZ (UTC) |
| `slug` | VARCHAR |
| `condition_id` | VARCHAR |
| `yes_token_id` | VARCHAR |
| `p_yes` | DOUBLE (0..1 midpoint) |
| `closed_at_fetch` | BOOLEAN (fetch-time state; doesn't retroactively update) |
| `fetched_at` | TIMESTAMPTZ |

## Initial pull sizing (NYC, 2026-04-11)

- 574 slugs covered (532 closed, 42 open)
- 571 slugs returned ≥1 point (3 empty / skip)
- **27,296 hourly rows** (avg ~48 points/slug, up to ~140 for long-lived markets)
- **54,055 1-minute rows** (42 open slugs × ~1,280 points/slug for the last 24 h)

## Why this is a big deal vs Goldsky fills

- **Midpoint, not fill-derived.** Goldsky fills only tell you prices where a trade happened. The `/prices-history` endpoint reflects the resting book midpoint even when there are no trades — it captures quiet-market regimes where we'd otherwise have zero signal.
- **Hourly backfill for closed markets is near-free.** 550 closed NYC slugs in ~8 minutes at 200 ms/req. No need to stitch from fills.
- **1-min fidelity is the real unlock.** For active markets we now have minute-level price paths — this is the level at which backtests can reason about mid-day reactions to temperature readings, HRRR init cycles, and the ~16 EDT resolution approach.

## Caveats

- Only a **24-hour trailing window** of 1-min data per fetch. If you want the full day-of-resolution path you must fetch exactly at or shortly after 16–18 EDT and the full trading day will be included.
- Midpoint ≠ real cost. Strategy backtests using 1-min data must still apply a fee (we use 2 %) and consider slippage; the midpoint is upper-bound-optimistic for execution.
- No order book depth. Book depth comes from the [[Polymarket CLOB WebSocket]] live stream, not this endpoint.

## Related

- [[Polymarket]] — parent entity
- [[Polymarket CLOB WebSocket]] — live book + incremental price stream, complements this endpoint
- [[Polymarket weather market catalog]] — slug source for the downloader

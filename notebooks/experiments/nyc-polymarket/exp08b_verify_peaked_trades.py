"""Experiment 08b — verify the 5 high-conviction peaked-ladder trades.

Exp07 found that "fade favorites priced ≥80¢ at 12 EDT" missed 5 of 5 in
the backtest window. That's the entire cum_pnl of the refined strategy.
If those 5 trades were on stale order books (frozen at 0.99¢ with no
fills around 12 EDT), then my "entry price" is fictional and the strategy
is an artifact.

This is the thesis stop-loss test.

For each of the 5 slug×day combos:
    1. How many fills in [11:30, 12:30 EDT]?
    2. Price range of fills in the window?
    3. Active trading volume (total USD)?
    4. Number of distinct takers in the window?

Stale book symptoms: 0-2 fills in the hour around 12 EDT, all at ≥99¢.
Active book symptoms: dozens of fills across a range of prices.

If any of the 5 trades are flagged stale, drop them from the headline
finding and re-score the peaked-ladder rule.
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 300)
pd.set_option("display.max_rows", 200)

PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
FILLS = "data/processed/polymarket_weather/fills/**/*.parquet"

TRADES = [
    ("2026-03-27", "66-67°F", 0.999, 68),
    ("2026-03-12", "56-57°F", 0.962, 60),
    ("2026-03-05", "44-45°F", 0.900, 46),
    ("2026-02-22", "34-35°F", 0.871, 44),
    ("2025-12-30", "32-33°F", 0.850, 40),
]


def main() -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    # Build slug lookup
    results = []
    for local_day, strike, p_fav, day_max in TRADES:
        slug = con.execute(f"""
            SELECT slug FROM 'data/processed/polymarket_weather/markets.parquet'
            WHERE city='New York City'
              AND weather_tags ILIKE '%Daily Temperature%'
              AND CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) = DATE '{local_day}'
              AND group_item_title = '{strike}'
        """).fetchone()
        if not slug:
            print(f"⚠️  slug not found for {local_day} {strike}")
            continue
        slug = slug[0]

        # Fills in [11:30, 12:30 EDT] on target day = [15:30, 16:30 UTC on EST (Dec-Mar)]
        # or [15:30, 16:30 UTC on EDT (Mar-Nov)]. Use ±1h around 12 EDT = 16 UTC
        window_start = f"{local_day} 15:30:00+00"
        window_end   = f"{local_day} 16:30:00+00"

        fill_stats = con.execute(f"""
            SELECT
                COUNT(*)                    AS n_fills,
                ROUND(MIN(price), 4)        AS min_price,
                ROUND(MAX(price), 4)        AS max_price,
                ROUND(AVG(price), 4)        AS avg_price,
                ROUND(SUM(usd), 2)          AS total_usd,
                COUNT(DISTINCT taker)       AS n_takers,
                COUNT(DISTINCT outcome)     AS n_outcomes
            FROM '{FILLS}'
            WHERE slug = '{slug}'
              AND timestamp BETWEEN TIMESTAMPTZ '{window_start}' AND TIMESTAMPTZ '{window_end}'
        """).fetchone()

        # Also: all fills on the day, and the last 20 fills before 12 EDT
        day_stats = con.execute(f"""
            SELECT COUNT(*) AS n_fills, ROUND(SUM(usd), 2) AS total_usd,
                   ROUND(AVG(price), 4) AS avg_price
            FROM '{FILLS}'
            WHERE slug = '{slug}'
              AND timestamp BETWEEN TIMESTAMPTZ '{local_day} 04:00:00+00' AND TIMESTAMPTZ '{local_day} 23:59:00+00'
        """).fetchone()

        last_fills = con.execute(f"""
            SELECT timestamp, outcome, side, ROUND(price, 4) AS price, ROUND(usd, 2) AS usd
            FROM '{FILLS}'
            WHERE slug = '{slug}'
              AND timestamp <= TIMESTAMPTZ '{local_day} 16:00:00+00'
            ORDER BY timestamp DESC
            LIMIT 15
        """).df()

        results.append({
            "day": local_day, "strike": strike, "p_fav_noted": p_fav,
            "day_max": day_max,
            "window_n_fills": fill_stats[0], "window_min": fill_stats[1],
            "window_max": fill_stats[2], "window_avg": fill_stats[3],
            "window_usd": fill_stats[4], "window_takers": fill_stats[5],
            "day_n_fills": day_stats[0], "day_total_usd": day_stats[1],
            "last_fills": last_fills,
            "slug": slug,
        })

    print("\n=== HIGH-CONVICTION TRADE VERIFICATION ===\n")
    for r in results:
        print(f"── {r['day']} {r['strike']} (p_fav={r['p_fav_noted']}, day_max={r['day_max']}) ──")
        print(f"   slug: {r['slug']}")
        print(f"   ±30min-at-12-EDT window:  {r['window_n_fills']} fills, "
              f"price {r['window_min']}–{r['window_max']}, "
              f"avg {r['window_avg']}, ${r['window_usd']} vol, "
              f"{r['window_takers']} distinct takers")
        print(f"   full day:                 {r['day_n_fills']} fills, ${r['day_total_usd']} vol")
        print(f"   last 15 fills before 12 EDT:")
        print(r["last_fills"].to_string(index=False))
        print()

    # Summary verdict
    print("\n=== VERDICT ===")
    stale_threshold_fills = 3
    stale_threshold_price_range = 0.02
    stale_count = 0
    for r in results:
        if r["window_n_fills"] < stale_threshold_fills:
            print(f"⚠️  {r['day']} {r['strike']}: only {r['window_n_fills']} fills in ±30min window")
            stale_count += 1
        elif r["window_max"] - r["window_min"] < stale_threshold_price_range and r["window_avg"] > 0.95:
            print(f"⚠️  {r['day']} {r['strike']}: window price range "
                  f"{r['window_min']}-{r['window_max']} looks frozen at ≥99¢")
            stale_count += 1
        else:
            print(f"✓  {r['day']} {r['strike']}: {r['window_n_fills']} active fills, price range {r['window_min']}-{r['window_max']}")
    print()
    print(f"Stale: {stale_count} / {len(results)}")
    print(f"Active: {len(results) - stale_count} / {len(results)}")


if __name__ == "__main__":
    main()

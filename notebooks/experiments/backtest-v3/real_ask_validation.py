"""Validate the +1 offset NO strategy with REAL ask prices from book JSONL.

My backtest assumed NO-ask = 1 - YES-mid. If real NO-ask is
systematically higher (wider spread), the edge could shrink.

This script:
1. For each slug with book data, parse L2 snapshots near 20 UTC
2. Extract best YES bid/ask and NO bid/ask at that time
3. Compare real NO-ask to (1 - YES-mid)
4. For +1 offset NO trades on Apr 11-13, compute realistic PnL

Also runs the strategy on Apr 11-13 markets as an additional holdout.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta, date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"
BOOK_DIR = REPO / "data" / "raw" / "polymarket_book"

CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE = 0.05


def parse_book_at_time(slug_dir: Path, target_ts: datetime) -> dict | None:
    """Parse book JSONL files and return the L2 snapshot closest to target_ts.

    Returns dict with best_yes_bid/ask (from bids/asks arrays).
    """
    closest_snap = None
    closest_delta = timedelta(hours=999)
    for f in sorted(slug_dir.glob("*.jsonl")):
        try:
            for line in f.read_text().splitlines():
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = m.get("event_type") or m.get("type") or ""
                if et != "book":
                    continue
                ts_str = m.get("_received_at", "")
                if not ts_str:
                    continue
                try:
                    snap_ts = datetime.strptime(
                        ts_str.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S.%f%z"
                    )
                except Exception:
                    continue
                delta = abs(snap_ts - target_ts)
                if delta < closest_delta:
                    closest_delta = delta
                    closest_snap = m
        except Exception:
            continue
    if closest_snap is None:
        return None
    bids = closest_snap.get("bids") or []
    asks = closest_snap.get("asks") or []
    bid_prices = sorted([float(b["price"]) for b in bids if "price" in b], reverse=True)
    ask_prices = sorted([float(a["price"]) for a in asks if "price" in a])
    if not bid_prices or not ask_prices:
        return None
    return {
        "best_yes_bid": bid_prices[0],
        "best_yes_ask": ask_prices[0],
        "yes_mid": (bid_prices[0] + ask_prices[0]) / 2,
        "yes_spread": ask_prices[0] - bid_prices[0],
        "snap_time": closest_snap["_received_at"],
        "snap_delta_s": closest_delta.total_seconds(),
    }


def main():
    # Markets to analyze: Apr 11-13 resolved daily-temp slugs
    con = duckdb.connect()
    slugs = con.execute(f"""
        SELECT slug, city, yes_token_id, group_item_threshold AS bucket_idx,
               group_item_title, outcome_prices, end_date,
               closed
        FROM '{REPO}/data/processed/polymarket_weather/markets.parquet'
        WHERE weather_tags ILIKE '%Daily Temperature%'
          AND DATE(end_date) BETWEEN '2026-04-11' AND '2026-04-13'
          AND closed = true
    """).fetch_df()
    slugs["market_date"] = pd.to_datetime(slugs["end_date"]).dt.date
    slugs["station"] = slugs["city"].map(CITY_TO_STATION)

    # Resolution check
    def won_yes(row):
        op = row["outcome_prices"]
        if op is None or len(op) != 2:
            return -1
        return int(op[0] == 1.0)
    slugs["won_yes"] = slugs.apply(won_yes, axis=1)
    print(f"Loaded {len(slugs)} resolved bucket-slugs Apr 11-13")
    print(f"Resolved with winner: {(slugs.won_yes >= 0).sum()}")

    # Parse bucket thresholds
    def parse_bucket(title):
        m = re.match(r"^(\d+)-(\d+)°F$", title)
        if m:
            return (float(m[1]), float(m[2]), (int(m[1])+int(m[2]))/2)
        m = re.match(r"^(\d+)°F or below$", title)
        if m:
            return (float("-inf"), float(m[1]), float(int(m[1])-1))
        m = re.match(r"^(\d+)°F or higher$", title)
        if m:
            return (float(m[1]), float("inf"), float(int(m[1])+1))
        return (None, None, None)

    slugs[["bucket_low", "bucket_high", "bucket_center"]] = slugs.apply(
        lambda r: pd.Series(parse_bucket(r["group_item_title"])), axis=1
    )
    slugs = slugs.dropna(subset=["bucket_center"])

    # Load NBS predictions to find NBS_fav bucket per (station, market_date)
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat["local_date_d"] = feat["local_date"].dt.date
    feat = feat[["station", "local_date_d", "nbs_pred_max_f", "actual_max_f"]].dropna()

    # For each (city, market_date), find the NBS_fav bucket
    nbs_fav_map = {}
    for (city, md), grp in slugs.groupby(["city", "market_date"]):
        station = CITY_TO_STATION.get(city)
        if station is None:
            continue
        nbs_row = feat[(feat.station == station) & (feat.local_date_d == md)]
        if nbs_row.empty:
            continue
        nbs_pred = float(nbs_row["nbs_pred_max_f"].iloc[0])
        # Find the bucket whose center is closest to nbs_pred
        diffs = (grp["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(grp.loc[diffs.idxmin(), "bucket_idx"])
        nbs_fav_map[(city, md)] = {"nbs_fav_idx": nbs_fav_idx, "nbs_pred": nbs_pred,
                                    "actual_max": float(nbs_row["actual_max_f"].iloc[0]) if pd.notna(nbs_row["actual_max_f"].iloc[0]) else None}

    # For each +1 offset bucket, check book data and compute real-ask PnL
    print()
    print("=== +1 offset NO trades (Apr 11-13, with real ask) ===")
    print(f"{'date':<10} {'city':<12} {'bucket':<15} {'YES_mid':>8} {'YES_ask':>8} "
          f"{'NO_mid':>7} {'NO_ask':>7} {'spread':>8} {'delta_s':>8} {'won_y':>5} "
          f"{'real_pnl':>9}")
    print("-" * 110)

    trades_real = []
    trades_theoretical = []
    for (city, md), meta in nbs_fav_map.items():
        nbs_fav_idx = meta["nbs_fav_idx"]
        target_idx = nbs_fav_idx + 1
        grp = slugs[(slugs.city == city) & (slugs.market_date == md)]
        row = grp[grp["bucket_idx"] == target_idx]
        if row.empty:
            continue
        r = row.iloc[0]
        if r["won_yes"] < 0:
            continue

        # Parse book at 20:00 UTC on market_date
        slug_dir = BOOK_DIR / r["slug"]
        if not slug_dir.exists():
            continue
        target_ts = datetime.combine(md, datetime.min.time(), tzinfo=UTC) + timedelta(hours=20)
        snap = parse_book_at_time(slug_dir, target_ts)
        if snap is None:
            continue

        yes_mid = snap["yes_mid"]
        yes_ask = snap["best_yes_ask"]
        yes_bid = snap["best_yes_bid"]
        # NO prices: NO_bid + YES_ask = 1, NO_ask + YES_bid = 1
        # NO_ask = 1 - YES_bid (the worst-case you pay to buy NO)
        no_ask = 1 - yes_bid
        no_bid = 1 - yes_ask
        no_mid = 1 - yes_mid

        # Theoretical PnL (our backtest assumption): NO at 1-yes_mid
        theo_fee = FEE * no_mid * (1 - no_mid)
        theo_won = 1 - int(r["won_yes"])
        theo_pnl = theo_won - no_mid - theo_fee

        # Real PnL: NO at actual no_ask
        real_fee = FEE * no_ask * (1 - no_ask)
        real_pnl = theo_won - no_ask - real_fee

        # Yes price filter for strategy eligibility (only trade if yes_mid in [0.005, 0.5])
        if not (0.005 <= yes_mid <= 0.5):
            continue

        print(f"{md} {city[:11]:<12} {r['group_item_title']:<15} "
              f"{yes_mid:>8.3f} {yes_ask:>8.3f} {no_mid:>7.3f} {no_ask:>7.3f} "
              f"{snap['yes_spread']:>8.4f} {snap['snap_delta_s']:>7.0f}s "
              f"{r['won_yes']:>5} ${real_pnl:>+8.3f}")

        trades_theoretical.append({
            "city": city, "date": md, "bucket": target_idx,
            "yes_mid": yes_mid, "no_price": no_mid,
            "won_no": theo_won, "pnl": theo_pnl,
        })
        trades_real.append({
            "city": city, "date": md, "bucket": target_idx,
            "yes_bid": yes_bid, "yes_ask": yes_ask,
            "yes_mid": yes_mid, "yes_spread": snap["yes_spread"],
            "no_ask": no_ask, "no_mid": no_mid,
            "won_no": theo_won, "pnl_theo": theo_pnl, "pnl_real": real_pnl,
        })

    th = pd.DataFrame(trades_theoretical)
    rl = pd.DataFrame(trades_real)
    if len(th) == 0:
        print("\nNo tradeable +1 offset buckets found in Apr 11-13 window.")
        return

    print()
    print(f"=== Summary (Apr 11-13 holdout with real ask) ===")
    print(f"n trades: {len(th)}")

    def stats(pnl_col, df):
        pnl = df[pnl_col]
        std = pnl.std() if len(pnl) > 1 else 0
        t = pnl.mean() / (std / len(pnl)**0.5) if std > 0 else 0
        return {
            "n": len(pnl), "mean": pnl.mean(), "tot": pnl.sum(),
            "hit": df.won_no.mean(), "t": t,
        }

    ts = stats("pnl_theo", rl)
    rs = stats("pnl_real", rl)
    print(f"  Theoretical (NO at 1-yes_mid): hit={ts['hit']*100:.1f}%  "
          f"per=${ts['mean']:+.4f}  tot=${ts['tot']:+.2f}  t={ts['t']:+.2f}")
    print(f"  REAL (NO at 1-yes_bid): hit={rs['hit']*100:.1f}%  "
          f"per=${rs['mean']:+.4f}  tot=${rs['tot']:+.2f}  t={rs['t']:+.2f}")
    print()
    print(f"Spread stats:")
    print(f"  YES spread mean: {rl.yes_spread.mean():.4f}")
    print(f"  YES spread median: {rl.yes_spread.median():.4f}")
    print(f"  NO-ask minus NO-mid: {(rl.no_ask - rl.no_mid).mean():.4f}")

    # Per-city
    if len(rl) > 0:
        print("\nPer-city (real ask PnL):")
        for city, g in rl.groupby("city"):
            print(f"  {city:<18} n={len(g):>2}  hit={g.won_no.mean()*100:>5.1f}%  "
                  f"theo_per=${g.pnl_theo.mean():>+.3f}  "
                  f"real_per=${g.pnl_real.mean():>+.3f}")


if __name__ == "__main__":
    main()

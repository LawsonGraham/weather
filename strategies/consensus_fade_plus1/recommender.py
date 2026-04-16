"""Consensus-Fade +1 Offset — Live Trade Recommender.

Reads today's NBS + GFS MOS + HRRR forecasts from the processed feature
parquet, pulls current Polymarket daily-temperature markets, and emits
recommended BUY-NO trades for buckets that pass the consensus filter.

Usage:
    uv run python strategies/consensus_fade_plus1/recommender.py
        [--date YYYY-MM-DD]              # target market date (default: today UTC)
        [--consensus-max 3.0]            # forecasts must agree within this °F
        [--min-yes-price 0.005]          # skip buckets with YES ≤ this (tick-floor)
        [--max-yes-price 0.5]            # skip buckets with YES ≥ this (already favorite)
        [--stake-per-trade 20]           # USD cap per recommendation
        [--max-slippage-bps 200]         # max allowed slippage (2% = 200 bps)

Output: table of recommendations printed to stdout (and optionally
written to JSON if --json-out is given).

This is a RECOMMENDER — it does not submit orders. The operator reviews
the table and places limit orders on Polymarket via their preferred
interface.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path(__file__).resolve().parents[2]

# Pre-registered parameters from backtest v3 (STRATEGY.md §3)
DEFAULT_CONSENSUS_MAX_F = 3.0
DEFAULT_MIN_YES_PRICE = 0.005
DEFAULT_MAX_YES_PRICE = 0.5
ENTRY_HOUR_UTC = 20

CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL",
    "Seattle": "SEA", "Chicago": "ORD", "Miami": "MIA",
    "Austin": "AUS", "Houston": "HOU", "Denver": "DEN",
    "Los Angeles": "LAX", "San Francisco": "SFO",
}


@dataclass
class Recommendation:
    city: str
    market_date: date
    consensus_spread: float
    nbs_pred: float
    gfs_pred: float
    hrrr_pred: float
    nbs_fav_bucket: str
    plus1_bucket: str
    plus1_slug: str
    plus1_yes_token_id: str
    plus1_no_token_id: str
    yes_price_estimate: float | None
    no_ask_estimate: float | None
    est_edge_pct: float | None
    est_expected_pnl_usd: float | None
    note: str


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def load_features_for_date(target_date: date) -> pd.DataFrame:
    """Load per-station forecasts for a given local_date."""
    feat_path = REPO / "data" / "processed" / "backtest_v3" / "features.parquet"
    if not feat_path.exists():
        sys.exit(f"ERROR: {feat_path} missing. Run backtest-v3 feature build first.")
    df = pd.read_parquet(feat_path)
    df["local_date"] = pd.to_datetime(df["local_date"]).dt.date
    df = df[df["local_date"] == target_date]
    return df


def load_markets_for_date(target_date: date) -> pd.DataFrame:
    """Load Polymarket daily-temp markets resolving on target_date."""
    con = _con()
    q = f"""
        SELECT slug, city, yes_token_id, no_token_id,
               group_item_threshold AS bucket_idx,
               group_item_title,
               DATE(end_date) AS market_date,
               active, closed, best_bid, best_ask
        FROM '{REPO}/data/processed/polymarket_weather/markets.parquet'
        WHERE weather_tags ILIKE '%Daily Temperature%'
          AND DATE(end_date) = '{target_date}'
    """
    df = con.execute(q).fetch_df()
    return df


def parse_bucket(title: str) -> tuple[float, float, float]:
    import re
    m = re.match(r"^(\d+)-(\d+)°F$", title)
    if m:
        lo, hi = int(m[1]), int(m[2])
        return (float(lo), float(hi), (lo + hi) / 2.0)
    m = re.match(r"^(\d+)°F or below$", title)
    if m:
        hi = int(m[1])
        return (float("-inf"), float(hi), float(hi - 1))
    m = re.match(r"^(\d+)°F or higher$", title)
    if m:
        lo = int(m[1])
        return (float(lo), float("inf"), float(lo + 1))
    return (float("nan"), float("nan"), float("nan"))


def build_recommendations(target_date: date, consensus_max: float,
                          min_yes_price: float, max_yes_price: float,
                          stake_per_trade_usd: float) -> list[Recommendation]:
    feat = load_features_for_date(target_date)
    mkt = load_markets_for_date(target_date)

    if feat.empty:
        print(f"WARN: no features for {target_date}. Feature pipeline may be stale.", file=sys.stderr)
        return []
    if mkt.empty:
        print(f"WARN: no Polymarket markets for {target_date}. Refresh markets.parquet.", file=sys.stderr)
        return []

    # Parse bucket metadata
    parsed = mkt["group_item_title"].apply(parse_bucket)
    mkt["bucket_low"] = parsed.apply(lambda t: t[0])
    mkt["bucket_high"] = parsed.apply(lambda t: t[1])
    mkt["bucket_center"] = parsed.apply(lambda t: t[2])
    mkt = mkt.dropna(subset=["bucket_center"])

    # Station mapping
    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    feat["city"] = feat["station"].map(station_to_city)

    recs: list[Recommendation] = []
    for city, city_mkts in mkt.groupby("city"):
        feat_row = feat[feat["city"] == city]
        if feat_row.empty:
            continue
        fr = feat_row.iloc[0]
        nbs_pred = fr.get("nbs_pred_max_f")
        gfs_pred = fr.get("gfs_pred_max_f")
        hrrr_pred = fr.get("hrrr_max_t_f")
        if pd.isna(nbs_pred) or pd.isna(gfs_pred) or pd.isna(hrrr_pred):
            continue
        consensus = float(max(nbs_pred, gfs_pred, hrrr_pred)
                          - min(nbs_pred, gfs_pred, hrrr_pred))
        if consensus > consensus_max:
            continue

        # Find NBS favorite bucket (center closest to NBS prediction)
        diffs = (city_mkts["bucket_center"] - nbs_pred).abs()
        nbs_fav_row = city_mkts.loc[diffs.idxmin()]
        nbs_fav_idx = int(nbs_fav_row["bucket_idx"])
        plus1 = city_mkts[city_mkts["bucket_idx"] == nbs_fav_idx + 1]
        if plus1.empty:
            continue
        p1 = plus1.iloc[0]

        # Estimate YES price from markets.parquet best_bid/best_ask (may be stale)
        yes_bid = float(p1["best_bid"]) if pd.notna(p1["best_bid"]) else None
        yes_ask = float(p1["best_ask"]) if pd.notna(p1["best_ask"]) else None
        yes_mid = ((yes_bid + yes_ask) / 2.0
                   if (yes_bid is not None and yes_ask is not None) else None)

        note = ""
        if yes_mid is None:
            note = "NO PRICE DATA — pull fresh quote before trading"
        elif yes_mid < min_yes_price:
            continue  # skip: tick-floor dust
        elif yes_mid > max_yes_price:
            continue  # skip: price region where edge vanishes

        no_ask = (1 - yes_bid) if yes_bid is not None else None
        # Edge model: expected hit rate 97%, actual fair NO price = 0.97
        est_edge_pct = None
        est_pnl = None
        if no_ask is not None:
            fair_no = 0.97
            est_edge_pct = (fair_no - no_ask) * 100
            # Expected per-share PnL at stake_per_trade_usd capital
            shares = stake_per_trade_usd / no_ask
            est_pnl = shares * (0.97 - no_ask - 0.05 * no_ask * (1 - no_ask))

        recs.append(Recommendation(
            city=city, market_date=target_date,
            consensus_spread=consensus,
            nbs_pred=float(nbs_pred), gfs_pred=float(gfs_pred), hrrr_pred=float(hrrr_pred),
            nbs_fav_bucket=nbs_fav_row["group_item_title"],
            plus1_bucket=p1["group_item_title"],
            plus1_slug=p1["slug"],
            plus1_yes_token_id=p1["yes_token_id"],
            plus1_no_token_id=p1["no_token_id"],
            yes_price_estimate=yes_mid,
            no_ask_estimate=no_ask,
            est_edge_pct=est_edge_pct,
            est_expected_pnl_usd=est_pnl,
            note=note,
        ))
    return recs


def print_recommendations(recs: list[Recommendation], target_date: date,
                          consensus_max: float, stake: float) -> None:
    print("=" * 100)
    print(f"Consensus-Fade +1 Offset — Recommendations for {target_date}")
    print(f"Filter: consensus_spread ≤ {consensus_max}°F  |  stake/trade = ${stake}")
    print("=" * 100)
    if not recs:
        print("No recommendations. Either no consensus-tight cities today, "
              "or +1 bucket missing / priced out of range.")
        return
    print(f"{'city':<16} {'cs':>5}  {'NBS/GFS/HRRR':>14}  {'fav':>8}  {'+1 bucket':>11}  "
          f"{'yes_mid':>8} {'no_ask':>7} {'est_edge':>8} {'est_PnL':>8}  note")
    print("-" * 110)
    for r in recs:
        fcasts = f"{r.nbs_pred:.0f}/{r.gfs_pred:.0f}/{r.hrrr_pred:.0f}"
        yes_m = f"{r.yes_price_estimate:.3f}" if r.yes_price_estimate is not None else "—"
        no_a = f"{r.no_ask_estimate:.3f}" if r.no_ask_estimate is not None else "—"
        edge = f"{r.est_edge_pct:+.1f}pp" if r.est_edge_pct is not None else "—"
        pnl = f"${r.est_expected_pnl_usd:+.2f}" if r.est_expected_pnl_usd is not None else "—"
        print(f"{r.city:<16} {r.consensus_spread:>4.1f}  {fcasts:>14}  "
              f"{r.nbs_fav_bucket:>8}  {r.plus1_bucket:>11}  "
              f"{yes_m:>8} {no_a:>7} {edge:>8} {pnl:>8}  {r.note}")
    print()
    print(f"Total: {len(recs)} recommendations")
    total_est_pnl = sum(r.est_expected_pnl_usd or 0 for r in recs)
    print(f"Expected total PnL if all filled at quoted ask: ${total_est_pnl:+.2f}")
    print()
    print("Before placing orders:")
    print("  1. Pull fresh L2 book for each bucket — estimates here may be stale")
    print("  2. Confirm depth at intended stake within 2¢ of best ask")
    print("  3. Place limit at (best_yes_bid + 0.01); step toward ask if unfilled")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--consensus-max", type=float, default=DEFAULT_CONSENSUS_MAX_F)
    ap.add_argument("--min-yes-price", type=float, default=DEFAULT_MIN_YES_PRICE)
    ap.add_argument("--max-yes-price", type=float, default=DEFAULT_MAX_YES_PRICE)
    ap.add_argument("--stake-per-trade", type=float, default=20.0)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        target_date = datetime.now(UTC).date()

    recs = build_recommendations(
        target_date=target_date,
        consensus_max=args.consensus_max,
        min_yes_price=args.min_yes_price,
        max_yes_price=args.max_yes_price,
        stake_per_trade_usd=args.stake_per_trade,
    )
    print_recommendations(recs, target_date, args.consensus_max, args.stake_per_trade)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.write_text(json.dumps([r.__dict__ for r in recs], default=str, indent=2))
        print(f"\nWrote JSON: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

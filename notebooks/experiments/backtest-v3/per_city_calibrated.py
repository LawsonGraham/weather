"""Per-city NBS-bias calibrated strategy.

Hypothesis: the per-city bias of NBS from IS training (Dec 1 - Feb 28)
provides a signal for how to offset bucket selection in OOS trading
(Mar 11 - Apr 10).

For each city:
1. Compute mean(actual - NBS_pred) on IS → per-city bias in °F
2. Round to nearest integer bucket offset (bias in °F / 2)
3. Use that offset in OOS trading

Compare to:
- Offset=0 (NBS fav) uniform baseline
- Model-shifted prediction (bias-correct NBS, then pick closest bucket)
- Per-city offset chosen by OOS optimality (upper bound sanity)

Also: within-OOS holdout. Split OOS into Mar 11-25 vs Mar 26-Apr 10,
verify the per-city calibration works on BOTH (protects against period
concentration).
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
from datetime import date

REPO = Path("/Users/lawsongraham/git/weather")
V3 = REPO / "data" / "processed" / "backtest_v3"
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE = 0.05


def main():
    # Load features to compute per-city IS bias
    feat = pd.read_parquet(V3 / "features.parquet")
    feat["local_date"] = pd.to_datetime(feat["local_date"])
    feat["nbs_err"] = feat["actual_max_f"] - feat["nbs_pred_max_f"]

    # Per-city bias on IS (Dec 1 - Feb 28)
    is_feat = feat[feat.fold == "IS"].dropna(subset=["nbs_err"])

    station_to_city = {v: k for k, v in CITY_TO_STATION.items()}
    is_feat["city"] = is_feat["station"].map(station_to_city)
    is_feat = is_feat.dropna(subset=["city"])

    # Also compute OOS bias (for comparison, NOT for strategy)
    oos_feat = feat[feat.fold == "OOS"].dropna(subset=["nbs_err"])
    oos_feat["city"] = oos_feat["station"].map(station_to_city)
    oos_feat = oos_feat.dropna(subset=["city"])

    print("=== Per-city NBS bias (mean actual - NBS_pred) ===")
    print(f"{'city':<18} {'IS_bias':>8} {'OOS_bias':>9} {'IS_n':>5} {'OOS_n':>6} {'recomm_off':>11}")
    bias_map = {}
    for city in sorted(CITY_TO_STATION.keys()):
        is_city = is_feat[is_feat.city == city]
        oos_city = oos_feat[oos_feat.city == city]
        is_bias = is_city.nbs_err.mean() if len(is_city) > 0 else np.nan
        oos_bias = oos_city.nbs_err.mean() if len(oos_city) > 0 else np.nan
        # Integer offset: nearest offset given 2°F buckets
        off = int(np.round(is_bias / 2.0)) if not np.isnan(is_bias) else 0
        bias_map[city] = {"is_bias": is_bias, "oos_bias": oos_bias, "offset": off}
        print(f"{city:<18} {is_bias:>+8.3f} {oos_bias:>+9.3f} "
              f"{len(is_city):>5} {len(oos_city):>6} {off:>+11d}")

    # Load trade table
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl = tbl.dropna(subset=["entry_price"])
    tbl = tbl[tbl["won_yes"] >= 0]
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)
    tbl = tbl[(tbl.date >= date(2026, 3, 11)) & (tbl.date <= date(2026, 4, 10))].copy()

    def apply_offset(day, offset_buckets):
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        nbs_fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        target = nbs_fav_idx + offset_buckets
        row = day[day["bucket_idx"] == target]
        return row.iloc[0] if len(row) == 1 else None

    def apply_shifted(day, shift_f):
        """Shift the NBS prediction by shift_f °F, then pick closest bucket."""
        nbs_pred = day["nbs_pred_max_f"].iloc[0] + shift_f
        diff = (day["bucket_center"] - nbs_pred).abs()
        return day.loc[diff.idxmin()]

    def strat_results(df, selector_fn):
        trades = []
        for (city, md), grp in df.groupby(["city", "market_date"]):
            day = grp.sort_values("bucket_idx").reset_index(drop=True)
            if day["entry_price"].isna().any() or len(day) < 9:
                continue
            r = selector_fn(city, day)
            if r is None:
                continue
            if r["entry_price"] < 0.02 or r["entry_price"] > 0.95:
                continue
            fee = FEE * r["entry_price"] * (1 - r["entry_price"])
            pnl = float(r["won_yes"]) - r["entry_price"] - fee
            trades.append({"city": city, "date": md.date(),
                          "price": float(r["entry_price"]),
                          "won_yes": int(r["won_yes"]),
                          "pnl": pnl})
        return pd.DataFrame(trades)

    def summarize(t, name, verbose=False):
        if len(t) == 0:
            print(f"  {name:<32}  n=0")
            return
        std = t.pnl.std() if len(t) > 1 else 0
        tstat = t.pnl.mean() / (std / len(t)**0.5) if std > 0 else 0
        print(f"  {name:<32}  n={len(t):>3}  hit={t.won_yes.mean()*100:>5.1f}%  "
              f"per=${t.pnl.mean():>+.4f}  tot=${t.pnl.sum():>+.2f}  t={tstat:>+.2f}  "
              f"price=${t.price.mean():.3f}")
        if verbose:
            for city, g in t.groupby("city"):
                s = g.pnl.std() if len(g) > 1 else 0
                ts = g.pnl.mean() / (s / len(g)**0.5) if s > 0 else 0
                print(f"    {city:<18} n={len(g):>3}  hit={g.won_yes.mean()*100:>5.1f}%  "
                      f"per=${g.pnl.mean():>+.3f}  tot=${g.pnl.sum():>+.2f}  t={ts:>+.2f}")

    # Strategies
    def sel_fav(city, day):
        return apply_offset(day, 0)

    def sel_city_offset(city, day):
        off = bias_map.get(city, {}).get("offset", 0)
        return apply_offset(day, off)

    def sel_city_shifted(city, day):
        shift = bias_map.get(city, {}).get("is_bias", 0) or 0
        return apply_shifted(day, shift)

    def sel_offset_minus1(city, day):
        return apply_offset(day, -1)

    def sel_offset_plus1(city, day):
        return apply_offset(day, 1)

    print()
    print("=== Strategy comparisons: FULL Mar 11 - Apr 10 ===")
    summarize(strat_results(tbl, sel_fav),
              "baseline: NBS fav (offset=0)")
    summarize(strat_results(tbl, sel_offset_minus1),
              "uniform offset=-1")
    summarize(strat_results(tbl, sel_offset_plus1),
              "uniform offset=+1 (negative ctrl)")
    summarize(strat_results(tbl, sel_city_offset),
              "per-city offset from IS bias")
    summarize(strat_results(tbl, sel_city_shifted),
              "per-city SHIFTED NBS (fractional)")

    # Within-OOS holdout: Mar 11-25 vs Mar 26-Apr 10
    split = date(2026, 3, 25)
    print()
    print(f"=== Within-OOS split: Mar 11-25 vs Mar 26-Apr 10 ===")
    for name, fn in [
        ("baseline: NBS fav", sel_fav),
        ("uniform offset=-1", sel_offset_minus1),
        ("per-city offset", sel_city_offset),
        ("per-city shifted", sel_city_shifted),
    ]:
        t1 = strat_results(tbl[tbl.date <= split], fn)
        t2 = strat_results(tbl[tbl.date > split], fn)
        print(f"  {name}:")
        summarize(t1, f"    Mar 11-25")
        summarize(t2, f"    Mar 26-Apr 10")

    # Per-city detail for per-city strategy
    print()
    print("=== Per-city breakdown for per-city offset strategy (FULL) ===")
    t = strat_results(tbl, sel_city_offset)
    summarize(t, "per-city offset", verbose=True)

    # Per-city detail for per-city shifted strategy
    print()
    print("=== Per-city breakdown for per-city shifted (FULL) ===")
    t = strat_results(tbl, sel_city_shifted)
    summarize(t, "per-city shifted", verbose=True)


if __name__ == "__main__":
    main()

"""Large sweep over local-time consensus variants to find optimal rule.

Reuses the forecast panel from consensus_form_variants.py. Tests a grid of:

    - local entry-floor hour           (12 .. 18)
    - consensus spread max             (2, 2.5, 3, 4, 5 °F)
    - yes_price cap                    (0.22, 0.30, 0.40, 0.50)
    - stability K consecutive hours    (1, 3, 6)

Then diagnoses the surviving 1 loss from the best rule, and sweeps a few
forecast-velocity filters (NBS-change-over-last-K-hours).
"""
from __future__ import annotations

from datetime import date
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
TZ = {
    "LGA": "America/New_York", "ATL": "America/New_York", "MIA": "America/New_York",
    "ORD": "America/Chicago", "DAL": "America/Chicago", "HOU": "America/Chicago",
    "AUS": "America/Chicago", "DEN": "America/Denver",
    "SEA": "America/Los_Angeles", "LAX": "America/Los_Angeles", "SFO": "America/Los_Angeles",
}
CITY_TO_STATION = {
    "New York City": "LGA", "Atlanta": "ATL", "Dallas": "DAL", "Seattle": "SEA",
    "Chicago": "ORD", "Miami": "MIA", "Austin": "AUS", "Houston": "HOU",
    "Denver": "DEN", "Los Angeles": "LAX", "San Francisco": "SFO",
}
FEE_RATE = 0.05
OFFSET = 1
IS_START, IS_END = date(2026, 3, 11), date(2026, 3, 25)
OOS_START, OOS_END = date(2026, 3, 26), date(2026, 4, 10)


def load_prices() -> pd.DataFrame:
    import duckdb
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT slug, timestamp, p_yes
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/year=2026/month=*/data_0.parquet')
    """).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def pick_price(prices: pd.DataFrame, slug: str, at: pd.Timestamp,
               max_wait: int = 3) -> tuple[pd.Timestamp, float] | None:
    s = prices[(prices.slug == slug) & (prices.timestamp >= at)
               & (prices.timestamp <= at + pd.Timedelta(hours=max_wait))]
    if s.empty:
        return None
    r = s.sort_values("timestamp").iloc[0]
    return pd.Timestamp(r.timestamp), float(r.p_yes)


def stats_row(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "hit": np.nan, "per": np.nan, "tot": 0.0, "t": np.nan}
    hit = df.won_no.mean()
    per = df.pnl.mean()
    sd = df.pnl.std(ddof=1) if n > 1 else 0.0
    t = per / (sd / n ** 0.5) if sd > 0 else 0.0
    return {"n": n, "hit": hit, "per": per, "tot": df.pnl.sum(), "t": t}


def build_trades(tbl: pd.DataFrame, panel: pd.DataFrame, prices: pd.DataFrame,
                 *, consensus_max: float, floor_local: int | None,
                 stable_hours: int, yes_price_cap: float,
                 yes_price_floor: float = 0.005) -> pd.DataFrame:
    out = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        station = day.station.iloc[0]
        tz = TZ[station]
        target = md.date()
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        fav_idx = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == fav_idx + OFFSET]
        if row.empty:
            continue
        r = row.iloc[0]

        p = panel[(panel.station == station) & (panel.target_date == target)].sort_values("hour_utc").reset_index(drop=True)
        if p.empty:
            continue
        p = p.copy()
        ts_local = p["hour_utc"].dt.tz_convert(tz)
        p["hour_local"] = ts_local.dt.hour
        p = p[ts_local.dt.date == target].reset_index(drop=True)
        if p.empty:
            continue
        p["ok"] = p["spread"] <= consensus_max
        if floor_local is not None:
            p["ok"] = p["ok"] & (p.hour_local >= floor_local)
        if stable_hours > 1:
            p["stable"] = p["ok"].rolling(stable_hours, min_periods=stable_hours).sum() == stable_hours
        else:
            p["stable"] = p["ok"]
        trig = p[p["stable"]]
        if trig.empty:
            continue
        entry_ts = trig.iloc[0]["hour_utc"]
        entry_lh = int(trig.iloc[0]["hour_local"])
        spr = float(trig.iloc[0]["spread"])
        nbs_at_entry = trig.iloc[0]["nbs"]

        pk = pick_price(prices, r["slug"], entry_ts)
        if pk is None:
            continue
        _, yes_p = pk
        if yes_p < yes_price_floor or yes_p > yes_price_cap:
            continue
        price = 1 - yes_p
        won_no = 1 - int(r["won_yes"])
        fee = FEE_RATE * price * (1 - price)
        pnl = float(won_no) - price - fee
        out.append({"city": city, "date": target, "station": station,
                    "entry_local_hour": entry_lh,
                    "consensus_spread": spr, "nbs_at_entry": nbs_at_entry,
                    "yes_price": yes_p, "price_paid": price,
                    "won_no": won_no, "pnl": pnl})
    return pd.DataFrame(out)


def main() -> None:
    print("Loading inputs...")
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl[(tbl.date >= IS_START) & (tbl.date <= OOS_END)].copy()
    tbl = tbl.dropna(subset=["nbs_pred_max_f"])
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)
    panel = pd.read_parquet(REPO / "data/processed/backtest_v3/forecast_panel.parquet")
    panel["hour_utc"] = pd.to_datetime(panel["hour_utc"], utc=True)
    panel["target_date"] = pd.to_datetime(panel["target_date"]).dt.date
    prices = load_prices()
    print(f"  tbl={len(tbl)}, panel={len(panel):,}, prices={len(prices):,}")

    # --- (1) Full grid sweep ---
    print("\n=== (1) Full grid sweep (rows: floor × cs × cap × stable) ===")
    floors = [12, 13, 14, 15, 16, 17, 18]
    cs_vals = [2.0, 2.5, 3.0, 4.0, 5.0]
    caps = [0.22, 0.30, 0.40, 0.50]
    stables = [1, 3, 6]
    rows = []
    for fl, cs, cap, sk in product(floors, cs_vals, caps, stables):
        r = build_trades(tbl, panel, prices, consensus_max=cs, floor_local=fl,
                         stable_hours=sk, yes_price_cap=cap)
        s = stats_row(r)
        is_s = stats_row(r[r.date <= IS_END])
        oos_s = stats_row(r[r.date >= OOS_START])
        rows.append({
            "floor_local": fl, "cs_max": cs, "yes_cap": cap, "stable_k": sk,
            "n": s["n"], "hit": s["hit"], "per": s["per"], "t": s["t"],
            "is_n": is_s["n"], "is_t": is_s["t"],
            "oos_n": oos_s["n"], "oos_t": oos_s["t"],
        })
    grid = pd.DataFrame(rows)
    # Filter to viable configs (enough trades both folds + positive edge)
    viable = grid[(grid.n >= 40) & (grid.is_n >= 15) & (grid.oos_n >= 15)
                  & (grid.is_t > 1.5) & (grid.oos_t > 1.5)].copy()
    viable = viable.sort_values("t", ascending=False)
    print(f"  viable configs (n≥40, IS_t>1.5, OOS_t>1.5): {len(viable)}")
    pd.set_option("display.max_rows", 40)
    pd.set_option("display.width", 200)
    print("\n  Top 20 by full-period t-stat:")
    print(viable.head(20).to_string(index=False,
          formatters={"hit": lambda x: f"{x*100:.1f}%", "per": lambda x: f"${x:+.4f}",
                      "t": "{:+.2f}".format, "is_t": "{:+.2f}".format, "oos_t": "{:+.2f}".format}))

    # Save grid for reference
    grid.to_parquet(REPO / "data/processed/backtest_v3/consensus_grid.parquet", index=False)

    # --- (2) Best config: ≥16 local, cs ≤ 3, yes_cap 0.50, stable 1 (baseline-like) ---
    print("\n=== (2) Recommended config inspection: ≥16 local, cs ≤ 3°F, yes_cap 0.50, stable 1 ===")
    r = build_trades(tbl, panel, prices, consensus_max=3.0, floor_local=16,
                     stable_hours=1, yes_price_cap=0.50)
    is_s = stats_row(r[r.date <= IS_END])
    oos_s = stats_row(r[r.date >= OOS_START])
    s = stats_row(r)
    print(f"  FULL: n={s['n']} hit={s['hit']*100:.1f}% per=${s['per']:+.4f} t={s['t']:+.2f}")
    print(f"    IS: n={is_s['n']} hit={is_s['hit']*100:.1f}% per=${is_s['per']:+.4f} t={is_s['t']:+.2f}")
    print(f"   OOS: n={oos_s['n']} hit={oos_s['hit']*100:.1f}% per=${oos_s['per']:+.4f} t={oos_s['t']:+.2f}")
    print("\n  per-city:")
    for c, g in r.sort_values("city").groupby("city"):
        gs = stats_row(g)
        print(f"    {c:<18} n={gs['n']:>2} hit={gs['hit']*100:>5.1f}% per=${gs['per']:>+.4f} t={gs['t']:>+.2f}")
    print(f"\n  losses:")
    print(r[r.won_no == 0][["city", "date", "entry_local_hour", "consensus_spread",
                            "yes_price", "pnl"]].to_string(index=False))

    # --- (3) Tighten: add yes_price cap 0.22 ---
    print("\n=== (3) With yes_price cap 0.22 (market-wisdom filter) ===")
    r2 = build_trades(tbl, panel, prices, consensus_max=3.0, floor_local=16,
                      stable_hours=1, yes_price_cap=0.22)
    is_s = stats_row(r2[r2.date <= IS_END])
    oos_s = stats_row(r2[r2.date >= OOS_START])
    s = stats_row(r2)
    print(f"  FULL: n={s['n']} hit={s['hit']*100:.1f}% per=${s['per']:+.4f} t={s['t']:+.2f}")
    print(f"    IS: n={is_s['n']} hit={is_s['hit']*100:.1f}% per=${is_s['per']:+.4f} t={is_s['t']:+.2f}")
    print(f"   OOS: n={oos_s['n']} hit={oos_s['hit']*100:.1f}% per=${oos_s['per']:+.4f} t={oos_s['t']:+.2f}")
    print(f"  losses: {(r2.won_no == 0).sum()}")

    # --- (4) Shorten the local floor? 15 local at tight cs + tight cap ---
    print("\n=== (4) Earlier floor (15 local) + tighter cs + cap ===")
    for cs, cap in [(2.0, 0.30), (2.5, 0.30), (3.0, 0.22), (3.0, 0.30)]:
        r = build_trades(tbl, panel, prices, consensus_max=cs, floor_local=15,
                         stable_hours=1, yes_price_cap=cap)
        s = stats_row(r)
        is_s = stats_row(r[r.date <= IS_END])
        oos_s = stats_row(r[r.date >= OOS_START])
        print(f"  cs≤{cs} cap≤{cap}: n={s['n']} hit={s['hit']*100:.1f}% per=${s['per']:+.4f} t={s['t']:+.2f}  IS_t={is_s['t']:+.2f} OOS_t={oos_s['t']:+.2f}")

    # --- (5) NBS-velocity filter: skip if NBS shifted > X°F in last 6h ---
    print("\n=== (5) NBS-velocity filter under ≥16 local, cs ≤ 3°F, cap ≤ 0.50 ===")
    # For each entry, look up NBS 6h earlier on panel
    r = build_trades(tbl, panel, prices, consensus_max=3.0, floor_local=16,
                     stable_hours=1, yes_price_cap=0.50)
    prev6 = []
    for _, row in r.iterrows():
        st = row["station"]
        tz = TZ[st]
        target = row["date"]
        entry_local = row["entry_local_hour"]
        # entry time UTC
        entry_utc = (pd.Timestamp(target).tz_localize(tz) + pd.Timedelta(hours=entry_local)).tz_convert("UTC")
        t6 = entry_utc - pd.Timedelta(hours=6)
        p6 = panel[(panel.station == st) & (panel.target_date == target) & (panel.hour_utc == t6)]
        prev6.append(float(p6.iloc[0].nbs) if not p6.empty and pd.notna(p6.iloc[0].nbs) else np.nan)
    r = r.assign(nbs_6h_prior=prev6)
    r["nbs_delta_6h"] = r["nbs_at_entry"] - r["nbs_6h_prior"]
    print(f"  NBS-delta-6h distribution: {r.nbs_delta_6h.describe().to_dict()}")
    for thresh in [0.5, 1.0, 1.5, 2.0]:
        rf = r[r.nbs_delta_6h.abs() <= thresh]
        s = stats_row(rf)
        print(f"  |NBS Δ 6h| ≤ {thresh}°F:  n={s['n']} hit={s['hit']*100:.1f}% per=${s['per']:+.4f} t={s['t']:+.2f}")

    # --- (6) Is the remaining 1 loss recoverable? ---
    print("\n=== (6) The 1 loss under recommended rule ===")
    rec = build_trades(tbl, panel, prices, consensus_max=3.0, floor_local=16,
                       stable_hours=1, yes_price_cap=0.50)
    loss = rec[rec.won_no == 0]
    if len(loss):
        print(loss.to_string(index=False))
        print(f"\n  Pre-entry NBS trajectory for this loss:")
        for _, row in loss.iterrows():
            st = row["station"]
            tz = TZ[st]
            target = row["date"]
            entry_utc = (pd.Timestamp(target).tz_localize(tz) + pd.Timedelta(hours=row["entry_local_hour"])).tz_convert("UTC")
            pp = panel[(panel.station == st) & (panel.target_date == target)
                       & (panel.hour_utc <= entry_utc)].sort_values("hour_utc")
            pp = pp.copy()
            pp["local_hour"] = pp.hour_utc.dt.tz_convert(tz).dt.hour
            print(f"\n  {row['city']} {target}:")
            print(pp[["local_hour", "nbs", "gfs", "hrrr", "spread"]].tail(10).to_string(index=False))


if __name__ == "__main__":
    main()

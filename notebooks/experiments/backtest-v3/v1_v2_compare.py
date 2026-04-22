"""Clean side-by-side of v1 vs v2-without-market-cap vs v2-with-cap.

All variants use the SAME price source (Polymarket hourly prices) so
per-trade numbers are comparable. The v1 row in STRATEGY.md used a
different snapshot column from trade_table.parquet — here we rerun
"20 UTC + cap 0.50" against hourly prices for apples-to-apples.

Variants:
    v1_hourly: 20 UTC fixed entry, cs ≤ 3°F, YES cap 0.50
    v2_local_loose: ≥16 local, cs ≤ 3°F, YES cap 0.50, HRRR any-coverage
    v2_local_gated: ≥16 local, cs ≤ 3°F, YES cap 0.50, HRRR 6h coverage
    v2_mkt_cap:    ≥16 local, cs ≤ 3°F, YES cap 0.22, HRRR 6h coverage
    v1_canonical (from STRATEGY.md): 94 trades, 98.9% hit, +$0.083, t=+4.44
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
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
IS_START, IS_END = date(2026, 3, 11), date(2026, 3, 25)
OOS_START, OOS_END = date(2026, 3, 26), date(2026, 4, 10)


def load_mos(model: str) -> pd.DataFrame:
    con = duckdb.connect()
    col = "txn_f" if model == "NBS" else "n_x_f"
    df = con.execute(f"""
        SELECT station, runtime, ftime, {col} AS n_x_f
        FROM read_parquet('{REPO}/data/processed/iem_mos/{model}/*.parquet')
        WHERE {col} IS NOT NULL
    """).df()
    df["runtime"] = pd.to_datetime(df["runtime"], utc=True)
    df["ftime"] = pd.to_datetime(df["ftime"], utc=True)
    df["station"] = df["station"].str.removeprefix("K")
    return df


def load_hrrr() -> pd.DataFrame:
    con = duckdb.connect()
    rows = []
    for st in TZ:
        try:
            sub = con.execute(f"""
                SELECT init_time, valid_time, t2m_heightAboveGround_2 AS t_k
                FROM read_parquet('{REPO}/data/raw/hrrr/K{st}/hourly.parquet')
                WHERE t2m_heightAboveGround_2 IS NOT NULL
            """).df()
        except Exception:
            continue
        sub["station"] = st
        sub["init_time"] = pd.to_datetime(sub["init_time"], utc=True)
        sub["valid_time"] = pd.to_datetime(sub["valid_time"], utc=True)
        sub["t_f"] = (sub["t_k"] - 273.15) * 9 / 5 + 32
        rows.append(sub[["station", "init_time", "valid_time", "t_f"]])
    return pd.concat(rows, ignore_index=True)


def load_prices() -> pd.DataFrame:
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT slug, timestamp, p_yes
        FROM read_parquet('{REPO}/data/processed/polymarket_prices_history/hourly/year=2026/month=*/data_0.parquet')
    """).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def stats(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "hit": np.nan, "per": np.nan, "tot": 0.0, "t": np.nan, "wins": 0, "losses": 0}
    hit = df.won_no.mean()
    per = df.pnl.mean()
    sd = df.pnl.std(ddof=1) if n > 1 else 0.0
    t = per / (sd / n ** 0.5) if sd > 0 else 0.0
    return {"n": n, "hit": hit, "per": per, "tot": df.pnl.sum(), "t": t,
            "wins": int(df.won_no.sum()), "losses": int(n - df.won_no.sum())}


def nbs_gfs_at(sub: pd.DataFrame, t: pd.Timestamp, ps: pd.Timestamp, pe: pd.Timestamp) -> float | None:
    s = sub[(sub.runtime <= t) & (sub.runtime >= t - pd.Timedelta(hours=24))
            & (sub.ftime >= ps) & (sub.ftime <= pe)]
    if s.empty:
        return None
    return float(s[s.runtime == s.runtime.max()].n_x_f.max())


def hrrr_at(sub: pd.DataFrame, t: pd.Timestamp, ps: pd.Timestamp, pe: pd.Timestamp,
            min_cov: int) -> float | None:
    s = sub[(sub.init_time <= t) & (sub.valid_time >= ps) & (sub.valid_time <= pe)]
    if s.empty:
        return None
    latest = s.sort_values("init_time").groupby("valid_time").tail(1)
    if min_cov > 0 and latest.valid_time.dt.hour.nunique() < min_cov:
        return None
    return float(latest.t_f.max())


def pick_price(prices: pd.DataFrame, slug: str, at: pd.Timestamp,
               max_wait: int = 3) -> float | None:
    s = prices[(prices.slug == slug) & (prices.timestamp >= at)
               & (prices.timestamp <= at + pd.Timedelta(hours=max_wait))]
    if s.empty:
        return None
    return float(s.sort_values("timestamp").iloc[0].p_yes)


def run_variant(tbl, nbs, gfs, hrrr, prices, *, mode: str, yes_cap: float,
                hrrr_min_cov: int, consensus_max: float = 3.0):
    """mode in {'fixed_20utc', 'local_floor_16'}"""
    rows = []
    for (city, md), grp in tbl.groupby(["city", "market_date"]):
        day = grp.sort_values("bucket_idx").reset_index(drop=True)
        if day["entry_price"].isna().any() or len(day) < 9:
            continue
        station = day.station.iloc[0]
        tz = TZ[station]
        target = md.date()
        nbs_pred = day["nbs_pred_max_f"].iloc[0]
        diff = (day["bucket_center"] - nbs_pred).abs()
        fav = int(day.loc[diff.idxmin(), "bucket_idx"])
        row = day[day["bucket_idx"] == fav + 1]
        if row.empty:
            continue
        r = row.iloc[0]

        pk_s = (pd.Timestamp(target).tz_localize(tz) + pd.Timedelta(hours=12)).tz_convert("UTC")
        pk_e = (pd.Timestamp(target).tz_localize(tz) + pd.Timedelta(hours=22)).tz_convert("UTC")
        nbs_st = nbs[nbs.station == station]
        gfs_st = gfs[gfs.station == station]
        hrrr_st = hrrr[hrrr.station == station]

        if mode == "fixed_20utc":
            entry_ts = pd.Timestamp(target).tz_localize("UTC") + pd.Timedelta(hours=20)
            n = nbs_gfs_at(nbs_st, entry_ts, pk_s, pk_e)
            g = nbs_gfs_at(gfs_st, entry_ts, pk_s, pk_e)
            h = hrrr_at(hrrr_st, entry_ts, pk_s, pk_e, hrrr_min_cov)
            if n is None or g is None or h is None:
                continue
            if max(n, g, h) - min(n, g, h) > consensus_max:
                continue
        elif mode == "local_floor_16":
            local_midnight_utc = pd.Timestamp(target).tz_localize(tz).tz_convert("UTC")
            entry_ts = None
            for hr in range(16, 24):
                t = local_midnight_utc + pd.Timedelta(hours=hr)
                n = nbs_gfs_at(nbs_st, t, pk_s, pk_e)
                g = nbs_gfs_at(gfs_st, t, pk_s, pk_e)
                h = hrrr_at(hrrr_st, t, pk_s, pk_e, hrrr_min_cov)
                if n is None or g is None or h is None:
                    continue
                if max(n, g, h) - min(n, g, h) <= consensus_max:
                    entry_ts = t
                    break
            if entry_ts is None:
                continue
        else:
            raise ValueError(mode)

        yes_p = pick_price(prices, r["slug"], entry_ts)
        if yes_p is None or yes_p < 0.005 or yes_p > yes_cap:
            continue
        price = 1 - yes_p
        won_no = 1 - int(r["won_yes"])
        fee = FEE_RATE * price * (1 - price)
        pnl = float(won_no) - price - fee
        rows.append({"city": city, "date": target, "station": station,
                     "yes_price": yes_p, "won_no": won_no, "pnl": pnl})
    return pd.DataFrame(rows)


def main() -> None:
    print("Loading...")
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl[(tbl.date >= IS_START) & (tbl.date <= OOS_END)].copy()
    tbl = tbl.dropna(subset=["nbs_pred_max_f"])
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)
    nbs = load_mos("NBS"); gfs = load_mos("GFS")
    hrrr = load_hrrr(); prices = load_prices()

    variants = [
        ("v1 — 20 UTC fixed, cap 0.50 (any HRRR coverage)",
         {"mode": "fixed_20utc", "yes_cap": 0.50, "hrrr_min_cov": 0}),
        ("v1 — 20 UTC fixed, cap 0.50, HRRR 6h coverage",
         {"mode": "fixed_20utc", "yes_cap": 0.50, "hrrr_min_cov": 6}),
        ("v2a — ≥16 local, cap 0.50, any HRRR coverage",
         {"mode": "local_floor_16", "yes_cap": 0.50, "hrrr_min_cov": 0}),
        ("v2b — ≥16 local, cap 0.50, HRRR 6h coverage",
         {"mode": "local_floor_16", "yes_cap": 0.50, "hrrr_min_cov": 6}),
        ("v2c — ≥16 local, cap 0.22, HRRR 6h coverage",
         {"mode": "local_floor_16", "yes_cap": 0.22, "hrrr_min_cov": 6}),
    ]
    out = {}
    for label, kwargs in variants:
        r = run_variant(tbl, nbs, gfs, hrrr, prices, **kwargs)
        s = stats(r)
        is_s = stats(r[r.date <= IS_END])
        oos_s = stats(r[r.date >= OOS_START])
        out[label] = (s, is_s, oos_s, r)

    print("\n=== Head-to-head ===\n")
    hdr = f"{'variant':<58} {'n':>4} {'W':>3} {'L':>3} {'hit%':>6} {'per':>9} {'tot':>8} {'t':>7} {'IS_t':>6} {'OOS_t':>6}"
    print(hdr)
    print("-" * len(hdr))
    for label, (s, is_s, oos_s, _) in out.items():
        print(f"{label:<58} {s['n']:>4} {s['wins']:>3} {s['losses']:>3} "
              f"{s['hit']*100:>5.1f}% ${s['per']:>+7.4f} ${s['tot']:>+6.2f} "
              f"{s['t']:>+6.2f} {is_s['t']:>+6.2f} {oos_s['t']:>+6.2f}")

    print("\n=== Per-city for v2b (≥16 local, cap 0.50) ===")
    _, _, _, r = out["v2b — ≥16 local, cap 0.50, HRRR 6h coverage"]
    for c, g in r.sort_values("city").groupby("city"):
        s = stats(g)
        print(f"  {c:<18} n={s['n']:>2} W={s['wins']:>2} L={s['losses']:>2} "
              f"hit={s['hit']*100:>5.1f}% per=${s['per']:>+.4f} t={s['t']:>+.2f}")

    print("\n=== Per-city for v1 (20 UTC fixed, cap 0.50) ===")
    _, _, _, r = out["v1 — 20 UTC fixed, cap 0.50 (any HRRR coverage)"]
    for c, g in r.sort_values("city").groupby("city"):
        s = stats(g)
        print(f"  {c:<18} n={s['n']:>2} W={s['wins']:>2} L={s['losses']:>2} "
              f"hit={s['hit']*100:>5.1f}% per=${s['per']:>+.4f} t={s['t']:>+.2f}")

    print("\n=== Losses in each variant ===")
    for label, (_, _, _, r) in out.items():
        losses = r[r.won_no == 0]
        print(f"\n  {label}: {len(losses)} losses")
        if len(losses):
            print(losses[["city", "date", "yes_price", "pnl"]].to_string(index=False))


if __name__ == "__main__":
    main()

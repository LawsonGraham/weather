"""Variants of time-resolved consensus-entry backtest.

Builds an (station, target_date, hour_utc) panel of NBS/GFS/HRRR peak
forecasts, joins to hourly Polymarket prices on the +1 bucket, then
evaluates several entry rules:

    A. First hour consensus ≤ X (HRRR full peak coverage required)
    B. First hour after T_floor with consensus ≤ X
    C. Require consensus stable for K consecutive hours
    D. Consensus-threshold sweep at different floors
    E. YES-price trajectory over the day (mean by hour)
    F. Of losses: did consensus break before fill or stay stable?
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
OFFSET = 1
YP_MIN, YP_MAX = 0.005, 0.5
IS_START, IS_END = date(2026, 3, 11), date(2026, 3, 25)
OOS_START, OOS_END = date(2026, 3, 26), date(2026, 4, 10)


def peak_utc(target: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    s = pd.Timestamp(target).tz_localize(tz)
    return (s + pd.Timedelta(hours=12)).tz_convert("UTC"), (s + pd.Timedelta(hours=22)).tz_convert("UTC")


def scan_utc(target: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    s = pd.Timestamp(target).tz_localize(tz)
    return (s + pd.Timedelta(hours=5)).tz_convert("UTC"), (s + pd.Timedelta(hours=24)).tz_convert("UTC")


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


def build_forecast_panel(nbs: pd.DataFrame, gfs: pd.DataFrame, hrrr: pd.DataFrame,
                         stations: list[str], dates: list[date],
                         min_hrrr_cov: int = 6) -> pd.DataFrame:
    """(station, target_date, hour_utc) -> (nbs_max, gfs_max, hrrr_max, spread, nbs_fav)."""
    rows = []
    for st in stations:
        tz = TZ[st]
        nbs_s = nbs[nbs.station == st]
        gfs_s = gfs[gfs.station == st]
        hrrr_s = hrrr[hrrr.station == st]
        for d in dates:
            pk_s, pk_e = peak_utc(d, tz)
            sc_s, sc_e = scan_utc(d, tz)
            hours = pd.date_range(sc_s.floor("h"), sc_e, freq="h", tz="UTC")
            for h in hours:
                # NBS
                nsub = nbs_s[(nbs_s.runtime <= h)
                             & (nbs_s.runtime >= h - pd.Timedelta(hours=24))
                             & (nbs_s.ftime >= pk_s) & (nbs_s.ftime <= pk_e)]
                nbs_m = float(nsub[nsub.runtime == nsub.runtime.max()].n_x_f.max()) if not nsub.empty else np.nan
                # GFS
                gsub = gfs_s[(gfs_s.runtime <= h)
                             & (gfs_s.runtime >= h - pd.Timedelta(hours=24))
                             & (gfs_s.ftime >= pk_s) & (gfs_s.ftime <= pk_e)]
                gfs_m = float(gsub[gsub.runtime == gsub.runtime.max()].n_x_f.max()) if not gsub.empty else np.nan
                # HRRR
                hsub = hrrr_s[(hrrr_s.init_time <= h)
                              & (hrrr_s.valid_time >= pk_s) & (hrrr_s.valid_time <= pk_e)]
                if hsub.empty:
                    hrrr_m = np.nan
                else:
                    latest = hsub.sort_values("init_time").groupby("valid_time").tail(1)
                    if latest.valid_time.dt.hour.nunique() < min_hrrr_cov:
                        hrrr_m = np.nan
                    else:
                        hrrr_m = float(latest.t_f.max())
                vals = [v for v in (nbs_m, gfs_m, hrrr_m) if not np.isnan(v)]
                spread = (max(vals) - min(vals)) if len(vals) == 3 else np.nan
                rows.append((st, d, h, nbs_m, gfs_m, hrrr_m, spread))
    panel = pd.DataFrame(rows, columns=["station", "target_date", "hour_utc",
                                        "nbs", "gfs", "hrrr", "spread"])
    return panel


def pick_price(prices: pd.DataFrame, slug: str, at: pd.Timestamp,
               max_wait: int = 3) -> tuple[pd.Timestamp, float] | None:
    s = prices[(prices.slug == slug) & (prices.timestamp >= at)
               & (prices.timestamp <= at + pd.Timedelta(hours=max_wait))]
    if s.empty:
        return None
    r = s.sort_values("timestamp").iloc[0]
    return pd.Timestamp(r.timestamp), float(r.p_yes)


def simulate_rule(tbl: pd.DataFrame, panel: pd.DataFrame, prices: pd.DataFrame,
                  *, consensus_max: float, floor_local: int | None = None,
                  stable_hours: int = 1) -> pd.DataFrame:
    """Generic rule: enter at first hour where spread ≤ consensus_max,
    local hour ≥ floor_local (if set), and has held ≤ consensus_max for
    the previous `stable_hours` hours."""
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
        p["hour_local"] = p["hour_utc"].dt.tz_convert(tz).dt.hour
        # keep only local hours on or after midnight target-date (in local)
        p["local_day"] = p["hour_utc"].dt.tz_convert(tz).dt.date
        p = p[p["local_day"] == target].reset_index(drop=True)
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

        pk = pick_price(prices, r["slug"], entry_ts)
        if pk is None:
            continue
        _, yes_p = pk
        if yes_p < YP_MIN or yes_p > YP_MAX:
            continue
        price = 1 - yes_p
        won_no = 1 - int(r["won_yes"])
        fee = FEE_RATE * price * (1 - price)
        pnl = float(won_no) - price - fee
        out.append({"city": city, "date": target, "station": station,
                    "entry_local_hour": entry_lh, "entry_utc_hour": int(entry_ts.hour),
                    "consensus_spread": spr, "yes_price": yes_p, "price_paid": price,
                    "won_no": won_no, "pnl": pnl})
    return pd.DataFrame(out)


def stats_row(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "hit": np.nan, "per": np.nan, "tot": 0.0, "t": np.nan}
    hit = df.won_no.mean()
    per = df.pnl.mean()
    tot = df.pnl.sum()
    sd = df.pnl.std(ddof=1) if n > 1 else 0.0
    t = per / (sd / n ** 0.5) if sd > 0 else 0.0
    return {"n": n, "hit": hit, "per": per, "tot": tot, "t": t}


def fmt(s: dict) -> str:
    if s["n"] == 0:
        return "n=0"
    return f"n={s['n']:>3}  hit={s['hit']*100:>5.1f}%  per=${s['per']:>+.4f}  tot=${s['tot']:>+.2f}  t={s['t']:>+.2f}"


def main() -> None:
    print("Loading inputs...")
    tbl = pd.read_parquet(REPO / "data/processed/backtest_v2/trade_table.parquet")
    tbl["market_date"] = pd.to_datetime(tbl["market_date"])
    tbl["date"] = tbl["market_date"].dt.date
    tbl = tbl[(tbl.date >= IS_START) & (tbl.date <= OOS_END)].copy()
    tbl = tbl.dropna(subset=["nbs_pred_max_f"])
    tbl["station"] = tbl["city"].map(CITY_TO_STATION)
    nbs = load_mos("NBS")
    gfs = load_mos("GFS")
    hrrr = load_hrrr()
    prices = load_prices()
    print(f"  tbl rows={len(tbl)}  prices rows={len(prices):,}")

    stations = sorted(tbl.station.unique())
    dates = sorted(tbl.date.unique())
    panel_path = REPO / "data/processed/backtest_v3/forecast_panel.parquet"
    if panel_path.exists():
        panel = pd.read_parquet(panel_path)
        panel["hour_utc"] = pd.to_datetime(panel["hour_utc"], utc=True)
        panel["target_date"] = pd.to_datetime(panel["target_date"]).dt.date
        print(f"  loaded cached panel: {len(panel):,} rows")
    else:
        print("Building forecast panel (one-time, ~60s)...")
        panel = build_forecast_panel(nbs, gfs, hrrr, stations, dates)
        panel.to_parquet(panel_path, index=False)
        print(f"  panel: {len(panel):,} rows → saved")

    # --- (1) Floor-LOCAL sweep @ consensus ≤ 3°F ---
    print("\n=== (1) Earliest entry at/after floor LOCAL hour (cs ≤ 3°F) ===")
    for floor in [None, 8, 10, 12, 13, 14, 15, 16]:
        r = simulate_rule(tbl, panel, prices, consensus_max=3.0, floor_local=floor)
        lbl = "no floor" if floor is None else f"≥ {floor}:00 local"
        is_t = r[r.date <= IS_END]; oos_t = r[r.date >= OOS_START]
        print(f"  {lbl:<14} | FULL {fmt(stats_row(r))}")
        print(f"  {'':<14} |   IS {fmt(stats_row(is_t))}")
        print(f"  {'':<14} |  OOS {fmt(stats_row(oos_t))}")

    # --- (2) Consensus threshold sweep at 13:00 local floor ---
    print("\n=== (2) Consensus threshold sweep (floor = 13:00 local) ===")
    for cs in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        r = simulate_rule(tbl, panel, prices, consensus_max=cs, floor_local=13)
        print(f"  cs ≤ {cs:>4.1f}°F  {fmt(stats_row(r))}")

    # --- (3) Stable consensus: require K consecutive hours ---
    print("\n=== (3) Stable consensus (cs ≤ 3°F, no floor) ===")
    for k in [1, 2, 3, 4, 6]:
        r = simulate_rule(tbl, panel, prices, consensus_max=3.0, stable_hours=k)
        print(f"  stable ≥ {k}h  {fmt(stats_row(r))}")

    # --- (4) Stable + local floor combined ---
    print("\n=== (4) Floor ≥ 13:00 local + stable K (cs ≤ 3°F) ===")
    for k in [1, 2, 3]:
        r = simulate_rule(tbl, panel, prices, consensus_max=3.0, floor_local=13, stable_hours=k)
        print(f"  ≥13:00 local, stable ≥ {k}h  {fmt(stats_row(r))}")

    # --- (5) Mean YES price by LOCAL hour (+1 bucket) ---
    print("\n=== (5) Mean YES price at +1 bucket by LOCAL hour ===")
    yes_rows = []
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
        slug = row.iloc[0]["slug"]
        p = panel[(panel.station == station) & (panel.target_date == target)]
        if p.empty or not (p.spread <= 3.0).any():
            continue
        px = prices[prices.slug == slug].copy()
        if px.empty:
            continue
        # convert to local hour on target_date only
        ts_local = px.timestamp.dt.tz_convert(tz)
        mask = ts_local.dt.date == target
        px = px[mask].copy()
        if px.empty:
            continue
        px["hour_local"] = ts_local[mask].dt.hour.values
        px["won_yes"] = int(row.iloc[0]["won_yes"])
        yes_rows.append(px[["hour_local", "p_yes", "won_yes"]])
    if yes_rows:
        yr = pd.concat(yes_rows, ignore_index=True)
        by_hr = yr.groupby("hour_local").agg(n=("p_yes", "count"),
                                             mean_yes=("p_yes", "mean"),
                                             median_yes=("p_yes", "median")).reset_index()
        print(by_hr.to_string(index=False))
        print("\n  ... split by outcome (winners = won_yes==0 → NO wins):")
        by_hr2 = yr.groupby(["hour_local", "won_yes"]).p_yes.mean().unstack()
        by_hr2.columns = [f"won_yes={c}" for c in by_hr2.columns]
        print(by_hr2.to_string())

    # --- (6) Losses: did consensus persist to 15:00 local (peak)? ---
    print("\n=== (6) For first-consensus LOSSES: was consensus still ≤3°F at 15:00 local? ===")
    r = simulate_rule(tbl, panel, prices, consensus_max=3.0)
    if not r.empty:
        r_losses = r[r.won_no == 0].copy()
        spreads_peak = []
        for _, row in r_losses.iterrows():
            station = row["station"]
            tz = TZ[station]
            target = row["date"]
            ts_peak_utc = (pd.Timestamp(target).tz_localize(tz) + pd.Timedelta(hours=15)).tz_convert("UTC")
            p_peak = panel[(panel.station == station) & (panel.target_date == target)
                           & (panel.hour_utc == ts_peak_utc)]
            spreads_peak.append(float(p_peak.iloc[0].spread) if not p_peak.empty else np.nan)
        r_losses = r_losses.assign(spread_at_15local=spreads_peak)
        held = r_losses.spread_at_15local <= 3.0
        print(f"  total losses: {len(r_losses)}")
        print(f"    consensus held to 15:00 local: {held.sum()}  ({held.sum()/len(r_losses)*100:.0f}%)")
        print(f"    consensus broke before 15:00 local: {(~held & r_losses.spread_at_15local.notna()).sum()}")
        print(f"    no data at 15:00 local: {r_losses.spread_at_15local.isna().sum()}")
        print(f"\n  loss details:")
        print(r_losses[["city", "date", "entry_local_hour", "consensus_spread",
                        "spread_at_15local", "yes_price", "pnl"]].to_string(index=False))


if __name__ == "__main__":
    main()

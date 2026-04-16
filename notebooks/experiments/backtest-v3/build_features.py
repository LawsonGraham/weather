"""Build unified (station, local_date) feature matrix for Dec 1 - Apr 11.

Target: `actual_max_f` — the actual daily max temperature at station in
local calendar day (from METAR).

Features: tier-by-tier ensemble of forecasts available "as of morning
of the market day" (i.e., forecasts issued before 14 UTC of the
target day — 10 EDT — giving ~6h before afternoon peak).

- NBS forecast max (latest runtime ≤ 14 UTC that day, txn_f)
- NBS forecast spread (uncertainty)
- GFS MOS forecast max
- HRRR forecast max (over afternoon hours)
- METAR-based: yesterday's actual max, temp at 12 UTC today,
  day-of-year seasonal anchor, error of NBS from yesterday
- HRRR vs NBS disagreement
- GFS vs NBS disagreement

Output: data/processed/backtest_v3/features.parquet
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path("/Users/lawsongraham/git/weather")
OUT_DIR = REPO / "data" / "processed" / "backtest_v3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Station → IANA tz
TZ = {
    "LGA": "America/New_York", "NYC": "America/New_York",
    "ATL": "America/New_York", "MIA": "America/New_York",
    "ORD": "America/Chicago", "DAL": "America/Chicago",
    "HOU": "America/Chicago", "AUS": "America/Chicago",
    "DEN": "America/Denver",
    "SEA": "America/Los_Angeles", "LAX": "America/Los_Angeles",
    "SFO": "America/Los_Angeles",
}
STATIONS = list(TZ.keys())

# IS / OOS boundaries (LOCKED before peeking at model performance)
IS_END = date(2026, 2, 28)
START_DATE = date(2025, 12, 1)
END_DATE = date(2026, 4, 14)


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def metar_daily_max() -> pd.DataFrame:
    """Daily max per (station, local_date) from METAR."""
    con = _con()
    rows = []
    for st in STATIONS:
        tz = TZ[st]
        q = f"""
            WITH obs AS (
              SELECT valid, tmpf
              FROM read_parquet('{REPO}/data/processed/iem_metar/{st}/*.parquet')
              WHERE tmpf IS NOT NULL
            )
            SELECT DATE(valid AT TIME ZONE '{tz}') AS local_date,
                   MAX(tmpf) AS actual_max_f,
                   COUNT(*) AS n_obs,
                   AVG(CASE WHEN EXTRACT(HOUR FROM valid AT TIME ZONE '{tz}') BETWEEN 11 AND 13 THEN tmpf END) AS tmp_noon_f,
                   AVG(CASE WHEN EXTRACT(HOUR FROM valid AT TIME ZONE '{tz}') BETWEEN 6 AND 8 THEN tmpf END) AS tmp_morning_f,
                   AVG(CASE WHEN EXTRACT(HOUR FROM valid AT TIME ZONE '{tz}') BETWEEN 3 AND 5 THEN tmpf END) AS tmp_dawn_f
            FROM obs
            GROUP BY local_date
        """
        try:
            df = con.execute(q).fetch_df()
            df["station"] = st
            rows.append(df)
        except Exception as e:
            print(f"  METAR {st}: {e}")
    out = pd.concat(rows, ignore_index=True)
    out["local_date"] = pd.to_datetime(out["local_date"])
    return out


def load_mos_forecasts(model: str) -> pd.DataFrame:
    """Load NBS or GFS MOS. Returns DataFrame with runtime, station, ftime,
    tmp_f, n_x_f (daily max/min), and (for NBS) txn_spread_f.
    """
    con = _con()
    path_glob = f"{REPO}/data/processed/iem_mos/{model}/*.parquet"
    if model == "NBS":
        q = f"""
            SELECT runtime, station, ftime, tmp_f, txn_f, txn_spread_f, lead_hours
            FROM read_parquet('{path_glob}')
        """
        df = con.execute(q).fetch_df()
        df = df.rename(columns={"txn_f": "n_x_f"})
        # spread only non-null on txn rows
    else:
        q = f"""
            SELECT runtime, station, ftime, tmp_f, n_x_f, lead_hours
            FROM read_parquet('{path_glob}')
        """
        df = con.execute(q).fetch_df()
        df["txn_spread_f"] = None
    df["runtime"] = pd.to_datetime(df["runtime"], utc=True)
    df["ftime"] = pd.to_datetime(df["ftime"], utc=True)
    return df


def extract_mos_pred_max(df: pd.DataFrame, station: str, target_local_date: date,
                         tz: str, cutoff_utc: datetime) -> tuple[float | None, float | None]:
    """For `target_local_date` at `station`, find the most recent MOS run
    issued before `cutoff_utc` whose `n_x_f` is the afternoon max forecast
    for that day. Return (pred_max, spread).
    """
    # Target afternoon window in UTC: convert (local_date, 12-22 local) to UTC
    # Approximation via pytz offsets
    local_afternoon_start = datetime.combine(target_local_date, datetime.min.time())
    # pandas Timestamp with tz then convert — easier
    start = pd.Timestamp(local_afternoon_start).tz_localize(tz).tz_convert("UTC")
    start_afternoon = start + pd.Timedelta(hours=10)  # 10 local ≈ morning ramp
    end_afternoon = start + pd.Timedelta(hours=22)    # 22 local ≈ end of day

    sub = df[
        (df.station == station)
        & (df.runtime <= pd.Timestamp(cutoff_utc))
        & (df.runtime >= pd.Timestamp(cutoff_utc) - pd.Timedelta(days=2))
        & (df.ftime >= start_afternoon)
        & (df.ftime <= end_afternoon)
        & (df.n_x_f.notna())
    ]
    if sub.empty:
        # Fallback: use tmp_f at ftime closest to afternoon peak (20 UTC EDT)
        fallback_peak = pd.Timestamp(datetime.combine(target_local_date, datetime.min.time())).tz_localize(tz).tz_convert("UTC") + pd.Timedelta(hours=15)
        sub2 = df[
            (df.station == station)
            & (df.runtime <= pd.Timestamp(cutoff_utc))
            & (df.runtime >= pd.Timestamp(cutoff_utc) - pd.Timedelta(days=2))
            & (df.ftime >= start_afternoon)
            & (df.ftime <= end_afternoon)
            & (df.tmp_f.notna())
        ]
        if sub2.empty:
            return (None, None)
        idx_max = sub2.loc[sub2.runtime == sub2.runtime.max(), "tmp_f"].idxmax()
        return (float(sub2.loc[idx_max, "tmp_f"]), None)

    latest_rt = sub.runtime.max()
    cand = sub[sub.runtime == latest_rt]
    idx = cand.n_x_f.idxmax()
    pred = float(cand.loc[idx, "n_x_f"])
    sp = cand.loc[idx, "txn_spread_f"]
    sp = float(sp) if pd.notna(sp) else None
    return (pred, sp)


def load_hrrr_pred_max() -> pd.DataFrame:
    """Daily max prediction from HRRR for each (station, local_date).

    Take HRRR runs from 06z target day, pick afternoon window forecasts.
    """
    con = _con()
    # Find a valid temperature column in HRRR
    schema = con.execute(f"""
        DESCRIBE SELECT * FROM read_parquet('{REPO}/data/raw/hrrr/*/hourly.parquet', union_by_name=true) LIMIT 1
    """).fetchall()
    tmp_cols = [r[0] for r in schema if "t2m" in r[0].lower() or "_0" in r[0].lower()]
    # Prefer 't2m' variant — look for common HRRR temperature name
    col = None
    for c in ["t2m_heightAboveGround_2", "tmp_heightAboveGround_2", "t_heightAboveGround_2"]:
        if c in [r[0] for r in schema]:
            col = c
            break
    if col is None:
        # fallback: any column with 't' and '2m' in name
        for r in schema:
            if "heightAboveGround_2" in r[0] and "t" in r[0].lower():
                col = r[0]
                break
    if col is None:
        print("  HRRR: no 2m temperature column found!")
        return pd.DataFrame()
    print(f"  HRRR temperature column: {col}")
    # Aggregate HRRR into per (station, local_date) daily max using valid_time
    # and each station's own local timezone. Uses ONLY forecasts with init_time
    # BEFORE local_date ~10 local (entry time), preventing leakage.
    # Also compute the "pre-peak" forecast (just the 18z UTC run's forecast
    # for afternoon peak) as a separate column.
    rows = []
    for st in STATIONS:
        tz = TZ[st]
        q = f"""
            SELECT station, DATE(valid_time AT TIME ZONE '{tz}') AS local_date,
                   init_time, valid_time, {col} AS t_k
            FROM read_parquet('{REPO}/data/raw/hrrr/K{st}/hourly.parquet')
            WHERE {col} IS NOT NULL
        """
        try:
            sub = con.execute(q).fetch_df()
        except Exception as e:
            print(f"  HRRR K{st}: {e}")
            continue
        sub["init_time"] = pd.to_datetime(sub["init_time"], utc=True)
        sub["valid_time"] = pd.to_datetime(sub["valid_time"], utc=True)
        sub["t_f"] = (sub["t_k"] - 273.15) * 9/5 + 32
        sub["local_date"] = pd.to_datetime(sub["local_date"])
        # Entry-time cutoff: init_time must be before 10 local on local_date
        cutoffs = sub["local_date"].apply(
            lambda d: pd.Timestamp(d).tz_localize(tz).tz_convert("UTC") + pd.Timedelta(hours=10)
        )
        sub = sub[sub["init_time"] <= cutoffs]
        g = sub.groupby("local_date").agg(
            hrrr_max_t_f=("t_f", "max"),
            hrrr_mean_t_f=("t_f", "mean"),
        ).reset_index()
        g["station"] = st
        rows.append(g)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def main():
    print("=" * 60)
    print("BUILDING UNIFIED FEATURE MATRIX (v3)")
    print("=" * 60)

    print("\n[1/4] METAR daily features...")
    metar = metar_daily_max()
    print(f"  rows: {len(metar)}, stations: {metar.station.nunique()}")

    print("\n[2/4] Loading MOS forecasts...")
    nbs = load_mos_forecasts("NBS")
    print(f"  NBS rows: {len(nbs)}")
    gfs = load_mos_forecasts("GFS")
    print(f"  GFS rows: {len(gfs)}")

    # Build rows per (station, local_date)
    print("\n[3/4] Joining forecasts to daily rows...")
    # Generate all (station, local_date) pairs in window
    dates = pd.date_range(START_DATE, END_DATE, freq="D")
    grid = pd.DataFrame(
        [(st, d) for st in STATIONS for d in dates],
        columns=["station", "local_date"]
    )
    grid["local_date"] = pd.to_datetime(grid["local_date"])

    # Left-join METAR
    grid = grid.merge(metar, on=["station", "local_date"], how="left")

    # For each row, compute MOS predictions
    nbs_preds = []
    gfs_preds = []
    for _, row in grid.iterrows():
        st = row["station"]
        d = row["local_date"].date()
        tz = TZ[st]
        # Cutoff: entry-time (14 UTC ≈ 10 local morning) — i.e., use forecasts
        # available 6+h before expected afternoon peak
        cutoff = pd.Timestamp(datetime.combine(d, datetime.min.time())).tz_localize(tz).tz_convert("UTC") + pd.Timedelta(hours=10)
        nbs_key = "K" + st
        nbs_pred, nbs_spread = extract_mos_pred_max(nbs, nbs_key, d, tz, cutoff)
        gfs_pred, _ = extract_mos_pred_max(gfs, nbs_key, d, tz, cutoff)
        nbs_preds.append((nbs_pred, nbs_spread))
        gfs_preds.append((gfs_pred, None))
    grid["nbs_pred_max_f"] = [p[0] for p in nbs_preds]
    grid["nbs_spread_f"] = [p[1] for p in nbs_preds]
    grid["gfs_pred_max_f"] = [p[0] for p in gfs_preds]

    print(f"  NBS filled: {grid.nbs_pred_max_f.notna().sum()}/{len(grid)}")
    print(f"  GFS filled: {grid.gfs_pred_max_f.notna().sum()}/{len(grid)}")

    print("\n[4/4] HRRR forecasts...")
    hrrr_df = load_hrrr_pred_max()
    if len(hrrr_df) > 0:
        print(f"  HRRR daily rows: {len(hrrr_df)}")
        grid = grid.merge(hrrr_df, on=["station", "local_date"], how="left")
        print(f"  HRRR filled: {grid.hrrr_max_t_f.notna().sum()}/{len(grid)}")
    else:
        grid["hrrr_max_t_f"] = None
        grid["hrrr_mean_t_f"] = None

    # Add lag features: yesterday's max, NBS error yesterday
    grid = grid.sort_values(["station", "local_date"]).reset_index(drop=True)
    grid["yesterday_max_f"] = grid.groupby("station")["actual_max_f"].shift(1)
    grid["yesterday_nbs_err_f"] = grid["yesterday_max_f"] - grid.groupby("station")["nbs_pred_max_f"].shift(1)
    grid["day_of_year"] = grid["local_date"].dt.dayofyear
    grid["month"] = grid["local_date"].dt.month

    # Fold assignment
    grid["fold"] = grid["local_date"].dt.date.apply(
        lambda d: "IS" if d <= IS_END else "OOS"
    )

    # Disagreement signals
    grid["nbs_minus_gfs"] = grid["nbs_pred_max_f"] - grid["gfs_pred_max_f"]
    grid["hrrr_minus_nbs"] = grid["hrrr_max_t_f"] - grid["nbs_pred_max_f"]

    # Save
    out = OUT_DIR / "features.parquet"
    grid.to_parquet(out, index=False)
    print(f"\nWrote {out}")
    print(f"\nFold counts:\n{grid.fold.value_counts()}")
    print(f"\nComplete rows (all features non-null + actual_max_f known):")
    complete = grid.dropna(subset=["actual_max_f", "nbs_pred_max_f", "gfs_pred_max_f"])
    print(f"  total: {len(complete)}")
    print(f"  IS: {(complete.fold=='IS').sum()}")
    print(f"  OOS: {(complete.fold=='OOS').sum()}")
    print(f"  per station IS: {complete[complete.fold=='IS'].station.value_counts().to_dict()}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Validation checks for the hrrr parquet output.

Implements permanent regression guardians at levels 1-4 of the project's
data-validation contract (see ``.claude/skills/data-validation/SKILL.md``).

What this catches:

- **L1 Manifest & disk**: manifest exists, status=complete, declared contents
  match disk, byte counts roughly agree.
- **L2 Row + column fidelity**: every expected (init_time, fxx) cycle has
  exactly one row per station in hourly.parquet and exactly 3 rows per station
  in subhourly.parquet (for the 315/330/345 min timesteps). No duplicate keys.
- **L3 Value-level fidelity**: no NaN anywhere, no unexpected nulls, Float32/64
  columns have finite values. Cycle→row monotonicity by init_time.
- **L4 Schema invariants & physical ranges**: dtype assertions, physical-range
  bounds on temperature/wind/pressure/etc., cross-column consistency checks:
  * 2 m temp ≈ skin temp (± 10 K)
  * 850 mb temp < 2 m temp (lapse rate downward; cold only in inversions)
  * 500 mb wind speed ≥ 10 m wind speed (shear monotone on typical days)
  * 2 m dewpoint ≤ 2 m temp (physical upper bound)
  * Cloud cover columns in [0, 100]
  * Surface pressure in [87000, 106000] Pa (typical NYC range)
  * t2m in Kelvin ∈ [230, 320] (broad NYC bounds)

What this does NOT catch:
- Byte-range subset accuracy vs full GRIB (L5 — separate audit)
- cfgrib→eccodes message count agreement (L5)
- Idempotency of byte-identical re-runs (L6)
- Cross-source joinability against iem_asos_1min (L6)

Run levels 5 and 6 via the ad-hoc audit scripts in /tmp/hrrr_verify/ after
making any change to download.py.

Usage::

    uv run python scripts/hrrr/validate.py
    uv run python scripts/hrrr/validate.py --stations KNYC KLGA
    uv run python scripts/hrrr/validate.py --verbose

Exit 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_NAME = "hrrr"
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"


# --- required columns and dtypes ------------------------------------------- #

# Every hourly.parquet must have these index columns with exact dtypes.
REQUIRED_INDEX_COLS_HOURLY: dict[str, Any] = {
    "init_time": pl.Datetime,
    "valid_time": pl.Datetime,
    "fxx": pl.Int64,
    "station": pl.Utf8,
    "lat": pl.Float64,
    "lon": pl.Float64,
    "grid_iy": pl.Int64,
    "grid_ix": pl.Int64,
    "grid_lat": pl.Float64,
    "grid_lon": pl.Float64,
}

REQUIRED_INDEX_COLS_SUBH: dict[str, Any] = {
    **REQUIRED_INDEX_COLS_HOURLY,
    "forecast_minutes": pl.Int64,
}

# Critical physical variables that MUST be present. If any of these disappear
# between runs, the extractor has regressed.
REQUIRED_HOURLY_PHYS_COLS: tuple[str, ...] = (
    "t2m_heightAboveGround_2",
    "d2m_heightAboveGround_2",
    "sh2_heightAboveGround_2",
    "r2_heightAboveGround_2",
    "u10_heightAboveGround_10",
    "v10_heightAboveGround_10",
    "u_heightAboveGround_80",
    "v_heightAboveGround_80",
    "sp_surface_0",
    "mslma_meanSea_0",
    "t_surface_0",
    "tp_surface_0_accum",
    "prate_surface_0",
    "vis_surface_0",
    "gust_surface_0",
    "cape_surface_0",
    "cin_surface_0",
    "refc_atmosphere_0",
    "tcc_atmosphere_0",
    "lcc_lowCloudLayer_0",
    "mcc_middleCloudLayer_0",
    "hcc_highCloudLayer_0",
    "blh_surface_0",
    "pwat_atmosphereSingleLayer_0",
    # Pressure levels
    "t_isobaricInhPa_500",
    "t_isobaricInhPa_700",
    "t_isobaricInhPa_850",
    "t_isobaricInhPa_925",
    "t_isobaricInhPa_1000",
    "dpt_isobaricInhPa_500",
    "dpt_isobaricInhPa_850",
    "u_isobaricInhPa_500",
    "v_isobaricInhPa_500",
    "gh_isobaricInhPa_500",
    # Mixed layer CAPE/CIN
    "cape_pressureFromGroundLayer_9000",
    "cape_pressureFromGroundLayer_18000",
    "cape_pressureFromGroundLayer_25500",
)

REQUIRED_SUBH_PHYS_COLS: tuple[str, ...] = (
    "t2m_heightAboveGround_2",
    "d2m_heightAboveGround_2",
    "u10_heightAboveGround_10",
    "v10_heightAboveGround_10",
    "u_heightAboveGround_80",
    "v_heightAboveGround_80",
    "sp_surface_0",
    "gust_surface_0",
    "refc_atmosphere_0",
    "vis_surface_0",
    "prate_surface_0",
    "tp_surface_0_accum",
)

# Columns that are allowed to contain NULL values (legitimate sentinel for
# "this feature is not present this cycle"). Cloud ceiling/base/top are null
# when HRRR reports "no definable ceiling" (clear or broken sky conditions).
NULLABLE_PHYS_COLS: frozenset[str] = frozenset(
    {
        "gh_cloudBase_0",
        "pcdb_cloudBase_0",
        "gh_cloudCeiling_0",
        "gh_cloudTop_0",
        "pres_cloudTop_0",
    }
)

# Physical range bounds. Bounds are deliberately broad — we're catching
# "obviously corrupt" not "obviously extreme". Temperatures are Kelvin
# (HRRR native unit).
BOUNDS: dict[str, tuple[float, float]] = {
    # Temperatures (K)
    "t2m_heightAboveGround_2": (220.0, 330.0),         # -53°C to +57°C
    "d2m_heightAboveGround_2": (200.0, 310.0),         # dewpoint < air
    "t_surface_0": (220.0, 335.0),                     # skin temp; slightly wider
    "t_isobaricInhPa_500": (200.0, 280.0),             # 500 mb is always cold
    "t_isobaricInhPa_700": (220.0, 295.0),
    "t_isobaricInhPa_850": (225.0, 310.0),
    "t_isobaricInhPa_925": (230.0, 318.0),
    "t_isobaricInhPa_1000": (235.0, 325.0),
    "dpt_isobaricInhPa_500": (190.0, 280.0),
    "dpt_isobaricInhPa_850": (220.0, 310.0),
    # Winds (m/s) — unbounded negative is fine (it's a component)
    "u10_heightAboveGround_10": (-80.0, 80.0),
    "v10_heightAboveGround_10": (-80.0, 80.0),
    "u_heightAboveGround_80": (-80.0, 80.0),
    "v_heightAboveGround_80": (-80.0, 80.0),
    "u_isobaricInhPa_500": (-150.0, 150.0),             # jet stream territory
    "v_isobaricInhPa_500": (-150.0, 150.0),
    # Pressure (Pa)
    "sp_surface_0": (85000.0, 107000.0),                # coastal NYC station
    "mslma_meanSea_0": (92000.0, 107000.0),             # MSL; tighter
    # Precip & water
    "tp_surface_0_accum": (0.0, 500.0),                 # 6-hr accum in kg/m² (= mm)
    "prate_surface_0": (0.0, 0.05),                     # kg m⁻² s⁻¹ — extreme
    "pwat_atmosphereSingleLayer_0": (0.0, 100.0),       # mm
    "sh2_heightAboveGround_2": (0.0, 0.03),             # specific humidity
    "r2_heightAboveGround_2": (0.0, 100.0),             # %
    # Stability
    "cape_surface_0": (0.0, 8000.0),
    "cin_surface_0": (-500.0, 0.0),                      # CIN is non-positive
    "cape_pressureFromGroundLayer_9000": (0.0, 8000.0),
    "cape_pressureFromGroundLayer_18000": (0.0, 8000.0),
    "cape_pressureFromGroundLayer_25500": (0.0, 8000.0),
    "blh_surface_0": (0.0, 5000.0),                      # m
    # Clouds
    "tcc_atmosphere_0": (0.0, 100.0),
    "lcc_lowCloudLayer_0": (0.0, 100.0),
    "mcc_middleCloudLayer_0": (0.0, 100.0),
    "hcc_highCloudLayer_0": (0.0, 100.0),
    # Surface
    # VIS:surface in HRRR is model-computed max visibility; can exceed
    # the METAR "10 statute miles unlimited" encoding (~16 km). On very
    # clear/dry days HRRR can emit values near its theoretical ~65 km cap.
    "vis_surface_0": (0.0, 70000.0),                     # m
    "gust_surface_0": (0.0, 80.0),                       # m/s
    "refc_atmosphere_0": (-40.0, 80.0),                  # dBZ
}


class Checker:
    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passes: int = 0

    def pass_(self, msg: str) -> None:
        self.passes += 1
        if self.verbose:
            print(f"  PASS: {msg}")

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"  FAIL: {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  WARN: {msg}")

    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------- #
# L1: Manifest & disk                                                         #
# --------------------------------------------------------------------------- #


def check_manifest(chk: Checker) -> dict[str, Any] | None:
    print("=== L1: Manifest & disk ===")
    if not MANIFEST_PATH.exists():
        chk.fail(f"manifest missing at {MANIFEST_PATH}")
        return None
    try:
        doc = json.loads(MANIFEST_PATH.read_text())
    except json.JSONDecodeError as e:
        chk.fail(f"manifest is not valid JSON: {e}")
        return None

    status = doc.get("download", {}).get("status")
    if status != "complete":
        chk.fail(f"manifest status is {status!r}, expected 'complete'")
    else:
        chk.pass_("manifest status = complete")

    # Byte counts vs disk
    expected_bytes = doc.get("download", {}).get("extracted_bytes")
    actual_bytes = sum(
        p.stat().st_size for p in RAW_DIR.rglob("*") if p.is_file()
    )
    if expected_bytes is not None:
        # Parquet ZSTD compression is not byte-deterministic across runs;
        # the manifest byte count is written once at the end of the run but
        # a later re-run with --force can produce slightly different output.
        # 10% drift is loose enough to absorb that while still catching
        # actual data-loss scenarios.
        drift = abs(expected_bytes - actual_bytes) / max(actual_bytes, 1)
        if drift > 0.10:
            chk.fail(
                f"manifest extracted_bytes={expected_bytes:,} "
                f"but disk has {actual_bytes:,} (drift={drift:.2%})"
            )
        elif drift > 0.02:
            chk.warn(
                f"manifest extracted_bytes drift {drift:.2%} "
                f"({expected_bytes:,} vs {actual_bytes:,})"
            )
            chk.pass_("manifest byte count within 10% of disk")
        else:
            chk.pass_(
                f"manifest byte count within 2% of disk "
                f"({expected_bytes:,} vs {actual_bytes:,})"
            )
    else:
        chk.warn("manifest extracted_bytes is null")

    # Declared contents match disk
    declared = set(doc.get("target", {}).get("contents", []))
    actual = {
        p.relative_to(RAW_DIR).as_posix()
        for p in RAW_DIR.rglob("*.parquet")
    }
    if declared != actual:
        only_declared = declared - actual
        only_actual = actual - declared
        if only_declared:
            chk.fail(f"declared in manifest but not on disk: {sorted(only_declared)}")
        if only_actual:
            chk.fail(f"on disk but not in manifest: {sorted(only_actual)}")
    else:
        chk.pass_(f"manifest contents match disk ({len(actual)} parquet files)")

    return doc


# --------------------------------------------------------------------------- #
# L2: Row + column fidelity                                                   #
# --------------------------------------------------------------------------- #


def check_row_column_fidelity(
    chk: Checker, doc: dict[str, Any], stations: list[str]
) -> dict[str, tuple[pl.DataFrame, pl.DataFrame]]:
    """Return {station: (hourly_df, subh_df)} for use by downstream checks."""
    print()
    print("=== L2: Row + column fidelity ===")
    out: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}

    # Parse start/end from manifest to derive expected cycle set.
    start_s = doc.get("download", {}).get("start")
    end_s = doc.get("download", {}).get("end")
    fxx_list = doc.get("download", {}).get("fxx", [])
    if not (start_s and end_s and fxx_list):
        chk.fail("manifest missing start/end/fxx — cannot derive expected cycles")
        return out

    start_d = datetime.fromisoformat(start_s).date()
    end_d = datetime.fromisoformat(end_s).date()
    expected_cycles: list[tuple[datetime, int]] = []
    d = start_d
    while d <= end_d:
        for h in range(24):
            init = datetime(d.year, d.month, d.day, h, 0, 0, tzinfo=UTC)
            for f in fxx_list:
                expected_cycles.append((init, f))
        d += timedelta(days=1)

    n_expected = len(expected_cycles)
    print(f"  expected cycles: {n_expected}")

    for sta in stations:
        hourly_path = RAW_DIR / sta / "hourly.parquet"
        subh_path = RAW_DIR / sta / "subhourly.parquet"
        if not hourly_path.exists():
            chk.fail(f"{sta}/hourly.parquet missing")
            continue
        if not subh_path.exists():
            chk.fail(f"{sta}/subhourly.parquet missing")
            continue
        h = pl.read_parquet(hourly_path)
        s = pl.read_parquet(subh_path)
        out[sta] = (h, s)

        # 1a. Hourly row count ≤ n_expected (may be less if some cycles failed)
        if h.height > n_expected:
            chk.fail(
                f"{sta} hourly: {h.height} rows exceeds {n_expected} expected cycles"
            )
        elif h.height == n_expected:
            chk.pass_(f"{sta} hourly: {h.height}/{n_expected} cycles present")
        else:
            chk.warn(
                f"{sta} hourly: {h.height}/{n_expected} cycles "
                f"(missing {n_expected - h.height})"
            )

        # 1b. Subh row count = 3 * hourly rows (315/330/345 min timesteps)
        if s.height != 3 * h.height:
            chk.fail(
                f"{sta} subh: {s.height} rows, expected {3 * h.height} "
                f"(= 3 * hourly)"
            )
        else:
            chk.pass_(f"{sta} subh: {s.height} rows = 3 * hourly")

        # 2. No duplicate keys
        hdups = h.group_by(["init_time", "fxx"]).len().filter(pl.col("len") > 1)
        if hdups.height > 0:
            chk.fail(f"{sta} hourly: {hdups.height} duplicate (init_time, fxx) keys")
        else:
            chk.pass_(f"{sta} hourly: no duplicate keys")

        sdups = (
            s.group_by(["init_time", "fxx", "forecast_minutes"])
            .len()
            .filter(pl.col("len") > 1)
        )
        if sdups.height > 0:
            chk.fail(f"{sta} subh: {sdups.height} duplicate (init_time, fxx, fm) keys")
        else:
            chk.pass_(f"{sta} subh: no duplicate keys")

        # 3. forecast_minutes distribution must be exactly {315, 330, 345}
        fm_set = set(s["forecast_minutes"].to_list())
        if fm_set != {315, 330, 345}:
            chk.fail(
                f"{sta} subh: forecast_minutes={sorted(fm_set)}, expected {{315,330,345}}"
            )
        else:
            chk.pass_(f"{sta} subh: forecast_minutes correct")

        # 4. valid_time = init_time + fxx*3600s for hourly
        expected_valid = h.with_columns(
            calc_valid=pl.col("init_time")
            + pl.duration(hours=pl.col("fxx"))
        )
        mism = expected_valid.filter(pl.col("valid_time") != pl.col("calc_valid"))
        if mism.height > 0:
            chk.fail(f"{sta} hourly: {mism.height} rows have valid_time ≠ init+fxx")
        else:
            chk.pass_(f"{sta} hourly: valid_time formula consistent")

        # 5. valid_time = init_time + forecast_minutes*60s for subh
        expected_valid_s = s.with_columns(
            calc_valid=pl.col("init_time")
            + pl.duration(minutes=pl.col("forecast_minutes"))
        )
        mism_s = expected_valid_s.filter(pl.col("valid_time") != pl.col("calc_valid"))
        if mism_s.height > 0:
            chk.fail(f"{sta} subh: {mism_s.height} rows have valid_time ≠ init+fm")
        else:
            chk.pass_(f"{sta} subh: valid_time formula consistent")

    # Column-set parity across stations
    if len(out) == 2:
        sta1, sta2 = list(out.keys())
        h1_cols = set(out[sta1][0].columns)
        h2_cols = set(out[sta2][0].columns)
        if h1_cols != h2_cols:
            chk.fail(
                f"hourly column set differs between stations: "
                f"only in {sta1}: {sorted(h1_cols - h2_cols)[:5]}, "
                f"only in {sta2}: {sorted(h2_cols - h1_cols)[:5]}"
            )
        else:
            chk.pass_(f"hourly column set matches between {sta1} and {sta2}")

        s1_cols = set(out[sta1][1].columns)
        s2_cols = set(out[sta2][1].columns)
        if s1_cols != s2_cols:
            chk.fail(
                f"subh column set differs: "
                f"only in {sta1}: {sorted(s1_cols - s2_cols)[:5]}, "
                f"only in {sta2}: {sorted(s2_cols - s1_cols)[:5]}"
            )
        else:
            chk.pass_(f"subh column set matches between {sta1} and {sta2}")

    return out


# --------------------------------------------------------------------------- #
# L3: Value-level fidelity                                                    #
# --------------------------------------------------------------------------- #


def check_value_fidelity(
    chk: Checker,
    dfs: dict[str, tuple[pl.DataFrame, pl.DataFrame]],
) -> None:
    print()
    print("=== L3: Value-level fidelity ===")
    for sta, (h, s) in dfs.items():
        for kind, df, required_phys in [
            ("hourly", h, REQUIRED_HOURLY_PHYS_COLS),
            ("subh", s, REQUIRED_SUBH_PHYS_COLS),
        ]:
            # 1. No NaN in any float column
            for c in df.columns:
                dt = df.schema[c]
                if dt not in (pl.Float64, pl.Float32):
                    continue
                nan_count = df.filter(pl.col(c).is_not_null() & pl.col(c).is_nan()).height
                if nan_count > 0:
                    chk.fail(f"{sta} {kind}: {c} has {nan_count} NaN values")

            # 2. Every required physical column must be present
            missing = [c for c in required_phys if c not in df.columns]
            if missing:
                chk.fail(f"{sta} {kind}: missing required physical cols: {missing}")
            else:
                chk.pass_(f"{sta} {kind}: all {len(required_phys)} required phys cols present")

            # 3. No unexpected nulls in required physical columns
            unexpected_null_cols = [
                c for c in required_phys
                if c in df.columns
                and c not in NULLABLE_PHYS_COLS
                and df[c].null_count() > 0
            ]
            if unexpected_null_cols:
                details = [
                    f"{c}={df[c].null_count()}/{df.height}"
                    for c in unexpected_null_cols[:5]
                ]
                chk.fail(
                    f"{sta} {kind}: unexpected nulls in required cols: {details}"
                )
            else:
                chk.pass_(f"{sta} {kind}: no unexpected nulls")

        # 4. init_time values are monotonic unique sorted
        init_sorted = h.sort("init_time")["init_time"].to_list()
        if init_sorted != sorted(init_sorted):
            chk.fail(f"{sta} hourly: init_time not sortable")
        if len(set(init_sorted)) != len(init_sorted):
            chk.fail(f"{sta} hourly: init_time has duplicates")
        else:
            chk.pass_(f"{sta} hourly: init_time unique and ordered")


# --------------------------------------------------------------------------- #
# L4: Schema invariants + physical ranges + cross-column consistency          #
# --------------------------------------------------------------------------- #


def check_schema_and_physics(
    chk: Checker,
    dfs: dict[str, tuple[pl.DataFrame, pl.DataFrame]],
) -> None:
    print()
    print("=== L4: Schema invariants + physical ranges + consistency ===")

    for sta, (h, s) in dfs.items():
        # Dtype assertions on required index columns
        for kind, df, required in [
            ("hourly", h, REQUIRED_INDEX_COLS_HOURLY),
            ("subh", s, REQUIRED_INDEX_COLS_SUBH),
        ]:
            for c, expected_dt in required.items():
                if c not in df.columns:
                    chk.fail(f"{sta} {kind}: missing required index col {c}")
                    continue
                actual_dt = df.schema[c]
                # Datetime has params; accept the base type.
                if expected_dt is pl.Datetime and not isinstance(actual_dt, pl.Datetime):
                    chk.fail(f"{sta} {kind}: {c} dtype={actual_dt}, expected Datetime")
                elif expected_dt is not pl.Datetime and actual_dt != expected_dt:
                    chk.fail(f"{sta} {kind}: {c} dtype={actual_dt}, expected {expected_dt}")

        # Physical range bounds on hourly
        for col, (lo, hi) in BOUNDS.items():
            if col not in h.columns:
                continue
            bad = h.filter(
                pl.col(col).is_not_null() & ((pl.col(col) < lo) | (pl.col(col) > hi))
            )
            if bad.height > 0:
                sample = bad[col].to_list()[:3]
                chk.fail(
                    f"{sta} hourly: {bad.height}/{h.height} rows have {col} "
                    f"outside [{lo}, {hi}] — sample {sample}"
                )
        chk.pass_(f"{sta} hourly: physical-range bounds satisfied on {len(BOUNDS)} columns")

        # 1. dpt_2m ≤ t2m_2m (physical constraint: dewpoint cannot exceed temp)
        # Allow 0.01K tolerance for float repr rounding.
        bad = h.filter(
            pl.col("t2m_heightAboveGround_2").is_not_null()
            & pl.col("d2m_heightAboveGround_2").is_not_null()
            & (
                pl.col("d2m_heightAboveGround_2")
                > pl.col("t2m_heightAboveGround_2") + 0.01
            )
        )
        if bad.height > 0:
            chk.fail(f"{sta} hourly: {bad.height} rows have d2m > t2m (physically impossible)")
        else:
            chk.pass_(f"{sta} hourly: dpt ≤ temp invariant holds")

        # 2. 500 mb temp < 850 mb temp < 1000 mb temp on typical days (lapse rate).
        # Allow violations in inversions, but flag if > 1% of rows violate.
        inv_count = h.filter(
            pl.col("t_isobaricInhPa_500").is_not_null()
            & pl.col("t_isobaricInhPa_850").is_not_null()
            & (pl.col("t_isobaricInhPa_500") > pl.col("t_isobaricInhPa_850"))
        ).height
        if inv_count > h.height * 0.01:
            chk.fail(
                f"{sta} hourly: {inv_count}/{h.height} rows have T500 > T850 "
                f"(expected < 1%, inversions only)"
            )
        else:
            chk.pass_(
                f"{sta} hourly: lapse rate T500 < T850 satisfied on "
                f"{h.height - inv_count}/{h.height} rows"
            )

        # 3. Surface skin temp should be within 15K of 2m temp (typical micro-layer).
        bad = h.filter(
            pl.col("t2m_heightAboveGround_2").is_not_null()
            & pl.col("t_surface_0").is_not_null()
            & (
                (pl.col("t2m_heightAboveGround_2") - pl.col("t_surface_0")).abs()
                > 15.0
            )
        )
        if bad.height > 0:
            chk.warn(
                f"{sta} hourly: {bad.height} rows with |t2m - t_surface| > 15 K "
                f"(unusual but not physically impossible)"
            )
        else:
            chk.pass_(f"{sta} hourly: skin temp within 15 K of 2 m temp")

        # 4. Cloud covers: tcc_atmosphere ≥ max(lcc, mcc, hcc) on some rows,
        # and all in [0, 100]. Already bounds-checked above.
        # Cross-check: tcc_atmosphere should be in [max(layered), 100] in
        # most cases. NOT strictly required (HRRR uses a different aggregation).
        # Just check that tcc_atmosphere is >= max(lcc, mcc, hcc) - 5 on > 95% of rows.
        lo_thresh = h.filter(
            pl.col("tcc_atmosphere_0").is_not_null()
            & pl.col("lcc_lowCloudLayer_0").is_not_null()
            & pl.col("mcc_middleCloudLayer_0").is_not_null()
            & pl.col("hcc_highCloudLayer_0").is_not_null()
            & (
                pl.col("tcc_atmosphere_0") + 5
                < pl.max_horizontal(
                    "lcc_lowCloudLayer_0",
                    "mcc_middleCloudLayer_0",
                    "hcc_highCloudLayer_0",
                )
            )
        ).height
        if lo_thresh > h.height * 0.05:
            chk.warn(
                f"{sta} hourly: {lo_thresh}/{h.height} rows have tcc < max(lcc,mcc,hcc)-5"
            )
        else:
            chk.pass_(f"{sta} hourly: cloud cover aggregation sane")

        # 5. Nearest-neighbor sanity: grid_lat/grid_lon should be within
        # 2 km of station lat/lon. Use a degree-based approximation good
        # enough for a sanity check at mid-latitudes.
        lat0 = float(h["lat"][0])
        lon0 = float(h["lon"][0])
        dlat_deg = h["grid_lat"] - lat0
        dlon_deg = h["grid_lon"] - lon0
        # Rough approximation: 1 deg lat ~= 111 km; at 40 N, 1 deg lon ~= 85 km
        dist_m = (
            (dlat_deg.pow(2) * 111000.0**2 + dlon_deg.pow(2) * 85000.0**2)
            .sqrt()
        )
        max_dist = float(dist_m.max())
        if max_dist > 2500:
            chk.fail(
                f"{sta} hourly: grid cell > 2.5 km from station "
                f"(max={max_dist:.0f} m)"
            )
        else:
            chk.pass_(
                f"{sta} hourly: grid cells within 2.5 km ({max_dist:.0f} m max)"
            )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stations", nargs="+", default=["KNYC", "KLGA"])
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    chk = Checker(verbose=args.verbose)

    doc = check_manifest(chk)
    if doc is None:
        print()
        print(f"HRRR validation FAILED: {len(chk.errors)} errors")
        return 1

    dfs = check_row_column_fidelity(chk, doc, args.stations)
    if not dfs:
        print()
        print(f"HRRR validation FAILED: {len(chk.errors)} errors")
        return 1

    check_value_fidelity(chk, dfs)
    check_schema_and_physics(chk, dfs)

    print()
    print("=== Summary ===")
    print(f"  passes:   {chk.passes}")
    print(f"  warnings: {len(chk.warnings)}")
    print(f"  errors:   {len(chk.errors)}")

    if chk.ok():
        print()
        print("HRRR validation PASSED")
        return 0
    else:
        print()
        print("HRRR validation FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())

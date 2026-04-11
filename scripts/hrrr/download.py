#!/usr/bin/env python3
"""Download HRRR wrfsfcf + wrfsubhf byte-range subsets, extract airport points.

Upstream: NOAA HRRR on AWS (https://registry.opendata.aws/noaa-hrrr-pds/)
Bucket:   s3://noaa-hrrr-bdp-pds/ (anonymous HTTPS)

This is a **stream-and-parse** downloader. Bytes flow:
    S3 → async streaming GET (byte range) → tempfile → cfgrib → airport points → Parquet
No raw GRIB files are persisted. Only the per-station Parquet outputs are kept.

Output layout (all under data/raw/hrrr/):
    data/raw/hrrr/
    ├── MANIFEST.json
    ├── download.log
    ├── KNYC/
    │   ├── hourly.parquet      # one row per (init_time, f06), ~50 HRRR cols
    │   └── subhourly.parquet   # three rows per init_time (15/30/45 min pre-f06)
    └── KLGA/
        ├── hourly.parquet
        └── subhourly.parquet

Architecture:
    - asyncio event loop, one shared httpx.AsyncClient with 32-socket keepalive pool
    - asyncio.Semaphore(--parallel) gates concurrent cycles (default 20)
    - Each cycle: fetch sfc idx + subh idx in parallel, parse, fetch byte ranges in
      parallel, stream to tempfile, cfgrib extract in an offloaded thread, unlink
    - Idempotency: on start, read existing Parquets, skip cycles already present

Usage:
    uv run python scripts/hrrr/download.py \\
        --stations KNYC KLGA \\
        --start 2025-12-20 \\
        --end 2026-04-11 \\
        --fxx 6 \\
        --parallel 20

    # Dry run (print the plan, fetch nothing):
    uv run python scripts/hrrr/download.py --dry-run ...

    # Small smoke test (1 day):
    uv run python scripts/hrrr/download.py \\
        --stations KNYC KLGA --start 2026-01-15 --end 2026-01-15 --fxx 6

Self-contained per .claude/skills/data-script/SKILL.md — all helpers inlined,
no shared utility module. Station coords hard-coded at the top (KNYC/KLGA);
add new airports by editing STATIONS. Variable selection is by GRIB idx-line
regex.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import warnings
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr

# eccodes / cfgrib emits a lot of FutureWarning chatter; silence for clean logs.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module=r"cfgrib.*")

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "hrrr"
UPSTREAM_REPO = "https://registry.opendata.aws/noaa-hrrr-pds/"
UPSTREAM_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/"
DESCRIPTION = (
    "NOAA HRRR hourly (wrfsfcf) and sub-hourly (wrfsubhf) byte-range subsets, "
    "extracted to airport-nearest-neighbor points and stored as per-station "
    "Parquet files. Analysis-and-forecast surface state for CONUS airports."
)
SCRIPT_VERSION = 1
REQUIRED_DISK_GIB = 5  # we only persist ~tens of MB; this is purely a sanity check

# --- station coordinates (ICAO, lat, lon) ---------------------------------- #

STATIONS: dict[str, tuple[float, float]] = {
    "KNYC": (40.7794, -73.9692),   # NYC Central Park
    "KLGA": (40.7772, -73.8726),   # LaGuardia
}

# --- variable selection ---------------------------------------------------- #
# We grab ONE contiguous byte range per file-type per cycle. For wrfsfcf we take
# records 1-164 (skipping satellite brightness temps 167-170 + LAND/ICEC 165-166).
# For wrfsubhf we take each timestep's records up to but not including the 4
# satellite records at the end.
#
# For the "include" rule we use a regex that matches idx lines WE WANT — the
# script then greedily expands to the minimal contiguous byte span(s) that
# covers all hits. This keeps HTTP overhead tiny while avoiding downloading
# satellite bytes.

# wrfsfcf include rule — everything except these leaf patterns:
SFC_EXCLUDE_PATTERNS = [
    re.compile(r":LAND:surface:"),
    re.compile(r":ICEC:surface:"),
    re.compile(r":SBT\d+:"),  # satellite brightness temperatures
]

# wrfsubhf exclude rules (per timestep)
SUBH_EXCLUDE_PATTERNS = [
    re.compile(r":SBT\d+:"),
]

# We skip the 360-min timestep in wrfsubhf06 (it's redundant with wrfsfcf06).
SUBH_SKIP_MINUTES = 360

# --- HRRR URLs ------------------------------------------------------------- #

HRRR_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"


def sfc_url(init_dt: datetime, fxx: int) -> str:
    return (
        f"{HRRR_BASE}/hrrr.{init_dt:%Y%m%d}/conus/"
        f"hrrr.t{init_dt:%H}z.wrfsfcf{fxx:02d}.grib2"
    )


def subh_url(init_dt: datetime, fxx: int) -> str:
    return (
        f"{HRRR_BASE}/hrrr.{init_dt:%Y%m%d}/conus/"
        f"hrrr.t{init_dt:%H}z.wrfsubhf{fxx:02d}.grib2"
    )

# --- paths ----------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
LOG_PATH = RAW_DIR / "download.log"
SCRIPT_REL = f"scripts/{SOURCE_NAME}/download.py"
TARGET_REL = f"data/raw/{SOURCE_NAME}"

log = logging.getLogger(SOURCE_NAME)


# --------------------------------------------------------------------------- #
# Inlined helpers (logging, disk, manifest)                                   #
# --------------------------------------------------------------------------- #


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure_logging(*, verbose: bool) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    if log.handlers:
        return

    class _Fmt(logging.Formatter):
        def formatTime(self, record, datefmt=None):  # noqa: N802
            return datetime.fromtimestamp(record.created, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

    fmt = _Fmt("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def die(msg: str) -> None:
    log.error(msg)
    raise SystemExit(1)


def require_disk_gib(n: int) -> None:
    avail = shutil.disk_usage(REPO_ROOT).free / (1024**3)
    if avail < n:
        die(f"insufficient disk: need {n} GiB on {REPO_ROOT}, have {avail:.1f} GiB")
    log.info("disk ok: %.1f GiB free (need %d)", avail, n)


def read_manifest() -> dict[str, Any] | None:
    if not MANIFEST_PATH.exists():
        return None
    return json.loads(MANIFEST_PATH.read_text())


def write_manifest(doc: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(doc, indent=2) + "\n")


def initial_manifest(
    *,
    stations: list[str],
    start: str,
    end: str,
    fxx_list: list[int],
    parallel: int,
) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "source_name": SOURCE_NAME,
        "description": DESCRIPTION,
        "upstream": {"repo": UPSTREAM_REPO, "url": UPSTREAM_URL},
        "script": {"path": SCRIPT_REL, "version": SCRIPT_VERSION},
        "download": {
            "started_at": utc_now(),
            "completed_at": None,
            "archive_bytes": None,
            "extracted_bytes": None,
            "status": "in_progress",
            "stations": stations,
            "start": start,
            "end": end,
            "fxx": fxx_list,
            "parallel": parallel,
        },
        "target": {"raw_dir": TARGET_REL, "contents": []},
        "notes": "",
    }


# --------------------------------------------------------------------------- #
# Idx parsing and byte-range computation                                      #
# --------------------------------------------------------------------------- #


@dataclass
class IdxRecord:
    idx: int          # record number (1-based)
    start_byte: int
    line: str         # full idx line text after the 'idx:start_byte:' prefix

    @property
    def timestep_minutes(self) -> int | None:
        """Parse timestep from 'N min fcst', 'M-N min ave fcst',
        'M-N min acc fcst', 'M-N min max fcst', etc. — returns the upper bound,
        or None if not a timestep line (e.g. analysis)."""
        # Two-bound windows (ave / acc / max / min / any lowercase word).
        m = re.search(r":(\d+)-(\d+) min [a-z]+ fcst", self.line)
        if m:
            return int(m.group(2))
        # Single-bound instantaneous forecast.
        m = re.search(r":(\d+) min fcst", self.line)
        if m:
            return int(m.group(1))
        if ":anl:" in self.line:
            return 0
        return None


def parse_idx(idx_text: str) -> list[IdxRecord]:
    records: list[IdxRecord] = []
    for line in idx_text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        try:
            rec_num = int(parts[0])
            start_byte = int(parts[1])
        except ValueError:
            continue
        records.append(IdxRecord(idx=rec_num, start_byte=start_byte, line=line))
    records.sort(key=lambda r: r.idx)
    return records


def excluded(line: str, patterns: Iterable[re.Pattern]) -> bool:
    return any(p.search(line) for p in patterns)


def select_sfc_ranges(records: list[IdxRecord]) -> list[tuple[int, int]]:
    """Return merged contiguous (start_byte, end_byte) pairs covering records
    we want from the wrfsfcf file. End byte is inclusive; for the last record
    we use end=-1 (read to EOF).
    """
    include = [r for r in records if not excluded(r.line, SFC_EXCLUDE_PATTERNS)]
    return merge_to_ranges(include, records)


def select_subh_ranges(records: list[IdxRecord]) -> list[tuple[int, int, int]]:
    """For wrfsubhf: return (start_byte, end_byte, timestep_minutes) tuples.
    One tuple per contiguous run of records that share a timestep (or are
    adjacent in idx order and the same minute label) and are not satellite.
    The timestep is the upper-bound minute label.

    Records in the SUBH_SKIP_MINUTES timestep are excluded entirely.
    """
    out: list[tuple[int, int, int]] = []
    include: list[IdxRecord] = []
    for r in records:
        mins = r.timestep_minutes
        if mins is None or mins == SUBH_SKIP_MINUTES:
            continue
        if excluded(r.line, SUBH_EXCLUDE_PATTERNS):
            continue
        include.append(r)

    # Group by timestep minute, preserving idx order. Within a timestep, records
    # are contiguous in idx; we collapse them to one merged range per timestep.
    if not include:
        return []
    by_minute: dict[int, list[IdxRecord]] = {}
    for r in include:
        by_minute.setdefault(r.timestep_minutes, []).append(r)  # type: ignore[arg-type]

    for minutes in sorted(by_minute):
        bucket = sorted(by_minute[minutes], key=lambda r: r.idx)
        ranges = merge_to_ranges(bucket, records)
        for start, end in ranges:
            out.append((start, end, minutes))
    return out


def merge_to_ranges(
    include: list[IdxRecord], all_records: list[IdxRecord]
) -> list[tuple[int, int]]:
    """Collapse a set of records-to-include into minimal contiguous byte ranges.

    End byte of a record = (start_byte of next record in the file) - 1.
    For the final record in the file, end = -1 (read-to-EOF).
    """
    if not include:
        return []

    # Build an idx→start_byte map of the full file so we can compute end bytes.
    all_sorted = sorted(all_records, key=lambda r: r.idx)
    next_start_by_idx: dict[int, int] = {}
    for i, r in enumerate(all_sorted):
        if i + 1 < len(all_sorted):
            next_start_by_idx[r.idx] = all_sorted[i + 1].start_byte
        else:
            next_start_by_idx[r.idx] = -1  # EOF sentinel

    include_idx = sorted(r.idx for r in include)

    # Merge contiguous runs of record indices (e.g. 5,6,7, then 12, then 15,16)
    merged: list[tuple[int, int]] = []
    run_start = include_idx[0]
    run_end = run_start
    for idx in include_idx[1:]:
        if idx == run_end + 1:
            run_end = idx
            continue
        merged.append((run_start, run_end))
        run_start = idx
        run_end = idx
    merged.append((run_start, run_end))

    # Convert (first_idx, last_idx) runs to (start_byte, end_byte) pairs.
    out: list[tuple[int, int]] = []
    by_idx = {r.idx: r for r in all_sorted}
    for first, last in merged:
        start_byte = by_idx[first].start_byte
        end_byte = next_start_by_idx[last] - 1 if next_start_by_idx[last] > 0 else -1
        out.append((start_byte, end_byte))
    return out


# --------------------------------------------------------------------------- #
# Cycle planning                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class Cycle:
    init_dt: datetime
    fxx: int

    @property
    def key(self) -> tuple[datetime, int]:
        return (self.init_dt, self.fxx)


def generate_cycles(start_d: date, end_d: date, fxx_list: list[int]) -> list[Cycle]:
    cycles: list[Cycle] = []
    d = start_d
    while d <= end_d:
        for h in range(24):
            init_dt = datetime(d.year, d.month, d.day, h, 0, 0, tzinfo=UTC)
            for f in fxx_list:
                cycles.append(Cycle(init_dt=init_dt, fxx=f))
        d += timedelta(days=1)
    return cycles


# --------------------------------------------------------------------------- #
# Async download + extract pipeline                                           #
# --------------------------------------------------------------------------- #


@dataclass
class CycleCounters:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_dl: int = 0
    hourly_rows: list[dict[str, Any]] = field(default_factory=list)
    subh_rows: list[dict[str, Any]] = field(default_factory=list)


MAX_RETRIES = 5
RETRY_BASE_DELAY = 1.5  # seconds; doubled each retry
RETRYABLE_EXC = (
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    httpx.WriteError,
)


async def fetch_idx(client: httpx.AsyncClient, url: str) -> str:
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(url + ".idx")
            resp.raise_for_status()
            return resp.text
        except RETRYABLE_EXC as e:
            last_err = e
            await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                last_err = e
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            else:
                raise
    raise RuntimeError(f"fetch_idx failed after {MAX_RETRIES} attempts: {last_err}")


async def fetch_range_to_tempfile(
    client: httpx.AsyncClient, url: str, start: int, end: int
) -> Path:
    """Stream a byte range to a tempfile. end=-1 means "to EOF" (no upper bound).
    Retries transient network errors with exponential backoff.
    """
    rng = f"bytes={start}-" if end < 0 else f"bytes={start}-{end}"
    headers = {"Range": rng}
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        fd, tmp_name = tempfile.mkstemp(suffix=".grib2")
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code not in (200, 206):
                    Path(tmp_name).unlink(missing_ok=True)
                    raise httpx.HTTPStatusError(
                        f"status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                with open(fd, "wb", closefd=True) as tmp:
                    fd = -1
                    async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                        tmp.write(chunk)
            return Path(tmp_name)
        except RETRYABLE_EXC as e:
            last_err = e
            Path(tmp_name).unlink(missing_ok=True)
            if fd >= 0:
                import os as _os
                _os.close(fd)
            await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        except httpx.HTTPStatusError as e:
            Path(tmp_name).unlink(missing_ok=True)
            if fd >= 0:
                import os as _os
                _os.close(fd)
            if e.response.status_code >= 500:
                last_err = e
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            else:
                raise
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            if fd >= 0:
                import os as _os
                _os.close(fd)
            raise
    raise RuntimeError(
        f"fetch_range_to_tempfile failed after {MAX_RETRIES} attempts: {last_err}"
    )


# --------------------------------------------------------------------------- #
# cfgrib extraction (runs in a thread via asyncio.to_thread)                  #
# --------------------------------------------------------------------------- #


# Per-station nearest-neighbor cache. HRRR's grid is fixed, so the nearest
# (iy, ix) for each station only needs to be computed once. We memoize on a
# per-process basis. The cache key is the grid shape — if HRRR ever reprojects
# we'll see a different shape and recompute.
_NN_CACHE: dict[tuple[int, int], dict[str, tuple[int, int, float, float]]] = {}


def _compute_nn(lat_grid: np.ndarray, lon_grid: np.ndarray) -> dict[
    str, tuple[int, int, float, float]
]:
    """Compute nearest (iy, ix, grid_lat, grid_lon) for each STATION."""
    # HRRR longitudes are in 0..360; convert to -180..180 if needed.
    if lon_grid.max() > 180:
        lon_adj = np.where(lon_grid > 180, lon_grid - 360, lon_grid)
    else:
        lon_adj = lon_grid
    out: dict[str, tuple[int, int, float, float]] = {}
    for name, (sta_lat, sta_lon) in STATIONS.items():
        dist = (lat_grid - sta_lat) ** 2 + (lon_adj - sta_lon) ** 2
        iy, ix = np.unravel_index(dist.argmin(), dist.shape)
        out[name] = (
            int(iy),
            int(ix),
            float(lat_grid[iy, ix]),
            float(lon_adj[iy, ix]),
        )
    return out


def _nn_for(dataset: xr.Dataset) -> dict[str, tuple[int, int, float, float]]:
    lat = dataset["latitude"].values
    lon = dataset["longitude"].values
    key = tuple(lat.shape)
    if key not in _NN_CACHE:
        _NN_CACHE[key] = _compute_nn(lat, lon)
    return _NN_CACHE[key]


def _open_grib_multi(path: Path) -> list[xr.Dataset]:
    """Open a multi-message GRIB using cfgrib's multi-index mode.

    cfgrib's xr.open_dataset only returns ONE dataset per call (the first that
    merges cleanly). To pull every message, we iterate over the filter_by_keys
    expansion: use cfgrib.open_datasets which returns a list.
    """
    import cfgrib  # local import to keep module load fast

    return cfgrib.open_datasets(str(path), backend_kwargs={"indexpath": ""})


def _float_or_none(val: Any) -> float | None:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if np.isnan(v):
        return None
    return v


def _flatten_dataset_vars(ds: xr.Dataset) -> dict[str, np.ndarray]:
    """Return {column_name: 2D numpy array of shape (y, x)}.

    Handles:
    - 2-D variables (shape y, x) — single column keyed by (shortName, typeOfLevel, level_value, stepType)
    - 3-D variables (shape level, y, x) — one column per level, iterating the level dimension

    Column name is always of the form
        ``{var}_{typeOfLevel}_{level_value}_{stepType}``
    where every component comes from the *variable* attrs (not the dataset),
    stripped and normalized. ``level_value`` is an integer if possible. Only
    a stepType of ``instant`` is omitted for readability.

    This scheme is collision-free: two variables that share a shortName but
    differ in level type, level value, or step type all get distinct names.
    """
    out: dict[str, np.ndarray] = {}
    for var_name, da in ds.data_vars.items():
        if "y" not in da.dims or "x" not in da.dims:
            continue
        lvl_type = str(da.attrs.get("GRIB_typeOfLevel", "") or "")
        step_type = str(da.attrs.get("GRIB_stepType", "") or "")

        # Find the extra (level) dimension, if any.
        extra_dims = [d for d in da.dims if d not in ("y", "x")]

        if not extra_dims:
            # 2D. Level value may live as a scalar coord named after lvl_type.
            lvl_val = _scalar_level(da, lvl_type)
            col = _make_col_name(str(var_name), lvl_type, lvl_val, step_type)
            arr = np.asarray(da.values)
            if arr.ndim == 2:
                out[_dedup(out, col)] = arr
        else:
            # 3D (or higher). Iterate the extra dim.
            extra_dim = extra_dims[0]
            coord_vals = np.asarray(da.coords[extra_dim].values)
            if coord_vals.ndim == 0:
                coord_vals = np.array([coord_vals.item()])
            for i, lvl_val in enumerate(coord_vals):
                col = _make_col_name(
                    str(var_name), extra_dim, lvl_val, step_type
                )
                slab = np.asarray(da.isel({extra_dim: i}).values)
                if slab.ndim == 2:
                    out[_dedup(out, col)] = slab
    return out


def _scalar_level(da: xr.DataArray, lvl_type: str) -> Any:
    """Extract a scalar level value from a DataArray, or None."""
    if not lvl_type:
        return None
    if lvl_type in da.coords:
        try:
            return da.coords[lvl_type].values.item()
        except (TypeError, ValueError, AttributeError):
            try:
                return float(da.coords[lvl_type].values)
            except (TypeError, ValueError):
                return str(da.coords[lvl_type].values)
    return None


def _make_col_name(
    var_name: str, lvl_type: str, lvl_val: Any, step_type: str
) -> str:
    """Build a fully-disambiguated column name.

    Format: ``{var}_{lvl_type}_{lvl_val}_{step_type}`` with:
    - lvl_type omitted if empty
    - lvl_val: int if it's a clean integer float, otherwise compact numeric, otherwise str
    - step_type: omitted if 'instant' or empty
    """
    parts: list[str] = [var_name]
    if lvl_type:
        parts.append(lvl_type)
    if lvl_val is not None and lvl_val != "":
        try:
            v = float(lvl_val)
            if v.is_integer():
                parts.append(str(int(v)))
            else:
                parts.append(f"{v:g}".replace(".", "p"))
        except (TypeError, ValueError):
            parts.append(str(lvl_val).replace(" ", ""))
    if step_type and step_type != "instant":
        parts.append(step_type)
    return "_".join(parts).replace("-", "m")


def _dedup(existing: dict[str, Any], name: str) -> str:
    """If `name` already exists, append _2, _3, etc. to force uniqueness.
    This is a last-line-of-defense for the collision-free naming scheme.
    """
    if name not in existing:
        return name
    i = 2
    while f"{name}_{i}" in existing:
        i += 1
    return f"{name}_{i}"


def extract_sfc_points(
    path: Path, init_dt: datetime, fxx: int
) -> list[dict[str, Any]]:
    """Extract airport-point rows from a wrfsfcf GRIB subset.

    Returns one row per station — all vars merged into a single dict.
    """
    datasets = _open_grib_multi(path)
    if not datasets:
        return []

    # Use the first dataset to compute nearest-neighbor indices for each station.
    nn = _nn_for(datasets[0])
    valid_time = init_dt + timedelta(hours=fxx)

    rows: dict[str, dict[str, Any]] = {}
    for name, (iy, ix, glat, glon) in nn.items():
        rows[name] = {
            "init_time": init_dt,
            "fxx": fxx,
            "valid_time": valid_time,
            "station": name,
            "lat": STATIONS[name][0],
            "lon": STATIONS[name][1],
            "grid_iy": iy,
            "grid_ix": ix,
            "grid_lat": glat,
            "grid_lon": glon,
        }

    for ds in datasets:
        cols = _flatten_dataset_vars(ds)
        for col, arr in cols.items():
            for name, (iy, ix, _glat, _glon) in nn.items():
                # In case of cross-dataset collisions (same col produced by
                # two different datasets), the last write wins, but our
                # naming scheme prevents this when attrs are correct.
                rows[name][col] = _float_or_none(arr[iy, ix])

    return list(rows.values())


def extract_subh_points(
    path: Path, init_dt: datetime, fxx: int, timestep_minutes: int
) -> list[dict[str, Any]]:
    """Extract airport-point rows from a wrfsubhf GRIB subset, one per station.
    Each row represents the state at `init_dt + timestep_minutes`.
    """
    datasets = _open_grib_multi(path)
    if not datasets:
        return []

    nn = _nn_for(datasets[0])
    valid_time = init_dt + timedelta(minutes=timestep_minutes)

    rows: dict[str, dict[str, Any]] = {}
    for name, (iy, ix, glat, glon) in nn.items():
        rows[name] = {
            "init_time": init_dt,
            "fxx": fxx,
            "forecast_minutes": timestep_minutes,
            "valid_time": valid_time,
            "station": name,
            "lat": STATIONS[name][0],
            "lon": STATIONS[name][1],
            "grid_iy": iy,
            "grid_ix": ix,
            "grid_lat": glat,
            "grid_lon": glon,
        }

    for ds in datasets:
        cols = _flatten_dataset_vars(ds)
        for col, arr in cols.items():
            for name, (iy, ix, _glat, _glon) in nn.items():
                rows[name][col] = _float_or_none(arr[iy, ix])

    return list(rows.values())


# --------------------------------------------------------------------------- #
# Per-cycle orchestration                                                     #
# --------------------------------------------------------------------------- #


async def process_cycle(
    client: httpx.AsyncClient,
    cycle: Cycle,
    semaphore: asyncio.Semaphore,
    counters: CycleCounters,
    lock: asyncio.Lock,
) -> None:
    async with semaphore:
        sfc_u = sfc_url(cycle.init_dt, cycle.fxx)
        subh_u = subh_url(cycle.init_dt, cycle.fxx)
        try:
            # Phase 1: fetch both idx files in parallel.
            sfc_idx_text, subh_idx_text = await asyncio.gather(
                fetch_idx(client, sfc_u),
                fetch_idx(client, subh_u),
            )
            sfc_records = parse_idx(sfc_idx_text)
            subh_records = parse_idx(subh_idx_text)
            sfc_ranges = select_sfc_ranges(sfc_records)
            subh_ranges = select_subh_ranges(subh_records)

            if not sfc_ranges:
                raise RuntimeError(
                    f"no sfc byte ranges for {cycle.init_dt:%Y%m%d_%Hz}"
                )
            if not subh_ranges:
                raise RuntimeError(
                    f"no subh byte ranges for {cycle.init_dt:%Y%m%d_%Hz}"
                )

            # Phase 2: fetch all byte ranges in parallel (streaming to tempfile).
            sfc_tasks = [
                fetch_range_to_tempfile(client, sfc_u, s, e) for s, e in sfc_ranges
            ]
            subh_tasks = [
                fetch_range_to_tempfile(client, subh_u, s, e)
                for (s, e, _) in subh_ranges
            ]
            sfc_paths_list, subh_paths_list = await asyncio.gather(
                asyncio.gather(*sfc_tasks),
                asyncio.gather(*subh_tasks),
            )

            # Compute total bytes downloaded for this cycle.
            bytes_dl = sum(p.stat().st_size for p in sfc_paths_list)
            bytes_dl += sum(p.stat().st_size for p in subh_paths_list)

            # Phase 3: offload cfgrib extraction to a PROCESS pool (not a
            # thread pool) because cfgrib/eccodes holds the GIL during GRIB2
            # decompression — process-level parallelism is what lets multiple
            # cycles decode at once.
            loop = asyncio.get_running_loop()
            sfc_path_strs = [str(p) for p in sfc_paths_list]
            subh_path_pairs = [
                (str(p), ts)
                for p, (_, _, ts) in zip(
                    subh_paths_list, subh_ranges, strict=True
                )
            ]
            pool = _get_extract_pool()
            hourly_rows, subh_rows_nested = await loop.run_in_executor(
                pool,
                _extract_worker,
                cycle.init_dt.isoformat(),
                cycle.fxx,
                sfc_path_strs,
                subh_path_pairs,
            )
            subh_rows: list[dict[str, Any]] = []
            for sub in subh_rows_nested:
                subh_rows.extend(sub)

            async with lock:
                counters.hourly_rows.extend(hourly_rows)
                counters.subh_rows.extend(subh_rows)
                counters.downloaded += 1
                counters.bytes_dl += bytes_dl

            log.debug(
                "cycle %s f%02d: %d sfc rows, %d subh rows, %d bytes",
                cycle.init_dt.strftime("%Y%m%d_%Hz"),
                cycle.fxx,
                len(hourly_rows),
                len(subh_rows),
                bytes_dl,
            )

        except Exception as e:
            log.warning(
                "cycle %s f%02d failed: %s: %s",
                cycle.init_dt.strftime("%Y%m%d_%Hz"),
                cycle.fxx,
                type(e).__name__,
                e,
            )
            async with lock:
                counters.failed += 1


_EXTRACT_POOL: ProcessPoolExecutor | None = None


def _get_extract_pool() -> ProcessPoolExecutor:
    """Lazily construct a process pool sized to the available CPUs.

    Using a process pool bypasses Python's GIL, which cfgrib/eccodes holds
    during GRIB2 decompression — threads cannot run GRIB decodes in parallel
    (the fundamental reason cycles serialize on extraction).

    Pool size: min(cpu_count, 8). Beyond 8 processes, we saturate memory
    bandwidth without much wall-time gain for this workload.
    """
    global _EXTRACT_POOL
    if _EXTRACT_POOL is None:
        workers = min(os.cpu_count() or 4, 8)
        _EXTRACT_POOL = ProcessPoolExecutor(max_workers=workers)
        log.info("extraction process pool: %d workers", workers)
    return _EXTRACT_POOL


def _shutdown_extract_pool() -> None:
    global _EXTRACT_POOL
    if _EXTRACT_POOL is not None:
        _EXTRACT_POOL.shutdown(wait=True)
        _EXTRACT_POOL = None


def _extract_worker(
    init_dt_iso: str,
    fxx: int,
    sfc_path_strs: list[str],
    subh_path_pairs: list[tuple[str, int]],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """Process-pool entry point: extract all variables, delete tempfiles.
    Takes ISO string + str paths so that every argument is trivially picklable.
    """
    init_dt = datetime.fromisoformat(init_dt_iso)
    sfc_paths = [Path(p) for p in sfc_path_strs]
    subh_paths_with_ts = [(Path(p), ts) for p, ts in subh_path_pairs]

    hourly_rows: list[dict[str, Any]] = []
    subh_rows: list[list[dict[str, Any]]] = []
    try:
        if len(sfc_paths) == 1:
            hourly_rows = extract_sfc_points(sfc_paths[0], init_dt, fxx)
        else:
            merged = _concat_gribs(sfc_paths)
            try:
                hourly_rows = extract_sfc_points(merged, init_dt, fxx)
            finally:
                merged.unlink(missing_ok=True)
        for p, ts in subh_paths_with_ts:
            subh_rows.append(extract_subh_points(p, init_dt, fxx, ts))
    finally:
        for p in sfc_paths:
            p.unlink(missing_ok=True)
        for p, _ in subh_paths_with_ts:
            p.unlink(missing_ok=True)
    return hourly_rows, subh_rows


def _concat_gribs(paths: list[Path]) -> Path:
    """Concat multiple GRIB files (byte-range subsets of the same source) into
    a single GRIB file. GRIB2 is self-framing, so concatenation yields a valid
    multi-message GRIB. Returns the path to a new tempfile; caller unlinks.
    """
    fd, out_name = tempfile.mkstemp(suffix=".grib2")
    with open(fd, "wb", closefd=True) as out:
        for p in paths:
            out.write(p.read_bytes())
    return Path(out_name)


# --------------------------------------------------------------------------- #
# Parquet I/O                                                                 #
# --------------------------------------------------------------------------- #


def _rows_to_table(rows: list[dict[str, Any]]) -> pa.Table:
    # Use unified column set across rows; fill missing with None.
    all_cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                all_cols.append(k)
                seen.add(k)
    cols: dict[str, list[Any]] = {k: [] for k in all_cols}
    for r in rows:
        for k in all_cols:
            cols[k].append(r.get(k))
    return pa.Table.from_pydict(cols)


def read_existing_keys(
    station_dir: Path, schema_name: str, key_cols: list[str]
) -> set[tuple]:
    """Return the set of already-written row keys for a Parquet table.
    `key_cols` is e.g. ["init_time", "fxx"] for hourly or
    ["init_time", "fxx", "forecast_minutes"] for subhourly.
    """
    path = station_dir / f"{schema_name}.parquet"
    if not path.exists():
        return set()
    try:
        tbl = pq.read_table(path, columns=key_cols)
    except Exception as e:
        log.warning("could not read existing %s: %s — will rewrite", path, e)
        return set()
    cols = [tbl.column(c).to_pylist() for c in key_cols]
    return set(zip(*cols, strict=True))


def write_station_parquets(counters: CycleCounters) -> None:
    """Write hourly.parquet and subhourly.parquet under data/raw/hrrr/<STATION>/.
    Merges with any existing Parquet data at those paths (so re-runs accumulate),
    deduping by primary key on --force re-runs (last-write-wins).
    """
    # Split accumulated rows by station.
    for station in STATIONS:
        station_dir = RAW_DIR / station
        station_dir.mkdir(parents=True, exist_ok=True)

        h_rows = [r for r in counters.hourly_rows if r["station"] == station]
        s_rows = [r for r in counters.subh_rows if r["station"] == station]

        _write_or_append(
            station_dir / "hourly.parquet", h_rows, key_cols=["init_time", "fxx"]
        )
        _write_or_append(
            station_dir / "subhourly.parquet",
            s_rows,
            key_cols=["init_time", "fxx", "forecast_minutes"],
        )

        if h_rows or s_rows:
            log.info(
                "station %s: wrote %d hourly, %d subh rows to %s",
                station,
                len(h_rows),
                len(s_rows),
                station_dir,
            )


def _write_or_append(
    path: Path, rows: list[dict[str, Any]], *, key_cols: list[str]
) -> None:
    """Append `rows` to an existing Parquet at `path`, deduping on `key_cols`.

    Last-write-wins: if a row's (key_cols) collides with an existing row, the
    NEW row replaces the old one. This makes `--force` re-runs idempotent
    with respect to the final Parquet state — running the downloader twice
    for the same date range produces the same output as running it once.
    """
    if not rows:
        return
    new_tbl = _rows_to_table(rows)
    if path.exists():
        try:
            old_tbl = pq.read_table(path)
            # Unify columns between old and new tables before concat.
            all_cols: list[str] = []
            for c in list(old_tbl.column_names) + [
                c for c in new_tbl.column_names if c not in old_tbl.column_names
            ]:
                if c not in all_cols:
                    all_cols.append(c)

            def aligned(tbl: pa.Table) -> pa.Table:
                cols_out: list[pa.Array] = []
                for c in all_cols:
                    if c in tbl.column_names:
                        cols_out.append(tbl.column(c))
                    else:
                        cols_out.append(pa.nulls(tbl.num_rows, type=pa.float64()))
                return pa.Table.from_arrays(cols_out, names=all_cols)

            combined = pa.concat_tables([aligned(old_tbl), aligned(new_tbl)])
        except Exception as e:
            log.warning("could not read/combine existing %s: %s — overwriting", path, e)
            combined = new_tbl
    else:
        combined = new_tbl

    # Dedup by primary key, keeping the LAST occurrence (new rows win).
    try:
        import polars as pl

        df = pl.from_arrow(combined)
        before = df.height
        df = df.unique(subset=key_cols, keep="last", maintain_order=True)
        after = df.height
        if before != after:
            log.info(
                "deduped %s: %d rows → %d rows (removed %d duplicates)",
                path.name,
                before,
                after,
                before - after,
            )
        combined = df.to_arrow()
    except Exception as e:
        log.warning("dedup failed for %s: %s — writing without dedup", path, e)

    pq.write_table(
        combined,
        path,
        compression="zstd",
        compression_level=3,
        use_dictionary=True,
    )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    today = datetime.now(UTC).date().isoformat()
    p = argparse.ArgumentParser(
        description=(
            f"Download HRRR wrfsfcf + wrfsubhf byte-range subsets for one or "
            f"more CONUS airports, extract airport-nearest-neighbor points, "
            f"and write per-station Parquet files under {TARGET_REL}/."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--stations",
        nargs="+",
        default=list(STATIONS.keys()),
        metavar="ICAO",
        help=(
            "ICAO station IDs to extract. Must be in the hard-coded STATIONS "
            "table at the top of this script; add new airports by editing that."
        ),
    )
    p.add_argument("--start", required=True, metavar="YYYY-MM-DD",
                   help="Start date (UTC, inclusive).")
    p.add_argument("--end", default=today, metavar="YYYY-MM-DD",
                   help=f"End date (UTC, inclusive). Defaults to today ({today}).")
    p.add_argument("--fxx", nargs="+", type=int, default=[6], metavar="FXX",
                   help="Forecast hour(s) to download. Default is f06.")
    p.add_argument("--parallel", type=int, default=20, metavar="N",
                   help="Concurrent cycles in flight. Range 1-64.")
    p.add_argument("--force", action="store_true",
                   help="Bypass idempotency; re-download and rewrite Parquets.")
    p.add_argument("--fresh", action="store_true",
                   help=f"Delete {TARGET_REL}/ before running (implies --force).")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the plan and exit without touching the network.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="More log output (DEBUG level).")
    return p.parse_args()


def parse_ymd(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise SystemExit(f"bad date: {s!r} (expected YYYY-MM-DD)") from e


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


async def run_async(
    cycles: list[Cycle], parallel: int, counters: CycleCounters
) -> None:
    limits = httpx.Limits(
        max_connections=max(32, parallel * 2),
        max_keepalive_connections=max(32, parallel * 2),
        keepalive_expiry=60.0,
    )
    timeout = httpx.Timeout(120.0, connect=10.0)
    headers = {"User-Agent": "weather-repo/hrrr-downloader (solo research)"}
    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        http2=False,
        follow_redirects=True,
        headers=headers,
    ) as client:
        semaphore = asyncio.Semaphore(parallel)
        lock = asyncio.Lock()
        tasks = [
            process_cycle(client, c, semaphore, counters, lock) for c in cycles
        ]
        # Progress reporting every N cycles.
        total = len(tasks)
        for done, fut in enumerate(asyncio.as_completed(tasks), start=1):
            await fut
            if done % 24 == 0 or done == total:
                log.info(
                    "progress: %d/%d cycles (%d ok, %d failed, %.1f GB dl)",
                    done,
                    total,
                    counters.downloaded,
                    counters.failed,
                    counters.bytes_dl / 1e9,
                )


def main() -> int:
    args = parse_args()

    stations = [s.upper() for s in args.stations]
    for s in stations:
        if s not in STATIONS:
            raise SystemExit(
                f"station {s!r} not in STATIONS table; edit the script "
                f"to add its coordinates"
            )

    start_d = parse_ymd(args.start)
    end_d = parse_ymd(args.end)
    if end_d < start_d:
        raise SystemExit(f"--end ({end_d}) before --start ({start_d})")

    if not 1 <= args.parallel <= 64:
        raise SystemExit(f"--parallel {args.parallel} out of range [1, 64]")

    fxx_list = sorted(set(args.fxx))
    force = args.force or args.fresh

    if args.fresh and RAW_DIR.exists():
        print(f"--fresh: removing {RAW_DIR}")
        shutil.rmtree(RAW_DIR)

    # Manifest gate: refuse if previous run is in_progress or failed without --force.
    existing = read_manifest()
    if existing and not force:
        status = existing.get("download", {}).get("status")
        if status in ("in_progress", "failed"):
            die(
                f"manifest status is {status!r}; investigate {MANIFEST_PATH} "
                f"then re-run with --force (or --fresh to wipe)."
            )

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    configure_logging(verbose=args.verbose)

    log.info("starting download of %s", SOURCE_NAME)
    log.info("stations: %s", " ".join(stations))
    log.info("range:    %s → %s (UTC, inclusive)", start_d, end_d)
    log.info("fxx:      %s", fxx_list)
    log.info("parallel: %d", args.parallel)
    log.info("target:   %s", RAW_DIR)
    log.info("force=%s  fresh=%s  dry_run=%s", args.force, args.fresh, args.dry_run)

    require_disk_gib(REQUIRED_DISK_GIB)

    cycles = generate_cycles(start_d, end_d, fxx_list)
    log.info(
        "plan: %d cycles (%d days * 24 init hours * %d fxx)",
        len(cycles),
        (end_d - start_d).days + 1,
        len(fxx_list),
    )

    # Idempotency: drop cycles already in both Parquets.
    if not force:
        existing_hourly_keys: dict[str, set[tuple]] = {}
        existing_subh_keys: dict[str, set[tuple]] = {}
        for sta in stations:
            existing_hourly_keys[sta] = read_existing_keys(
                RAW_DIR / sta, "hourly", ["init_time", "fxx"]
            )
            existing_subh_keys[sta] = read_existing_keys(
                RAW_DIR / sta, "subhourly", ["init_time", "fxx"]
            )
        before = len(cycles)
        cycles = [
            c
            for c in cycles
            if any(
                (c.init_dt, c.fxx) not in existing_hourly_keys.get(sta, set())
                or (c.init_dt, c.fxx) not in existing_subh_keys.get(sta, set())
                for sta in stations
            )
        ]
        log.info("idempotency: %d already-complete cycles skipped", before - len(cycles))

    if args.dry_run:
        log.info("DRY RUN: would process %d cycles", len(cycles))
        if cycles:
            log.info("first cycle: %s f%02d", cycles[0].init_dt, cycles[0].fxx)
            log.info("last  cycle: %s f%02d", cycles[-1].init_dt, cycles[-1].fxx)
            log.info("first sfc url: %s", sfc_url(cycles[0].init_dt, cycles[0].fxx))
            log.info("first subh url: %s", subh_url(cycles[0].init_dt, cycles[0].fxx))
        return 0

    if not cycles:
        log.info("nothing to do — all requested cycles already complete")
        return 0

    write_manifest(
        initial_manifest(
            stations=stations,
            start=start_d.isoformat(),
            end=end_d.isoformat(),
            fxx_list=fxx_list,
            parallel=args.parallel,
        )
    )
    log.info("manifest initialized: %s (status=in_progress)", MANIFEST_PATH)

    counters = CycleCounters()
    try:
        asyncio.run(run_async(cycles, args.parallel, counters))
    except BaseException as e:
        doc = read_manifest() or {}
        if doc.get("download", {}).get("status") == "in_progress":
            doc["download"]["status"] = "failed"
            doc["download"]["completed_at"] = utc_now()
            doc["notes"] = (doc.get("notes") or "") + f"\nfailed: {type(e).__name__}: {e}"
            write_manifest(doc)
            log.error("manifest marked failed")
        raise
    finally:
        _shutdown_extract_pool()

    log.info(
        "download loop done: %d downloaded, %d failed, %d skipped, %.2f GB total",
        counters.downloaded,
        counters.failed,
        counters.skipped,
        counters.bytes_dl / 1e9,
    )

    log.info("writing Parquets for %d stations", len(stations))
    write_station_parquets(counters)

    # Populate final manifest.
    doc = read_manifest() or {}
    doc.setdefault("download", {})
    doc["download"]["completed_at"] = utc_now()
    doc["download"]["status"] = "complete" if counters.failed == 0 else "failed"
    doc["download"]["archive_bytes"] = counters.bytes_dl
    doc["download"]["extracted_bytes"] = sum(
        p.stat().st_size
        for p in RAW_DIR.rglob("*.parquet")
        if p.is_file()
    )
    doc["download"]["downloaded_cycles"] = counters.downloaded
    doc["download"]["failed_cycles"] = counters.failed
    doc["target"] = {
        "raw_dir": TARGET_REL,
        "contents": sorted(
            p.relative_to(RAW_DIR).as_posix()
            for p in RAW_DIR.rglob("*.parquet")
        ),
    }
    write_manifest(doc)
    log.info("manifest marked %s", doc["download"]["status"])

    return 0 if counters.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

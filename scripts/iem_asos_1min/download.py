#!/usr/bin/env python3
"""Download IEM ASOS 1-minute data for one or more stations over a date range.

Upstream form: https://mesonet.agron.iastate.edu/request/asos/1min.phtml
Upstream CGI:  https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py

Lands under ``data/raw/iem_asos_1min/`` with this layout::

    data/raw/iem_asos_1min/
    ├── MANIFEST.json
    ├── download.log
    ├── LGA/
    │   ├── 2025-06.csv
    │   ├── 2025-07.csv
    │   └── ...
    └── NYC/
        └── ...

One CSV per ``(station, calendar month)`` in UTC.  Idempotent at the file
level: a month already present on disk is skipped on re-runs, with two
exceptions — the month containing "today" (UTC) is always re-fetched
because it's still partial, and ``--force`` overwrites everything in the
range.

Station IDs are IEM's 3-character form (``NYC``, ``LGA``, ``JFK``, ``SFO``,
``LAX``, ``ORD``, ``DFW``, ...).  ``K``-prefixed ICAO names (``KNYC``,
``KLGA``) are accepted and auto-stripped.

Usage::

    uv run python scripts/iem_asos_1min/download.py \\
        --stations NYC LGA \\
        --start 2025-06-01 \\
        --end 2026-04-10

    # Default --end is today UTC.
    uv run python scripts/iem_asos_1min/download.py --stations LGA --start 2025-06-01

    # Rewrite every month in the requested range.
    uv run python scripts/iem_asos_1min/download.py \\
        --stations NYC LGA --start 2025-06-01 --force

    # Nuke data/raw/iem_asos_1min/ and re-pull from scratch.
    uv run python scripts/iem_asos_1min/download.py \\
        --stations NYC LGA --start 2025-06-01 --fresh

Self-contained: all helpers inlined, no shared utility module. See
``.claude/skills/data-script/`` for the canonical pattern and template.
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "iem_asos_1min"
UPSTREAM_REPO = "https://mesonet.agron.iastate.edu/request/asos/1min.phtml"
UPSTREAM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
DESCRIPTION = (
    "IEM ASOS 1-minute observations (temperature, dewpoint, wind, gust, pressure, "
    "precip, ptype) for user-specified stations and date ranges. One CSV per "
    "(station, calendar month), timestamps in UTC."
)
# Bump when the download logic changes in a way that affects on-disk output.
SCRIPT_VERSION = 1
# A few years of ~10 stations at 1-min fits in well under 2 GiB.
REQUIRED_DISK_GIB = 2

# Default variable set: numeric core for modeling. We skip the redundant
# pressure channels (pres2/pres3) and the three visibility sensors (sparsely
# populated / noisy in the 1-min archive).
DEFAULT_VARS: tuple[str, ...] = (
    "tmpf",
    "dwpf",
    "sknt",
    "drct",
    "gust_sknt",
    "gust_drct",
    "pres1",
    "precip",
    "ptype",
)

EXPECTED_HEADER_PREFIX = "station,station_name,valid(UTC)"
REQUEST_TIMEOUT_S = 180
RETRY_COUNT = 4
RETRY_BASE_DELAY_S = 5
POLITENESS_DELAY_S = 1.5
USER_AGENT = "weather-repo/iem-asos-1min-downloader (solo research)"

# --- paths ----------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
LOG_PATH = RAW_DIR / "download.log"
SCRIPT_REL = f"scripts/{SOURCE_NAME}/download.py"
TARGET_REL = f"data/raw/{SOURCE_NAME}"

log = logging.getLogger(SOURCE_NAME)


# --- inlined helpers ------------------------------------------------------- #


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    log.propagate = False

    class _Fmt(logging.Formatter):
        def formatTime(self, record, datefmt=None):  # noqa: N802
            return datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

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


def dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def read_manifest() -> dict[str, Any] | None:
    if not MANIFEST_PATH.exists():
        return None
    return json.loads(MANIFEST_PATH.read_text())


def write_manifest(doc: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(doc, indent=2) + "\n")


def initial_manifest(
    *, stations: list[str], start: str, end: str, vars_: list[str]
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
            "archive_sha256": None,
            "status": "in_progress",
            "stations": stations,
            "start": start,
            "end": end,
            "vars": vars_,
        },
        "target": {"raw_dir": TARGET_REL, "contents": []},
        "notes": "",
    }


# --- CLI ------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    today = datetime.now(UTC).date().isoformat()
    p = argparse.ArgumentParser(
        description=(
            "Download IEM ASOS 1-minute observations for one or more stations "
            "across a date range. Writes one CSV per (station, month) under "
            f"{TARGET_REL}/."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--stations",
        nargs="+",
        required=True,
        metavar="ID",
        help=(
            "IEM station IDs (3-character, no 'K' prefix). Examples: "
            "NYC LGA JFK SFO LAX ORD DFW. 'K'-prefixed IDs are accepted and "
            "auto-stripped."
        ),
    )
    p.add_argument(
        "--start",
        required=True,
        metavar="YYYY-MM-DD",
        help="Start date (UTC, inclusive).",
    )
    p.add_argument(
        "--end",
        default=today,
        metavar="YYYY-MM-DD",
        help=f"End date (UTC, inclusive). Defaults to today ({today}).",
    )
    p.add_argument(
        "--vars",
        nargs="+",
        default=list(DEFAULT_VARS),
        metavar="VAR",
        help=(
            "IEM 1-minute variable names. See the form page for the full list: "
            "https://mesonet.agron.iastate.edu/request/asos/1min.phtml"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download every month in the range, even if the CSV already exists.",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help=f"Delete {TARGET_REL}/ entirely before downloading. Implies --force.",
    )
    return p.parse_args()


def normalize_station(s: str) -> str:
    """Accept ``NYC`` / ``knyc`` / ``KNYC`` and normalise to IEM's 3-char form."""
    up = s.upper().strip()
    if len(up) == 4 and up.startswith("K"):
        return up[1:]
    return up


def parse_date_arg(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise SystemExit(f"bad date: {s!r} (expected YYYY-MM-DD)") from e


# --- month iteration ------------------------------------------------------- #


def month_starts(start: date, end: date):
    """Yield first-of-month dates from the month containing ``start`` through
    the month containing ``end``, inclusive.
    """
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield date(y, m, 1)
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def month_end(first: date) -> date:
    last_day = calendar.monthrange(first.year, first.month)[1]
    return date(first.year, first.month, last_day)


# --- fetcher --------------------------------------------------------------- #


def build_url(
    station: str,
    start_dt: datetime,
    end_dt: datetime,
    vars_: list[str],
) -> str:
    params: list[tuple[str, str]] = [
        ("station", station),
        ("tz", "UTC"),
        ("year1", str(start_dt.year)),
        ("month1", str(start_dt.month)),
        ("day1", str(start_dt.day)),
        ("hour1", str(start_dt.hour)),
        ("minute1", str(start_dt.minute)),
        ("year2", str(end_dt.year)),
        ("month2", str(end_dt.month)),
        ("day2", str(end_dt.day)),
        ("hour2", str(end_dt.hour)),
        ("minute2", str(end_dt.minute)),
        ("sample", "1min"),
        ("what", "download"),
        ("delim", "comma"),
        ("gis", "no"),
    ]
    for v in vars_:
        params.append(("vars", v))
    return UPSTREAM_URL + "?" + urllib.parse.urlencode(params)


def fetch_month(
    station: str,
    first: date,
    range_start: date,
    range_end: date,
    vars_: list[str],
) -> bytes:
    """Fetch one (station, month) window clipped to the user's requested range.

    IEM's ``asos1min.py`` CGI treats its end time as EXCLUSIVE (half-open
    interval ``[start, end)``), so a query with ``hour2=23, minute2=59``
    silently drops the final ``23:59`` minute. We query up to the midnight
    that STARTS the day after ``last_day`` — e.g. ``[2026-03-01 00:00,
    2026-04-01 00:00)`` — so every minute of ``last_day`` including
    ``23:59`` is captured.
    """
    first_day = max(first, range_start)
    last_day = min(month_end(first), range_end)
    start_dt = datetime(first_day.year, first_day.month, first_day.day, 0, 0)
    # Half-open end: midnight on the day AFTER last_day.
    end_excl = datetime(last_day.year, last_day.month, last_day.day, 0, 0) + timedelta(days=1)
    url = build_url(station, start_dt, end_excl, vars_)

    log.info("  fetching %s %s..%s", station, first_day, last_day)
    last_err: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                body = resp.read()
            _validate(body, station, first)
            return body
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_err = e
            delay = RETRY_BASE_DELAY_S * attempt
            log.warning(
                "    attempt %d/%d failed: %s (sleeping %ds)",
                attempt,
                RETRY_COUNT,
                e,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"failed to fetch {station} {first:%Y-%m} after {RETRY_COUNT} attempts: {last_err}"
    )


def _validate(body: bytes, station: str, first: date) -> None:
    if not body:
        raise ValueError(f"empty response for {station} {first:%Y-%m}")
    head = body[:256].decode("utf-8", errors="replace").strip()
    if head.startswith("Unknown station provided"):
        # User error — no point retrying. Raise SystemExit so the outer
        # retry loop doesn't swallow it.
        raise SystemExit(f"IEM rejected station {station!r}: {head}")
    if not head.startswith(EXPECTED_HEADER_PREFIX):
        raise ValueError(
            f"unexpected response for {station} {first:%Y-%m}: first bytes = {head[:120]!r}"
        )
    if len(body.splitlines()) < 2:
        raise ValueError(f"no data rows for {station} {first:%Y-%m} (only header)")


def write_csv(body: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def all_csv_contents() -> list[str]:
    return sorted(p.relative_to(RAW_DIR).as_posix() for p in RAW_DIR.rglob("*.csv"))


# --- main ------------------------------------------------------------------ #


def main() -> int:
    args = parse_args()

    stations = sorted({normalize_station(s) for s in args.stations})
    start_d = parse_date_arg(args.start)
    end_d = parse_date_arg(args.end)
    if end_d < start_d:
        raise SystemExit(f"--end ({end_d}) is before --start ({start_d})")

    force = args.force or args.fresh

    # Idempotency gate — mirror the template. For this incremental source,
    # a 'complete' manifest does NOT short-circuit: re-runs are cheap thanks
    # to per-file skipping, and the current month always needs refreshing.
    # We only refuse to proceed when a previous run left the manifest in an
    # abnormal state (in_progress or failed) and the user hasn't passed
    # --force.
    existing = read_manifest()
    if existing and not force:
        status = existing.get("download", {}).get("status")
        if status in ("in_progress", "failed"):
            die(
                f"manifest status is {status!r}; investigate {MANIFEST_PATH} "
                f"then re-run with --force (or --fresh to wipe)."
            )

    if args.fresh and RAW_DIR.exists():
        print(f"--fresh: removing {RAW_DIR}")
        shutil.rmtree(RAW_DIR)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    configure_logging()

    log.info("starting download of %s", SOURCE_NAME)
    log.info("stations: %s", " ".join(stations))
    log.info("range:    %s → %s (UTC, inclusive)", start_d, end_d)
    log.info("vars:     %s", " ".join(args.vars))
    log.info("target:   %s", RAW_DIR)
    log.info("force=%s  fresh=%s", args.force, args.fresh)

    require_disk_gib(REQUIRED_DISK_GIB)

    write_manifest(
        initial_manifest(
            stations=stations,
            start=start_d.isoformat(),
            end=end_d.isoformat(),
            vars_=list(args.vars),
        )
    )
    log.info("manifest initialized: %s (status=in_progress)", MANIFEST_PATH)

    # The month containing "today" (UTC) is always still in progress; always
    # refetch it even without --force so re-runs stay up to date.
    current_month_first = datetime.now(UTC).date().replace(day=1)

    try:
        fetched = 0
        skipped = 0
        bytes_fetched = 0
        for station in stations:
            log.info("station %s", station)
            for first in month_starts(start_d, end_d):
                csv_path = RAW_DIR / station / f"{first.year:04d}-{first.month:02d}.csv"
                is_current_month = first >= current_month_first
                if (
                    csv_path.exists()
                    and file_bytes(csv_path) > 0
                    and not force
                    and not is_current_month
                ):
                    log.info(
                        "  skip %s (exists, %d bytes)",
                        csv_path.relative_to(RAW_DIR),
                        file_bytes(csv_path),
                    )
                    skipped += 1
                    continue
                body = fetch_month(station, first, start_d, end_d, list(args.vars))
                write_csv(body, csv_path)
                log.info(
                    "  wrote %s (%d bytes)",
                    csv_path.relative_to(RAW_DIR),
                    len(body),
                )
                fetched += 1
                bytes_fetched += len(body)
                time.sleep(POLITENESS_DELAY_S)

        extracted_bytes = dir_bytes(RAW_DIR)
        contents = all_csv_contents()
        log.info(
            "done: fetched=%d skipped=%d bytes_fetched=%d total_tree_bytes=%d",
            fetched,
            skipped,
            bytes_fetched,
            extracted_bytes,
        )

        doc = read_manifest() or initial_manifest(
            stations=stations,
            start=start_d.isoformat(),
            end=end_d.isoformat(),
            vars_=list(args.vars),
        )
        doc["download"]["completed_at"] = utc_now()
        doc["download"]["status"] = "complete"
        doc["download"]["archive_bytes"] = bytes_fetched
        doc["download"]["extracted_bytes"] = extracted_bytes
        doc["target"]["contents"] = contents
        write_manifest(doc)
        log.info("manifest marked complete")

    except BaseException as e:
        # Flip status to failed on any error (including SystemExit from die()).
        doc = read_manifest() or {}
        if doc.get("download", {}).get("status") == "in_progress":
            doc["download"]["status"] = "failed"
            doc["download"]["completed_at"] = utc_now()
            doc["notes"] = (doc.get("notes") or "") + f"\nfailed: {type(e).__name__}: {e}"
            write_manifest(doc)
            log.error("manifest marked failed")
        raise

    log.info("done: %s", SOURCE_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Download IEM METAR observations for one or more stations over a date range.

Upstream form: https://mesonet.agron.iastate.edu/request/download.phtml
Upstream CGI:  https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py

Layer 3 of the Phase 1 data stack — richer qualitative context than the 1-min
ASOS feed: sky layers + heights, present-weather codes (RA/SN/FG/TS/...),
hourly precip, peak wind, pressure tendency via the raw ``metar`` column, and
SPECI (``report_type=4``) special observations triggered by rapid changes.

Lands under ``data/raw/iem_metar/`` with this layout::

    data/raw/iem_metar/
    ├── MANIFEST.json
    ├── download.log
    ├── LGA/
    │   ├── 2025-12.csv
    │   ├── 2026-01.csv
    │   └── ...
    └── NYC/
        └── ...

One CSV per ``(station, calendar month)`` in UTC. Idempotent at the file
level: a month already present on disk is skipped on re-runs, with two
exceptions — the month containing "today" (UTC) is always re-fetched because
it's still partial, and ``--force`` overwrites everything in the range.

Station IDs are IEM's 3-character form (``NYC``, ``LGA``, ``JFK``, ``SFO``,
``LAX``, ``ORD``, ``DFW``, ...). ``K``-prefixed ICAO names (``KNYC``, ``KLGA``)
are accepted and auto-stripped.

Usage::

    uv run python scripts/iem_metar/download.py \\
        --stations NYC LGA \\
        --start 2025-12-20 \\
        --end 2026-04-11

    # Default --end is today UTC.
    uv run python scripts/iem_metar/download.py --stations LGA --start 2025-12-20

    # Rewrite every month in the requested range.
    uv run python scripts/iem_metar/download.py \\
        --stations NYC LGA --start 2025-12-20 --force

    # Nuke data/raw/iem_metar/ and re-pull from scratch.
    uv run python scripts/iem_metar/download.py \\
        --stations NYC LGA --start 2025-12-20 --fresh

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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# --- source metadata ------------------------------------------------------- #

SOURCE_NAME = "iem_metar"
UPSTREAM_REPO = "https://mesonet.agron.iastate.edu/request/download.phtml"
UPSTREAM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DESCRIPTION = (
    "IEM METAR archive (hourly + SPECI special observations) for user-specified "
    "stations and date ranges. data=all, UTC. One CSV per (station, calendar "
    "month). Columns include sky layers/heights, present-weather codes, "
    "hourly precip, peak wind, and the raw METAR string for downstream remark "
    "decoding via the `metar` parser."
)
# Bump when the download logic changes in a way that affects on-disk output.
SCRIPT_VERSION = 1
# A decade of two stations fits in well under 1 GiB. 2 GiB is generous.
REQUIRED_DISK_GIB = 2

# IEM CGI returns a block of ``#DEBUG:`` comment lines followed by the CSV
# header. The header column names below are used as the schema assertion after
# the comment block is stripped.
EXPECTED_HEADER_PREFIX = "station,valid,"
REQUEST_TIMEOUT_S = 180
RETRY_COUNT = 4
RETRY_BASE_DELAY_S = 5
POLITENESS_DELAY_S = 1.5
USER_AGENT = "weather-repo/iem-metar-downloader (solo research)"

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


def initial_manifest(*, stations: list[str], start: str, end: str) -> dict[str, Any]:
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
            "data": "all",
            "report_types": [3, 4],
        },
        "target": {"raw_dir": TARGET_REL, "contents": []},
        "notes": "",
    }


# --- CLI ------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    today = datetime.now(UTC).date().isoformat()
    p = argparse.ArgumentParser(
        description=(
            "Download IEM METAR observations for one or more stations across a "
            f"date range. Writes one CSV per (station, month) under {TARGET_REL}/."
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
        "--force",
        action="store_true",
        help="Re-download every month in the range, even if the CSV already exists.",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help=f"Delete {TARGET_REL}/ entirely before downloading. Implies --force.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan (station x month x URL) and exit without fetching.",
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


def build_url(station: str, start_dt: datetime, end_dt: datetime) -> str:
    params: list[tuple[str, str]] = [
        ("station", station),
        ("data", "all"),
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
        ("format", "comma"),
        ("latlon", "no"),
        ("elev", "no"),
        ("missing", "M"),
        ("trace", "T"),
        ("direct", "no"),
        # Include both routine METAR (report_type=3) and SPECI (4).
        ("report_type", "3"),
        ("report_type", "4"),
    ]
    return UPSTREAM_URL + "?" + urllib.parse.urlencode(params)


def fetch_month(
    station: str,
    first: date,
    range_start: date,
    range_end: date,
) -> bytes:
    """Fetch one (station, month) window clipped to the user's requested range."""
    first_day = max(first, range_start)
    last_day = min(month_end(first), range_end)
    start_dt = datetime(first_day.year, first_day.month, first_day.day, 0, 0)
    end_dt = datetime(last_day.year, last_day.month, last_day.day, 23, 59)
    url = build_url(station, start_dt, end_dt)

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


def _strip_debug_lines(body: bytes) -> tuple[list[str], list[str]]:
    """Split IEM response into (debug_comments, csv_lines).

    The IEM CGI prepends lines like::

        #DEBUG: Format Typ    -> comma
        #DEBUG: Time Period   -> 2026-03-01 00:00:00+00:00 2026-03-02 00:00:00+00:00
        ...

    followed by a normal CSV ``station,valid,...`` header + data rows.
    """
    debug: list[str] = []
    csv_lines: list[str] = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        if line.startswith("#"):
            debug.append(line)
        else:
            csv_lines.append(line)
    return debug, csv_lines


def _validate(body: bytes, station: str, first: date) -> None:
    if not body:
        raise ValueError(f"empty response for {station} {first:%Y-%m}")
    # IEM sometimes returns an error string with no header. Detect the common
    # "Unknown station" shape up front so we SystemExit (no retry) instead of
    # burning the retry budget.
    head = body[:256].decode("utf-8", errors="replace").strip()
    if "Unknown station" in head or "ERROR" in head.upper()[:80]:
        raise SystemExit(f"IEM rejected station {station!r}: {head[:200]}")
    _, csv_lines = _strip_debug_lines(body)
    if not csv_lines or not csv_lines[0].startswith(EXPECTED_HEADER_PREFIX):
        first_csv = csv_lines[0] if csv_lines else "(no csv lines)"
        raise ValueError(
            f"unexpected response for {station} {first:%Y-%m}: header = {first_csv[:120]!r}"
        )
    # Header + at least 1 data row.
    if len(csv_lines) < 2:
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

    # Idempotency gate — mirror iem_asos_1min. For this incremental source,
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
    log.info("target:   %s", RAW_DIR)
    log.info("force=%s  fresh=%s  dry_run=%s", args.force, args.fresh, args.dry_run)

    if args.dry_run:
        log.info("dry-run plan:")
        for station in stations:
            for first in month_starts(start_d, end_d):
                url = build_url(
                    station,
                    datetime(
                        max(first, start_d).year,
                        max(first, start_d).month,
                        max(first, start_d).day,
                        0,
                        0,
                    ),
                    datetime(
                        min(month_end(first), end_d).year,
                        min(month_end(first), end_d).month,
                        min(month_end(first), end_d).day,
                        23,
                        59,
                    ),
                )
                log.info("  %s %s → %s", station, first.strftime("%Y-%m"), url)
        return 0

    require_disk_gib(REQUIRED_DISK_GIB)

    write_manifest(
        initial_manifest(
            stations=stations,
            start=start_d.isoformat(),
            end=end_d.isoformat(),
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
                body = fetch_month(station, first, start_d, end_d)
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

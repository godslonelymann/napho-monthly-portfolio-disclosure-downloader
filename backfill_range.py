"""Run every downloader in ``verified/`` across a full historical range, not
just one month, and produce a per (amc, year, month) manifest with a rich,
unambiguous status for every cell -- never just "success" or "skip".

Same invocation style as the original single-month tools: each AMC script is
run as its own subprocess with AMC_PERIOD set in its environment -- just
looped over many months/years instead of one, and with AMC_DOWNLOAD=true so
real files land under AMC_OUTPUT_DIR exactly like a normal run. This is
deliberate: a script's own __main__ block is the only place that reliably
knows whether that AMC needs a special session (JM Financial's WAF-dodging
User-Agent, Edelweiss/HDFC's curl_cffi browser impersonation, Bandhan's
Playwright session, ...). Calling discover() directly in-process bypasses
that and silently breaks the few AMCs that need it. Subprocess-per-cell
costs more wall clock, but it's the only way to be sure every AMC is being
driven exactly the way it's already been verified to work -- and per the
project's own ground rules, verified/*.py is not touched by this file.

Range defaults to START_YEAR..END_YEAR below (2017-2026 inclusive), every
month. Months after "today" are never attempted at all -- they are recorded
directly as NOT_YET_PUBLISHED without spending a subprocess on them, since
no AMC publishes a portfolio for a month that hasn't happened yet. Recent
past months (see --lag-months) get the same NOT_YET_PUBLISHED treatment when
the adapter reports nothing, since AMCs routinely publish 10-15 days after
month-end and a gap there isn't evidence of anything wrong.

Status vocabulary (one CSV row per (amc, year, month), always):
  SUCCESS             file(s) downloaded and validated this run (or a prior
                       run's files are still present and intact -- see
                       ALREADY_EXISTS below for the "we didn't even have to
                       ask the site again" case).
  ALREADY_EXISTS       a previous run of *this tool* already recorded SUCCESS
                       for this cell and the files are still on disk; skipped
                       re-invoking the AMC script entirely. Not used for the
                       first time a cell is downloaded -- that's SUCCESS.
  YEAR_NOT_AVAILABLE   every month in this year came back "not available"
                       from the AMC's own site/adapter, and it sits in the
                       unbroken run of earliest years this AMC has ever come
                       back "not available" for -- i.e. as far outside this
                       AMC's actual coverage as we can tell mechanically
                       without hand-auditing each site's year picker. One row
                       per year, month="--".
  MONTH_NOT_AVAILABLE  the adapter reported "not available" for this month,
                       but this AMC has at least one confirmed SUCCESS
                       elsewhere in the same year -- so the year itself is
                       reachable and this is a within-year gap, not a
                       structural boundary.
  NO_DATA              the adapter reported "not available" for this month
                       and it doesn't qualify for either rollup above (an
                       isolated past gap, not part of a contiguous
                       unavailable-year prefix and not backed by a same-year
                       success).
  NOT_YET_PUBLISHED    month is in the future, or within --lag-months of
                       today and the AMC hasn't published it yet.
  DOWNLOAD_FAILED      discovery found the file(s) but downloading/validating
                       them did not fully succeed (core.cli exit 5/6).
  INVALID_FILE         response was HTML/corrupt/wrong-type instead of the
                       expected portfolio file (core.cli exit 7, or a raised
                       "did not return a ZIP/XLSX payload" style error).
  DISCOVERY_FAILED     the AMC page/API could not be parsed or queried in a
                       way that isn't explained by any of the above (e.g.
                       "returned zero documents", a JSON/parsing exception).
  HTTP_ERROR            network/HTTP failure (timeout, connection error,
                       4xx/5xx) reaching the AMC's site or file host.
  SITE_CHANGED         the adapter's own error indicates the page/API
                       structure it expects is gone (core.cli exit 9, or
                       messages like "no longer exposes", "structure
                       changed").
  UNKNOWN_ERROR        anything else -- an uncaught exception that doesn't
                       match any pattern above. Always logged with its raw
                       message in the `error` column; never silently dropped.

This file never lets one AMC's failure stop the run: every exception from a
subprocess is caught, classified, and recorded, and the loop moves on.

Two phases:
  --discover-only   AMC_DOWNLOAD=false: classify availability without
                     downloading anything, so the shape of a range can be
                     seen before committing to it.
  (default)         Actually downloads.

Safe to interrupt and rerun: any (amc, year, month) already recorded with a
SUCCESS/ALREADY_EXISTS status whose files are still on disk is reported as
ALREADY_EXISTS on the next run without re-invoking the AMC script, unless
--force. Writes the manifest as it goes (not just at the end), so a killed
run still leaves a usable partial manifest.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERIFIED_DIR = ROOT / "verified"

sys.path.insert(0, str(ROOT))
from core.config import settings  # noqa: E402

START_YEAR = 2017
END_YEAR = 2026

# -- statuses -----------------------------------------------------------
SUCCESS = "SUCCESS"
ALREADY_EXISTS = "ALREADY_EXISTS"
YEAR_NOT_AVAILABLE = "YEAR_NOT_AVAILABLE"
MONTH_NOT_AVAILABLE = "MONTH_NOT_AVAILABLE"
NO_DATA = "NO_DATA"
NOT_YET_PUBLISHED = "NOT_YET_PUBLISHED"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
INVALID_FILE = "INVALID_FILE"
DISCOVERY_FAILED = "DISCOVERY_FAILED"
HTTP_ERROR = "HTTP_ERROR"
SITE_CHANGED = "SITE_CHANGED"
UNKNOWN_ERROR = "UNKNOWN_ERROR"

# Cells already in one of these statuses are considered "done" by a resumed
# run and skipped (surfaced as ALREADY_EXISTS for SUCCESS-family, or
# reproduced as-is for the not-available family) unless --force.
_TERMINAL_UNAVAILABLE = {YEAR_NOT_AVAILABLE, MONTH_NOT_AVAILABLE, NO_DATA, NOT_YET_PUBLISHED}
_TERMINAL_SUCCESS = {SUCCESS, ALREADY_EXISTS}

FIELDNAMES = [
    "amc", "year", "month", "status", "description",
    "source_page", "download_url", "file_path", "error",
]

# -- message classification ----------------------------------------------
# core.cli.run_cli's own PeriodUnavailable path is the clean, explicit
# signal (see core/discovery.py) -- but only 8 of the 53 verified adapters
# raise it. The other ~45 signal the exact same "site checked, nothing
# published this period" outcome with a plain RuntimeError whose message
# names the period, e.g. "HDFC listing has no current monthly workbook for
# 2019-04" (see verified/*.py -- every adapter follows this "no ... for
# {period}" shape). Recognizing that shape is what makes those messages
# classifiable at all without touching the adapters themselves.
_PERIOD_SHAPE_RE = re.compile(r"\bfor\s+\d{4}-\d{2}\b")
_NO_DATA_WORDS_RE = re.compile(r"\b(no|not list|not available|not published|no longer offers)\b", re.IGNORECASE)

# Structural drift: the adapter's own message says the page/API shape it
# depends on is gone, independent of which period was asked for. These are
# real signals worth distinguishing from "this month has no file" because
# they mean the same failure would happen for *every* period, not just this
# one -- e.g. "Kotak Portfolios header no longer exposes option 51".
_SITE_CHANGED_RE = re.compile(
    r"no longer (has|exposes|references|offers)|"
    r"did not expose|is missing or is not a list|not an object, not a|"
    r"page structure changed|encryption scheme has changed|"
    r"dropdown never opened|AccordionList is missing",
    re.IGNORECASE,
)

_HTTP_ERROR_RE = re.compile(
    r"\btimeout\b|\btimed out\b|connectionerror|sslerror|httperror|"
    r"read timed out|failed to establish|max retries exceeded|"
    r"connection refused|name or service not known|"
    r"\b[45]\d{2}\b.*(status|error)|status.*\b[45]\d{2}\b",
    re.IGNORECASE,
)

_INVALID_FILE_RE = re.compile(
    r"did not return a (zip/xlsx|xls/xlsx) payload|returned an empty payload|"
    r"content looks like an html page",
    re.IGNORECASE,
)

_DISCOVERY_FAILED_RE = re.compile(
    r"returned zero documents|discovery returned no documents|"
    r"filename collision|jsondecodeerror|keyerror|attributeerror|"
    r"indexerror|typeerror|valueerror",
    re.IGNORECASE,
)


def month_range(start_year: int, end_year: int) -> list[tuple[int, int]]:
    return [(year, month) for year in range(start_year, end_year + 1) for month in range(1, 13)]


def _period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def classify_unavailable_message(message: str, *, period: str, today: date, lag_months: int) -> tuple[str, str]:
    """Classify a "nothing published" message into (status, description).

    Only decides between NOT_YET_PUBLISHED and the generic "not available"
    bucket (NO_DATA) here -- the YEAR_NOT_AVAILABLE / MONTH_NOT_AVAILABLE
    refinement needs to see every month of the year together, so that's a
    second pass over the whole ledger (see `_rollup_year_status`).
    """
    if _is_recent(period, today=today, lag_months=lag_months):
        return NOT_YET_PUBLISHED, f"Recent period, not yet published by the AMC (checked {today.isoformat()}): {message}"
    return NO_DATA, f"AMC reported no portfolio disclosure for this period: {message}"


def classify_failure(returncode: int, output: str, message: str) -> tuple[str, str]:
    """Classify anything that isn't a clean success or a "not available" signal."""
    if _SITE_CHANGED_RE.search(message):
        return SITE_CHANGED, f"AMC page/API structure appears to have changed: {message}"
    if _INVALID_FILE_RE.search(message):
        return INVALID_FILE, f"Response was not a valid portfolio file: {message}"
    if _HTTP_ERROR_RE.search(message):
        return HTTP_ERROR, f"Network/HTTP failure reaching the AMC's site or file host: {message}"
    if _DISCOVERY_FAILED_RE.search(message):
        return DISCOVERY_FAILED, f"AMC page/API could not be parsed or queried: {message}"
    return UNKNOWN_ERROR, f"Unexpected failure (exit {returncode}): {message}" if message else f"Unexpected failure (exit {returncode})"


def _is_recent(period: str, *, today: date, lag_months: int) -> bool:
    year, month = int(period[:4]), int(period[5:7])
    if (year, month) > (today.year, today.month):
        return True  # strictly future
    y, m = today.year, today.month
    for _ in range(lag_months):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        if (year, month) == (y, m):
            return True
    return (year, month) == (today.year, today.month)


def _amc_display_name(script_stem: str) -> str:
    return script_stem.replace("_", " ")


_AMC_CONST_RE = re.compile(r'^AMC\s*=\s*"([^"]+)"', re.MULTILINE)


def _slug_from_script(script: Path) -> str:
    # core.cli.run_cli derives each AMC's destination folder from the exact
    # string each verified/*.py script passes as amc=... to run_cli (its own
    # module-level AMC constant, e.g. "axis"), sanitized with
    # re.sub(r"[^a-z0-9._-]+", "_", amc.lower()) -- NOT from the script's
    # filename. "Axis_Mutual_Fund.py" writes to data/raw/axis/, not
    # data/raw/axis_mutual_fund/. Reading the constant straight out of the
    # script is the only way to reuse the exact same sanitizer without
    # duplicating it out of sync, or importing 53 scripts with heavy
    # module-level side effects (Playwright/curl_cffi sessions, etc.).
    match = _AMC_CONST_RE.search(script.read_text(encoding="utf-8"))
    raw = match.group(1) if match else script.stem
    return re.sub(r"[^a-z0-9._-]+", "_", raw.lower()).strip("_")


def _find_period_dir(output_root: Path, amc_slug: str, period: str) -> Path | None:
    base = output_root / amc_slug
    if not base.is_dir():
        return None
    if (base / period).is_dir():
        return base / period
    # Bandhan-style one extra subdir level (see core/cli.py's own comment).
    for candidate in base.glob(f"*/{period}"):
        if candidate.is_dir():
            return candidate
    return None


def _read_json(path: Path) -> dict | None:
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _discovery_info(period_dir: Path | None) -> tuple[str, str]:
    """(source_page, download_url) from .expected.json, when discovery got that far."""
    if period_dir is None:
        return "", ""
    data = _read_json(period_dir / ".expected.json")
    if not data:
        return "", ""
    source_pages = data.get("source_pages") or []
    items = data.get("items") or []
    source_page = source_pages[0] if source_pages else ""
    download_url = items[0].get("url", "") if items else ""
    return source_page, download_url


def _still_has_files(period_dir: Path | None) -> bool:
    if period_dir is None or not period_dir.is_dir():
        return False
    for path in period_dir.rglob("*"):
        if path.is_file() and not path.name.startswith(".") and path.name != "manifest.json":
            return True
    return False


def run_one(
    amc_display: str,
    amc_slug: str,
    script: Path,
    period: str,
    *,
    output_root: Path,
    discover_only: bool,
    timeout: int,
    today: date,
    lag_months: int,
) -> dict:
    year, month = period[:4], period[5:7]
    env = os.environ.copy()
    env["AMC_PERIOD"] = period
    if discover_only:
        env["AMC_DOWNLOAD"] = "false"

    row = {
        "amc": amc_display, "year": year, "month": month,
        "status": "", "description": "", "source_page": "", "download_url": "",
        "file_path": "", "error": "",
    }

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        row["status"] = HTTP_ERROR
        row["description"] = f"AMC site/download timed out after {timeout}s"
        row["error"] = f"subprocess killed after {timeout}s"
        return row

    output = (proc.stdout or "") + (proc.stderr or "")
    reason = _last_line(output).removeprefix("unavailable: ")
    period_dir = _find_period_dir(output_root, amc_slug, period)
    row["source_page"], row["download_url"] = _discovery_info(period_dir)

    if proc.returncode == 0:
        row["status"] = SUCCESS
        row["description"] = "Discovered successfully (not downloaded: --discover-only)" if discover_only else "Downloaded and validated successfully"
        if period_dir is not None:
            row["file_path"] = str(period_dir.relative_to(ROOT))
        return row

    if proc.returncode == 2:
        status, description = classify_unavailable_message(reason, period=period, today=today, lag_months=lag_months)
        row["status"] = status
        row["description"] = description
        return row

    if proc.returncode in (5, 6):
        row["status"] = DOWNLOAD_FAILED
        row["description"] = f"File(s) discovered but download/validation did not complete: {reason}"
        row["error"] = reason[:500]
        if period_dir is not None:
            row["file_path"] = str(period_dir.relative_to(ROOT))
        return row

    if proc.returncode == 7:
        row["status"] = INVALID_FILE
        row["description"] = f"Downloaded file failed integrity/content checks: {reason}"
        row["error"] = reason[:500]
        if period_dir is not None:
            row["file_path"] = str(period_dir.relative_to(ROOT))
        return row

    if proc.returncode == 9:
        row["status"] = SITE_CHANGED
        row["description"] = f"Re-discovery confirmed the site no longer lists file(s) it originally listed: {reason}"
        row["error"] = reason[:500]
        return row

    if proc.returncode == 8:
        row["status"] = SUCCESS
        row["description"] = f"Downloaded successfully (truncated by AMC_MAX_FILES config): {reason}"
        if period_dir is not None:
            row["file_path"] = str(period_dir.relative_to(ROOT))
        return row

    # Anything else (exit 1: uncaught exception) needs its message classified.
    # ~45 of the 53 verified adapters signal "not available" with a plain
    # RuntimeError instead of PeriodUnavailable (see this file's module
    # docstring) -- the period-shaped message pattern is what catches those.
    if _PERIOD_SHAPE_RE.search(reason) and _NO_DATA_WORDS_RE.search(reason) and not _SITE_CHANGED_RE.search(reason):
        status, description = classify_unavailable_message(reason, period=period, today=today, lag_months=lag_months)
        row["status"] = status
        row["description"] = description
        return row

    status, description = classify_failure(proc.returncode, output, reason)
    row["status"] = status
    row["description"] = description
    row["error"] = reason[:500] or output[-500:]
    return row


def _load_existing(manifest_path: Path) -> dict[tuple[str, str, str], dict]:
    if not manifest_path.exists():
        return {}
    with manifest_path.open(newline="", encoding="utf-8") as fh:
        return {(row["amc"], row["year"], row["month"]): row for row in csv.DictReader(fh)}


def _write_manifest(manifest_path: Path, rows: dict[tuple[str, str, str], dict]) -> None:
    tmp = manifest_path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for key in sorted(rows, key=lambda k: (k[0], k[1], k[2])):
            writer.writerow(rows[key])
    tmp.replace(manifest_path)


def _rollup_year_status(rows: dict[tuple[str, str, str], dict]) -> dict[tuple[str, str, str], dict]:
    """Refine the generic NO_DATA bucket into YEAR_NOT_AVAILABLE / MONTH_NOT_AVAILABLE.

    Runs after every cell for an AMC has a result. For each AMC:
      - Find every year with at least one SUCCESS/ALREADY_EXISTS month --
        proof that year is reachable on the site at all.
      - Any NO_DATA month in a *reachable* year becomes MONTH_NOT_AVAILABLE:
        the year exists, this month within it doesn't.
      - Any year with *zero* reachable months, sitting in the unbroken run of
        earliest such years for this AMC (i.e. no reachable year is older),
        collapses to a single YEAR_NOT_AVAILABLE row (month="--") -- the
        site/AMC has no history that far back, as far as this run can tell.
      - A year with zero reachable months that ISN'T part of that earliest
        run (e.g. a lone missing year sandwiched between two that worked)
        is left as-is (NO_DATA per month): that's a real gap, not evidence
        the year itself is unselectable.
    """
    by_amc: dict[str, dict[int, dict[int, dict]]] = defaultdict(lambda: defaultdict(dict))
    for (amc, year, month), row in rows.items():
        if month == "--":
            continue
        by_amc[amc][int(year)][int(month)] = row

    out: dict[tuple[str, str, str], dict] = dict(rows)

    for amc, years in by_amc.items():
        reachable_years = {
            year for year, months in years.items()
            if any(row["status"] in _TERMINAL_SUCCESS for row in months.values())
        }
        earliest_reachable = min(reachable_years) if reachable_years else None

        unavailable_prefix_years = sorted(
            year for year, months in years.items()
            if year not in reachable_years
            and all(row["status"] in {NO_DATA, MONTH_NOT_AVAILABLE} for row in months.values())
            and (earliest_reachable is None or year < earliest_reachable)
        )

        for year, months in years.items():
            if year in reachable_years:
                for month, row in months.items():
                    if row["status"] == NO_DATA:
                        row = dict(row)
                        row["status"] = MONTH_NOT_AVAILABLE
                        row["description"] = (
                            f"Year {year} has confirmed downloads elsewhere on this AMC's site, "
                            f"but not this month. {row['description']}"
                        )
                        out[(amc, str(year), f"{int(row['month']):02d}")] = row
            elif year in unavailable_prefix_years:
                sample_reason = next(iter(months.values()))["error"] or next(iter(months.values()))["description"]
                collapsed = {
                    "amc": amc, "year": str(year), "month": "--",
                    "status": YEAR_NOT_AVAILABLE,
                    "description": f"No month in {year} produced a result on the AMC website "
                                    f"(earliest confirmed year: {earliest_reachable if earliest_reachable else 'none found in range'}): {sample_reason}",
                    "source_page": "", "download_url": "", "file_path": "", "error": "",
                }
                for month in months:
                    del out[(amc, str(year), f"{month:02d}")]
                out[(amc, str(year), "--")] = collapsed

    return out


def _apply_resume_shortcut(
    period: str, existing_row: dict | None, output_root: Path, amc_slug: str,
) -> dict | None:
    """A previous run of this tool already has a usable answer for this cell.

    Success-family rows are only reused if their files are still actually on
    disk (a deleted/moved download must be re-fetched, not silently marked
    done). Confirmed-unavailable rows (NO_DATA/MONTH_NOT_AVAILABLE/
    NOT_YET_PUBLISHED) are reused as-is without re-invoking the AMC script --
    those already represent a real "checked, nothing there" answer, not a
    failure worth retrying automatically (unlike DOWNLOAD_FAILED/HTTP_ERROR/
    SITE_CHANGED/etc., which fall through and get retried every resumed run).
    """
    if existing_row is None:
        return None
    if existing_row["status"] in _TERMINAL_SUCCESS:
        if not _still_has_files(_find_period_dir(output_root, amc_slug, period)):
            return None
        row = dict(existing_row)
        row["status"] = ALREADY_EXISTS
        row["description"] = "Valid file(s) already downloaded by a previous run"
        return row
    if existing_row["status"] in _TERMINAL_UNAVAILABLE:
        return dict(existing_row)
    return None


def main(argv: list[str] | None = None) -> int:
    """``argv`` defaults to ``sys.argv[1:]`` (argparse's own default) when
    run as a script. run_verified.py's ``--full-range`` mode passes an
    explicit list here instead, to delegate into this exact engine without
    going through a subprocess or mutating ``sys.argv``.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=START_YEAR)
    parser.add_argument("--end-year", type=int, default=END_YEAR)
    parser.add_argument("--amc", action="append", help="Limit to this AMC script stem (repeatable); default: all")
    parser.add_argument("--discover-only", action="store_true", help="AMC_DOWNLOAD=false: classify availability without downloading")
    parser.add_argument("--force", action="store_true", help="Re-run cells already recorded as SUCCESS/ALREADY_EXISTS")
    parser.add_argument("--lag-months", type=int, default=2, help="Recent months within this window are NOT_YET_PUBLISHED instead of NO_DATA when unavailable")
    parser.add_argument("--timeout", type=int, default=None, help="Per (amc, period) subprocess timeout, seconds (default: AMC_PROCESS_TIMEOUT or 600)")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent subprocesses")
    parser.add_argument("--manifest", default="outputs/portfolio_manifest.csv")
    args = parser.parse_args(argv)

    timeout = args.timeout or settings().process_timeout
    output_root = settings().output_dir
    today = date.today()

    scripts = {p.stem: p for p in VERIFIED_DIR.glob("*.py")}
    amc_stems = sorted(args.amc) if args.amc else sorted(scripts)
    periods = month_range(args.start_year, args.end_year)
    manifest_path = ROOT / args.manifest
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _load_existing(manifest_path)

    jobs = []
    shortcuts: dict[tuple[str, str, str], dict] = {}
    for stem in amc_stems:
        amc_display = _amc_display_name(stem)
        amc_slug = _slug_from_script(scripts[stem])
        for year, month in periods:
            period = _period(year, month)
            key = (amc_display, f"{year:04d}", f"{month:02d}")

            if (year, month) > (today.year, today.month):
                shortcuts[key] = {
                    "amc": amc_display, "year": key[1], "month": key[2],
                    "status": NOT_YET_PUBLISHED, "description": "Month is in the future",
                    "source_page": "", "download_url": "", "file_path": "", "error": "",
                }
                continue

            if not args.force:
                existing = rows.get(key)
                year_rollup = rows.get((amc_display, key[1], "--"))
                if existing is None and year_rollup is not None:
                    # A prior run collapsed this whole year into one
                    # YEAR_NOT_AVAILABLE row (month="--"). Re-expand it back
                    # to a per-month placeholder rather than reusing
                    # YEAR_NOT_AVAILABLE verbatim, so _rollup_year_status can
                    # re-derive the correct label (still YEAR_NOT_AVAILABLE
                    # in the common case) instead of leaving both the old
                    # collapsed row and 12 new per-month rows on disk.
                    existing = dict(year_rollup)
                    existing["status"] = NO_DATA
                shortcut = _apply_resume_shortcut(period, existing, output_root, amc_slug)
                if shortcut is not None:
                    shortcut = dict(shortcut)
                    shortcut["year"], shortcut["month"] = key[1], key[2]
                    shortcuts[key] = shortcut
                    continue

            jobs.append((amc_display, amc_slug, stem, period, key))

    total_cells = len(amc_stems) * len(periods)
    print(
        f"amcs={len(amc_stems)} periods={len(periods)} ({args.start_year}..{args.end_year}) "
        f"total_cells={total_cells} to_run={len(jobs)} already_resolved={len(shortcuts)} "
        f"discover_only={args.discover_only}"
    )

    rows.update(shortcuts)

    start = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_one, amc_display, amc_slug, scripts[stem], period,
                output_root=output_root, discover_only=args.discover_only,
                timeout=timeout, today=today, lag_months=args.lag_months,
            ): key
            for amc_display, amc_slug, stem, period, key in jobs
        }
        for fut in as_completed(futures):
            key = futures[fut]
            result = fut.result()
            rows[key] = result
            done += 1
            if done % 25 == 0 or done == len(jobs):
                _write_manifest(manifest_path, _rollup_year_status(rows))
            elapsed = time.time() - start
            print(f"[{done:5}/{len(jobs)}] [{elapsed:7.1f}s] {result['amc']:35} {key[1]}-{key[2]}  {result['status']:20} {result['description'][:60]}", flush=True)

    rows = _rollup_year_status(rows)
    _write_manifest(manifest_path, rows)
    print(f"\nDone in {time.time()-start:.1f}s. Manifest: {manifest_path}")

    counts = Counter(r["status"] for r in rows.values())
    print("status totals:", dict(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Full-history downloader for Bandhan's consolidated monthly portfolio summary.

    https://bandhanmutual.com/downloads/portfolio-summary/monthly

Standalone tool, structurally the twin of Bandhan_Monthly_Half_Yearly.py in
this directory (same site, same encrypted-API technique) but a different
page: this one publishes a handful of consolidated workbooks per month
(e.g. "Debt Fund Portfolios" and "Equity and Hybrid Fund Portfolios") rather
than one file per scheme. A prior version of the per-scheme adapter
(verified/Bandhan_Mutual_Fund.py) assumed this page only ever lists one
debt-only workbook -- live discovery for this script shows that is not
current: 2021, for example, lists 22 records across the year covering both
debt and arbitrage workbooks, and other years add an equity/hybrid one too.
This script downloads whatever the API actually returns, nothing assumed.

-- The site's API --------------------------------------------------------

Same backend endpoint as the per-scheme page, same encryption, same
technique for getting plaintext in and out of it (see the module docstring
in Bandhan_Monthly_Half_Yearly.py for the full explanation of the
Playwright init-script hook and why no key/signature is reimplemented
here). This page's own listing call differs only in its query:

  * type="PORTFOLIO_SUMMARY", subcategory="monthly-portfolio".
  * Dropping "month" returns every month of the requested year in one call.
  * Dropping "financial_year" and "month" both returns the site's current
    default period plus its own "financial_years" list, used to learn
    which years exist without hardcoding any.
  * This page's rows carry no per-scheme "funds_mapping" (there is no
    scheme dropdown on this page at all) -- each row is one consolidated
    workbook, identified by its own document_name/title.

-- Workflow ---------------------------------------------------------------

Identical shape to Bandhan_Monthly_Half_Yearly.py:

    discover (stabilized) -> download (concurrent) -> validate
        -> rediscover -> reconcile (download anything new/missing)
        -> final verification -> report

See that file's docstring for the discovery-stability and reconcile-loop
reasoning; it applies here unchanged.
"""

from __future__ import annotations

import csv
import hashlib
import html as html_module
import json
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
from requests.adapters import HTTPAdapter

from core.config import ROOT, settings
from core.http import create_session
from core.periods import MONTH_ALIASES, last_period

# -- constants --------------------------------------------------------------

AMC = "bandhan"
PAGE_URL = os.getenv(
    "BANDHAN_PSM_PAGE_URL",
    "https://bandhanmutual.com/downloads/portfolio-summary/monthly",
)
API_REQUEST_TYPE = "PORTFOLIO_SUMMARY"
API_SUBCATEGORY = "monthly-portfolio"

PER_PAGE = 300
_MAX_PAGES_GUARD = 50

_PAGE_LOAD_ATTEMPTS = 3
_RESPONSE_TIMEOUT_MS = 45_000
_RETRY_BACKOFF_SECONDS = 2.0

_DEFAULT_MAX_DISCOVERY_ATTEMPTS = 5
_DEFAULT_MAX_RECONCILE_ROUNDS = 3

_DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "bandhan_portfolio_summary_monthly"

_NON_HTML_MAGIC = {
    "xlsx": (b"PK",),
    "xlsm": (b"PK",),
    "xlsb": (b"PK",),
    "zip": (b"PK",),
    "xls": (b"PK", b"\xd0\xcf\x11\xe0"),
    "pdf": (b"%PDF",),
}
_HTML_MARKERS = (b"<!doctype html", b"<html")

STATUS_DOWNLOADED = "downloaded"
STATUS_IDENTICAL = "already_identical"
STATUS_FAILED = "failed"
STATUS_CONFLICT = "conflict"


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


MAX_WORKERS = _env_int("BANDHAN_PSM_MAX_WORKERS", 8)
MAX_DISCOVERY_ATTEMPTS = _env_int("BANDHAN_PSM_MAX_DISCOVERY_ATTEMPTS", _DEFAULT_MAX_DISCOVERY_ATTEMPTS)
MAX_RECONCILE_ROUNDS = _env_int("BANDHAN_PSM_MAX_RECONCILE_ROUNDS", _DEFAULT_MAX_RECONCILE_ROUNDS)
DISCOVERY_DELAY_SECONDS = _env_float("BANDHAN_PSM_DISCOVERY_DELAY_SECONDS", 0.4)
_YEARS_OVERRIDE = [y.strip() for y in os.getenv("BANDHAN_PSM_YEARS", "").split(",") if y.strip()]


def _output_dir() -> Path:
    raw = os.getenv("BANDHAN_PSM_OUTPUT_DIR", "")
    path = Path(raw) if raw else _DEFAULT_OUTPUT_DIR
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


# -- the page-side hook -------------------------------------------------

_INIT_SCRIPT_TEMPLATE = """
(() => {
  const QUERY = %(query)s;
  window.__psmCaptures = [];
  const stringify = JSON.stringify;
  const parse = JSON.parse;
  JSON.stringify = function (value, replacer, space) {
    try {
      if (value && value.type === QUERY.type && value.data) {
        const data = Object.assign({}, value.data, QUERY.data);
        for (const key of Object.keys(data)) {
          if (data[key] === null) delete data[key];
        }
        return stringify.call(this, Object.assign({}, value, {data: data}), replacer, space);
      }
    } catch (error) { /* fall through to the untouched call below */ }
    return stringify.apply(this, arguments);
  };
  JSON.parse = function (text) {
    const result = parse.apply(this, arguments);
    try {
      if (result && typeof result === 'object'
          && (result.financial_years !== undefined || result.status === 'no_posts_found')) {
        window.__psmCaptures.push(result);
      }
    } catch (error) { /* capturing is best-effort, never break the app */ }
    return result;
  };
})();
"""

_CAPTURE_SUMMARY_JS = """() => (window.__psmCaptures || []).map(
    capture => ({
        status: capture && capture.status,
        posts_per_page: capture && capture.posts_per_page,
        current_page: capture && capture.current_page,
    })
)"""


def _init_script(*, year: str | None, page_number: int) -> str:
    """The page-side hook, parameterised for one listing request.

    ``year=None`` asks for whatever period the page defaults to -- used
    once at startup purely to read the site's own "financial_years" list.
    ``year`` set always drops "month" too, so the response covers every
    month of that year in one call.
    """
    data: dict[str, object] = {
        "subcategory": API_SUBCATEGORY,
        "page": page_number,
        "posts_per_page": PER_PAGE,
        "financial_year": year,
        "month": None,
    }
    query = {"type": API_REQUEST_TYPE, "data": data}
    return _INIT_SCRIPT_TEMPLATE % {"query": json.dumps(query)}


def _capture_index(summaries: list, page_number: int) -> int | None:
    for index, summary in enumerate(summaries or []):
        if not isinstance(summary, dict):
            continue
        if summary.get("status") == "no_posts_found":
            return index
        if summary.get("posts_per_page") == PER_PAGE and summary.get("current_page") == page_number:
            return index
    return None


def _fetch_listing(browser, *, year: str | None, page_number: int, playwright_timeout_error) -> dict:
    config = settings()
    last_error = "no response"
    for attempt in range(1, _PAGE_LOAD_ATTEMPTS + 1):
        page = browser.new_page()
        try:
            page.add_init_script(_init_script(year=year, page_number=page_number))
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=config.read_timeout * 1000)
            deadline = time.monotonic() + _RESPONSE_TIMEOUT_MS / 1000
            while time.monotonic() < deadline:
                index = _capture_index(page.evaluate(_CAPTURE_SUMMARY_JS), page_number)
                if index is not None:
                    return page.evaluate("index => window.__psmCaptures[index]", index)
                page.wait_for_timeout(500)
            last_error = f"timed out waiting {_RESPONSE_TIMEOUT_MS // 1000}s for the site's listing API"
        except playwright_timeout_error as exc:
            last_error = f"page load timed out: {exc}"
        finally:
            page.close()
        if attempt < _PAGE_LOAD_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"Bandhan portfolio-summary page returned no listing response "
        f"(year={year} page={page_number}, attempts<={_PAGE_LOAD_ATTEMPTS}): {last_error}"
    )


# -- payload -> records (pure; no browser involved) ----------------------


def direct_url(raw_url: str) -> str:
    raw_url = (raw_url or "").strip()
    if "/investor/v1/dashboard/download-doc" not in raw_url:
        return raw_url
    filepath = (parse_qs(urlsplit(raw_url).query).get("filepath") or [""])[0]
    filepath = unquote(filepath).strip()
    if filepath.startswith(("http://", "https://")):
        return filepath
    return raw_url


def _clean_text(value: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).replace("–", "-").strip()


def _month_number(name: str) -> int | None:
    return MONTH_ALIASES.get((name or "").strip().lower())


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


# A workbook title always ends with its own as-of date ("... 31 July
# 2022") -- stripped so the same recurring workbook (e.g. "Debt Fund
# Portfolios") groups together across months instead of each month's date
# becoming part of its identity.
_TRAILING_DATE_RE = re.compile(
    r"\s*[-,]?\s*(?:as\s+on\s+)?(?:\d{1,2}(?:st|nd|rd|th)?\s+)?"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+\d{4}\s*$",
    re.I,
)


def _workbook_title(document_name: str) -> str:
    return _TRAILING_DATE_RE.sub("", document_name or "").strip() or (document_name or "").strip() or "portfolio summary"


@dataclass(frozen=True)
class Record:
    record_id: str
    year: str
    month: str
    period: str
    title: str  # the workbook's recurring identity (e.g. "Debt Fund Portfolios")
    label: str  # the row's own full document_name, as published
    url: str
    filename: str
    source_row_id: str = ""
    file_period_conflict: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.record_id, self.url)


def records_from_payload(payload: dict, year: str) -> tuple[list[Record], list[dict]]:
    records: list[Record] = []
    malformed: list[dict] = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        acf = row.get("acf_fields") or {}
        label = _clean_text(acf.get("document_name") or row.get("title") or "")
        title = _workbook_title(label)
        month_name = (acf.get("month") or "").strip()
        month_number = _month_number(month_name)
        row_year = str(row.get("financial_year") or year)
        row_id = str(row.get("id") or "")
        for entry in acf.get("disclosure_files") or []:
            if not isinstance(entry, dict):
                continue
            url = direct_url(entry.get("url") or "")
            if not url.startswith(("http://", "https://")):
                malformed.append({"reason": "no usable url", "title": title, "label": label, "year": year, "raw": entry})
                continue
            if month_number is None:
                malformed.append({"reason": f"unparseable month {month_name!r}", "title": title, "label": label, "year": year, "url": url})
                continue
            record_id = str(entry.get("id") or f"{row_id}:{url}")
            period = f"{row_year}-{month_number:02d}"
            basename = unquote(Path(urlsplit(url).path).name)
            file_period = last_period(f"{label} {basename}", before=None)
            conflict = file_period if file_period and file_period != period else None
            records.append(
                Record(
                    record_id=record_id,
                    year=row_year,
                    month=month_name,
                    period=period,
                    title=title,
                    label=label,
                    url=url,
                    filename="",
                    source_row_id=row_id,
                    file_period_conflict=conflict,
                )
            )
    return records, malformed


def _assign_filenames(records: list[Record]) -> list[Record]:
    by_group: dict[tuple[str, str], list[Record]] = {}
    for record in records:
        by_group.setdefault((record.period, record.title), []).append(record)

    used: dict[str, str] = {}
    resolved: list[Record] = []
    for (period, title), group in by_group.items():
        for record in group:
            base = f"bandhan_summary_{_safe_slug(title)}_{period}"
            if len(group) > 1:
                base += f"_{_safe_slug(record.label)}"
            filename = f"{base}.xlsx"
            if filename in used and used[filename] != record.record_id:
                filename = f"{base}_{record.record_id}.xlsx"
            used[filename] = record.record_id
            resolved.append(
                Record(
                    record_id=record.record_id,
                    year=record.year,
                    month=record.month,
                    period=record.period,
                    title=record.title,
                    label=record.label,
                    url=record.url,
                    filename=filename,
                    source_row_id=record.source_row_id,
                    file_period_conflict=record.file_period_conflict,
                )
            )
    return resolved


def _dedupe_records(records: list[Record]) -> tuple[list[Record], list[dict]]:
    by_url: dict[str, Record] = {}
    duplicates: list[dict] = []
    for record in records:
        existing = by_url.get(record.url)
        if existing is None:
            by_url[record.url] = record
        elif existing.record_id != record.record_id:
            duplicates.append({"kept_record_id": existing.record_id, "dropped_record_id": record.record_id, "url": record.url})
    return list(by_url.values()), duplicates


# -- discovery -------------------------------------------------------------


@dataclass
class DiscoverySnapshot:
    records: list[Record] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    malformed: list[dict] = field(default_factory=list)
    years: list[str] = field(default_factory=list)

    def key_set(self) -> frozenset:
        return frozenset(record.key() for record in self.records)


def _learn_years(browser, playwright_timeout_error) -> list[str]:
    payload = _fetch_listing(browser, year=None, page_number=1, playwright_timeout_error=playwright_timeout_error)
    years = sorted({str(y) for y in (payload.get("financial_years") or [])})
    if not years:
        raise RuntimeError("Bandhan portfolio-summary page did not report any financial_years -- site structure may have changed")
    return years


def _discover_year(browser, year: str, playwright_timeout_error) -> tuple[list[Record], list[dict]]:
    records: list[Record] = []
    malformed: list[dict] = []
    page_number = 1
    while page_number <= _MAX_PAGES_GUARD:
        payload = _fetch_listing(browser, year=year, page_number=page_number, playwright_timeout_error=playwright_timeout_error)
        if payload.get("status") == "no_posts_found":
            break
        year_records, year_malformed = records_from_payload(payload, year)
        records.extend(year_records)
        malformed.extend(year_malformed)
        max_pages = int(payload.get("max_pages") or 1)
        if page_number >= max_pages:
            break
        page_number += 1
        if DISCOVERY_DELAY_SECONDS:
            time.sleep(DISCOVERY_DELAY_SECONDS)
    return records, malformed


def run_discovery_pass(browser, playwright_timeout_error, *, log_prefix: str = "discover") -> DiscoverySnapshot:
    years = _YEARS_OVERRIDE or _learn_years(browser, playwright_timeout_error)
    all_records: list[Record] = []
    all_malformed: list[dict] = []
    for year in years:
        print(f"{log_prefix}    year={year} ...", flush=True)
        year_records, year_malformed = _discover_year(browser, year, playwright_timeout_error)
        print(f"{log_prefix}    year={year} -> {len(year_records)} record(s)")
        all_records.extend(year_records)
        all_malformed.extend(year_malformed)
        if DISCOVERY_DELAY_SECONDS:
            time.sleep(DISCOVERY_DELAY_SECONDS)
    deduped, duplicates = _dedupe_records(all_records)
    deduped = _assign_filenames(deduped)
    return DiscoverySnapshot(records=deduped, duplicates=duplicates, malformed=all_malformed, years=years)


def stabilize_discovery(browser, playwright_timeout_error, *, max_attempts: int) -> tuple[DiscoverySnapshot, int, bool]:
    previous: DiscoverySnapshot | None = None
    snapshot = None
    for attempt in range(1, max_attempts + 1):
        snapshot = run_discovery_pass(browser, playwright_timeout_error, log_prefix=f"discover[{attempt}/{max_attempts}]")
        if previous is not None and previous.key_set() == snapshot.key_set():
            return snapshot, attempt, True
        previous = snapshot
    return snapshot, max_attempts, False


# -- download ---------------------------------------------------------------


class _AdaptiveThrottle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resume_at = 0.0
        self._penalty = 1.0

    def wait_if_throttled(self) -> None:
        with self._lock:
            resume_at = self._resume_at
        remaining = resume_at - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def penalize(self) -> None:
        with self._lock:
            self._penalty = min(self._penalty * 2, 60.0)
            self._resume_at = time.monotonic() + self._penalty

    def relax(self) -> None:
        with self._lock:
            self._penalty = max(1.0, self._penalty * 0.7)


_throttle = _AdaptiveThrottle()
_thread_local = threading.local()


def _thread_session(pool_size: int) -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is not None:
        return session
    session = create_session()
    retries = session.get_adapter("https://").max_retries
    adapter = HTTPAdapter(max_retries=retries, pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    _thread_local.session = session
    return session


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return any(head.startswith(marker) for marker in _HTML_MARKERS)


def _validate_bytes(data: bytes, suffix: str, filename: str) -> None:
    if not data:
        raise RuntimeError(f"{filename}: empty payload")
    if _looks_like_html(data):
        raise RuntimeError(f"{filename}: server returned an HTML page (bot wall / error page), not the file")
    magics = _NON_HTML_MAGIC.get(suffix)
    if magics and not any(data.startswith(magic) for magic in magics):
        raise RuntimeError(f"{filename}: content does not start with the expected magic bytes for .{suffix}")


def _validate_openable(data: bytes, suffix: str) -> None:
    """Confirm the downloaded bytes actually open as the claimed type.

    Validated from an in-memory buffer: openpyxl refuses to open a file
    based on its extension alone, and the not-yet-renamed .part temp file
    would fail that check even for perfectly valid content.
    """
    import io

    if suffix in {"xlsx", "xlsm", "xlsb"}:
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            list(workbook.worksheets)
        finally:
            workbook.close()
    elif suffix == "xls":
        try:
            import xlrd

            xlrd.open_workbook(file_contents=data)
        except xlrd.XLRDError:
            import openpyxl

            workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            workbook.close()
    elif suffix == "zip":
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise RuntimeError(f"archive member {bad!r} failed its CRC check")


@dataclass
class DownloadOutcome:
    record: Record
    status: str
    path: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None


def _conditional_headers(manifest_entry: dict | None, destination: Path) -> dict[str, str]:
    if not manifest_entry:
        return {}
    digest = manifest_entry.get("sha256")
    if not digest or not destination.is_file():
        return {}
    try:
        if _sha256_file(destination) != digest:
            return {}
    except OSError:
        return {}
    headers = {}
    if manifest_entry.get("etag"):
        headers["If-None-Match"] = manifest_entry["etag"]
    if manifest_entry.get("last_modified"):
        headers["If-Modified-Since"] = manifest_entry["last_modified"]
    return headers


def _download_record(record: Record, output_dir: Path, prior_manifest: dict, *, pool_size: int, retry_total: int, retry_backoff: float, timeout: tuple[int, int]) -> DownloadOutcome:
    destination = output_dir / record.period / record.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    prior_entry = prior_manifest.get(record.url)
    conditional_headers = _conditional_headers(prior_entry, destination)

    session = _thread_session(pool_size)
    last_exc: Exception | None = None
    for attempt in range(1, retry_total + 2):
        _throttle.wait_if_throttled()
        try:
            response = session.get(
                record.url,
                headers={"Referer": PAGE_URL, **conditional_headers},
                stream=True,
                timeout=timeout,
                allow_redirects=True,
            )
            try:
                if response.status_code == 304:
                    _throttle.relax()
                    return DownloadOutcome(
                        record=record,
                        status=STATUS_IDENTICAL,
                        path=prior_entry.get("path"),
                        bytes=prior_entry.get("bytes"),
                        sha256=prior_entry.get("sha256"),
                        etag=prior_entry.get("etag"),
                        last_modified=prior_entry.get("last_modified"),
                    )
                if response.status_code == 429:
                    _throttle.penalize()
                response.raise_for_status()
                data = response.content
            finally:
                response.close()

            suffix = (Path(record.filename).suffix.lstrip(".") or "xlsx").lower()
            _validate_bytes(data, suffix, record.filename)
            try:
                _validate_openable(data, suffix)
            except Exception as exc:
                raise RuntimeError(f"{record.filename}: downloaded but could not be opened as .{suffix}: {exc}") from exc

            digest = _sha256_bytes(data)
            if destination.is_file():
                try:
                    existing_digest = _sha256_file(destination)
                except OSError:
                    existing_digest = None
                if existing_digest == digest:
                    _throttle.relax()
                    return DownloadOutcome(
                        record=record, status=STATUS_IDENTICAL, path=str(destination.relative_to(output_dir)),
                        bytes=len(data), sha256=digest,
                        etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"),
                    )
                if existing_digest is not None:
                    alt_name = f"{Path(record.filename).stem}_{record.record_id}{Path(record.filename).suffix}"
                    destination = destination.with_name(alt_name)

            with tempfile.NamedTemporaryFile(prefix=".bandhan-psm-", suffix=".part", dir=destination.parent, delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(data)
            os.replace(temp_path, destination)
            _throttle.relax()
            return DownloadOutcome(
                record=record, status=STATUS_DOWNLOADED, path=str(destination.relative_to(output_dir)),
                bytes=len(data), sha256=digest,
                etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"),
            )
        except Exception as exc:  # noqa: BLE001 -- every failure mode is retried the same way here
            last_exc = exc
            if attempt <= retry_total:
                time.sleep(retry_backoff * attempt)
    return DownloadOutcome(record=record, status=STATUS_FAILED, error=str(last_exc))


def download_all(records: list[Record], output_dir: Path, prior_manifest: dict, *, max_workers: int) -> list[DownloadOutcome]:
    config = settings()
    timeout = (config.connect_timeout, config.read_timeout)
    outcomes: list[DownloadOutcome] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _download_record, record, output_dir, prior_manifest,
                pool_size=max_workers, retry_total=config.retry_total, retry_backoff=config.retry_backoff, timeout=timeout,
            ): record
            for record in records
        }
        completed = 0
        for future in as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            completed += 1
            tag = {STATUS_DOWNLOADED: "downloaded", STATUS_IDENTICAL: "unchanged ", STATUS_FAILED: "failed    "}[outcome.status]
            print(f"{tag} {outcome.record.period} {outcome.record.filename}" + (f" -- {outcome.error}" if outcome.error else ""))
            if completed % 25 == 0:
                print(f"... {completed}/{len(records)} done")
    return outcomes


# -- manifests / reporting --------------------------------------------------


def _manifest_path(output_dir: Path) -> Path:
    return output_dir / "download_manifest.json"


def _read_download_manifest(output_dir: Path) -> dict:
    path = _manifest_path(output_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data.get("by_url", {}) if isinstance(data, dict) else {}


def _write_download_manifest(output_dir: Path, by_url: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(output_dir)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"by_url": by_url}, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _write_discovery_manifest(output_dir: Path, snapshot: DiscoverySnapshot, *, stable: bool, passes: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "amc": AMC,
        "source_page_url": PAGE_URL,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "years": snapshot.years,
        "stable": stable,
        "discovery_passes": passes,
        "total_records": len(snapshot.records),
        "duplicates": snapshot.duplicates,
        "malformed_rows": snapshot.malformed,
        "period_conflicts": [
            {"record_id": r.record_id, "url": r.url, "listed_period": r.period, "filename_period": r.file_period_conflict}
            for r in snapshot.records if r.file_period_conflict
        ],
        "records": [
            {
                "record_id": r.record_id,
                "year": r.year,
                "month": r.month,
                "period": r.period,
                "title": r.title,
                "label": r.label,
                "url": r.url,
                "filename": r.filename,
            }
            for r in sorted(snapshot.records, key=lambda r: (r.period, r.title, r.record_id))
        ],
    }
    json_path = output_dir / "discovery_manifest.json"
    temp = json_path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, json_path)

    csv_path = output_dir / "discovery_manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", "year", "month", "period", "title", "label", "url", "filename"])
        for r in payload["records"]:
            writer.writerow([r["record_id"], r["year"], r["month"], r["period"], r["title"], r["label"], r["url"], r["filename"]])


def _write_download_csv(output_dir: Path, outcomes: list[DownloadOutcome]) -> None:
    csv_path = output_dir / "download_manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", "period", "filename", "status", "bytes", "sha256", "error"])
        for outcome in sorted(outcomes, key=lambda o: (o.record.period, o.record.title, o.record.record_id)):
            writer.writerow([
                outcome.record.record_id, outcome.record.period, outcome.record.filename,
                outcome.status, outcome.bytes or "", outcome.sha256 or "", outcome.error or "",
            ])


def _validate_on_disk(output_dir: Path, outcome: DownloadOutcome) -> bool:
    if outcome.status not in {STATUS_DOWNLOADED, STATUS_IDENTICAL} or not outcome.path:
        return False
    path = output_dir / outcome.path
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if outcome.sha256 and _sha256_file(path) != outcome.sha256:
        return False
    return True


def render_report(*, discovered: int, downloaded: int, identical: int, failed: int,
                   final_discovered: int, missing: int, duplicates: int, status: str) -> str:
    return (
        f"Discovered: {discovered}\n"
        f"Downloaded successfully: {downloaded}\n"
        f"Already existing + identical: {identical}\n"
        f"Failed: {failed}\n"
        f"Final discovered: {final_discovered}\n"
        f"Missing: {missing}\n"
        f"Duplicates: {duplicates}\n"
        f"STATUS: {status}"
    )


# -- orchestration -----------------------------------------------------------


def main() -> int:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"Bandhan discovery requires Playwright; install requirements and Chromium: {exc}")
        return 1

    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"bandhan portfolio summary (monthly): source={PAGE_URL}")
    print(f"output={output_dir} workers={MAX_WORKERS}")

    config = settings()
    total_duplicates = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=config.headless)
        try:
            snapshot, passes, stable = stabilize_discovery(browser, PlaywrightTimeoutError, max_attempts=MAX_DISCOVERY_ATTEMPTS)
            if not stable:
                print(f"WARNING: discovery did not stabilize after {passes} passes -- proceeding with the last snapshot")
            print(f"discovery stable after {passes} pass(es): {len(snapshot.records)} record(s) across {len(snapshot.years)} year(s)")
            total_duplicates += len(snapshot.duplicates)
            _write_discovery_manifest(output_dir, snapshot, stable=stable, passes=passes)

            discovered_count = len(snapshot.records)
            if discovered_count == 0:
                report_text = render_report(discovered=0, downloaded=0, identical=0, failed=0, final_discovered=0, missing=0, duplicates=0, status="INCOMPLETE")
                print(report_text)
                return 1

            prior_manifest = _read_download_manifest(output_dir)
            all_outcomes: dict[str, DownloadOutcome] = {}
            pending = list(snapshot.records)

            for round_number in range(1, MAX_RECONCILE_ROUNDS + 1):
                print(f"--- download round {round_number}/{MAX_RECONCILE_ROUNDS}: {len(pending)} file(s) ---")
                outcomes = download_all(pending, output_dir, prior_manifest, max_workers=MAX_WORKERS)
                for outcome in outcomes:
                    all_outcomes[outcome.record.record_id] = outcome
                    if outcome.status in {STATUS_DOWNLOADED, STATUS_IDENTICAL} and outcome.path:
                        prior_manifest[outcome.record.url] = {
                            "record_id": outcome.record.record_id, "path": outcome.path, "bytes": outcome.bytes,
                            "sha256": outcome.sha256, "etag": outcome.etag, "last_modified": outcome.last_modified,
                            "downloaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        }
                _write_download_manifest(output_dir, prior_manifest)

                print("validating downloads against disk ...")
                for outcome in list(all_outcomes.values()):
                    if outcome.status in {STATUS_DOWNLOADED, STATUS_IDENTICAL} and not _validate_on_disk(output_dir, outcome):
                        outcome.status = STATUS_FAILED
                        outcome.error = outcome.error or "post-download validation failed: file missing/corrupt on disk"

                print(f"rediscovering to reconcile (round {round_number}) ...")
                snapshot, passes, stable = stabilize_discovery(browser, PlaywrightTimeoutError, max_attempts=MAX_DISCOVERY_ATTEMPTS)
                if not stable:
                    print(f"WARNING: reconcile discovery did not stabilize after {passes} passes")
                total_duplicates += len(snapshot.duplicates)

                validated_ids = {oid for oid, o in all_outcomes.items() if o.status in {STATUS_DOWNLOADED, STATUS_IDENTICAL}}
                missing_records = [r for r in snapshot.records if r.record_id not in validated_ids]
                if not missing_records:
                    break
                print(f"{len(missing_records)} record(s) missing after round {round_number}; retrying")
                pending = missing_records

            _write_discovery_manifest(output_dir, snapshot, stable=stable, passes=passes)
            _write_download_csv(output_dir, list(all_outcomes.values()))
        finally:
            browser.close()

    final_ids = {r.record_id for r in snapshot.records}
    validated_ids = {oid for oid, o in all_outcomes.items() if o.status in {STATUS_DOWNLOADED, STATUS_IDENTICAL}}
    downloaded_count = sum(1 for o in all_outcomes.values() if o.status == STATUS_DOWNLOADED)
    identical_count = sum(1 for o in all_outcomes.values() if o.status == STATUS_IDENTICAL)
    failed_count = sum(1 for o in all_outcomes.values() if o.status == STATUS_FAILED)
    missing_ids = final_ids - validated_ids
    status = "COMPLETE" if not missing_ids and failed_count == 0 else "INCOMPLETE"

    report_text = render_report(
        discovered=discovered_count, downloaded=downloaded_count, identical=identical_count, failed=failed_count,
        final_discovered=len(final_ids), missing=len(missing_ids), duplicates=total_duplicates, status=status,
    )
    print("\n" + report_text)

    summary_path = output_dir / "run_report.json"
    summary_path.write_text(json.dumps({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "discovered": discovered_count, "downloaded": downloaded_count, "already_identical": identical_count,
        "failed": failed_count, "final_discovered": len(final_ids), "missing": len(missing_ids),
        "duplicates": total_duplicates, "status": status,
        "missing_record_ids": sorted(missing_ids),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    return 0 if status == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())

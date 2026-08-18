"""Full-history downloader for Bandhan's per-scheme monthly/half-yearly portfolios.

    https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/monthly-half-yearly

Standalone tool: discovers *every* year the site currently exposes (including
the old IDFC-era filings) and downloads every scheme's workbook for every
period, rather than the single AMC_PERIOD month verified/Bandhan_Mutual_Fund.py
downloads. Nothing here modifies that file or any other existing script.

-- The site's API --------------------------------------------------------

The page is a React SPA. Its listing calls one endpoint:

    POST https://pnservices.bandhanmutual.com/internal/investorservices/
         encdec/investor/v1/dashboard/cms-call

Both the request body and the response body are encrypted client-side
(AES + an RSA-signed request header, per the page's own bundle), so there is
no plaintext JSON to POST directly without reimplementing that scheme from
an obfuscated 11MB bundle. Instead -- the same technique already used by
verified/Bandhan_Mutual_Fund.py -- a Playwright ``add_init_script`` hook is
installed *before* the page's own JS runs. It wraps the page's own
``JSON.stringify`` to substitute a different set of plaintext query
parameters into the request the app was about to encrypt and send, and wraps
``JSON.parse`` to capture the plaintext response after the app's own code
decrypts it. No key, signature, or fingerprint is derived, stored, or
replayed anywhere in this file; the browser signs and sends its own request
exactly as it always would.

Verified live (see the conversation this script was built from):

  * type="SCHEME_PORTFOLIOS", subcategory="monthly-and-half-yearly".
  * Dropping the request's "month" key returns every month of the given
    year in one call (confirmed: year 2023 -> 626 records across 3 pages
    at 300/page, spanning January-December).
  * Dropping both "financial_year" and "month" returns the site's own
    current default period *and*, critically, the full list of years it
    offers in the response's own "financial_years" field -- that is how
    this script learns which years exist, instead of hardcoding any.
  * Pagination is real (a busy year needs multiple pages) and is followed
    via the response's own "max_pages"/"current_page" fields.
  * The actual files (cmsnew.bandhanmutual.com and storage.googleapis.com)
    are plain static downloads over HTTPS -- no browser, no auth, and both
    hosts support conditional GET (ETag / Last-Modified -> 304), which is
    what makes a re-run of this script cheap.

-- Workflow ---------------------------------------------------------------

    discover (stabilized) -> download (concurrent) -> validate
        -> rediscover -> reconcile (download anything new/missing)
        -> final verification -> report

Discovery is run repeatedly and compared by the full set of (record id, url)
pairs -- not just a count -- until two consecutive passes agree, bounded by
BANDHAN_MHY_MAX_DISCOVERY_ATTEMPTS. Downloads run concurrently
(BANDHAN_MHY_MAX_WORKERS, default 8 -- benchmarked against the live CMS host
at ~4.7x a single connection's throughput with no rate-limit errors) using
one retrying requests.Session per worker thread.
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
    "BANDHAN_MHY_PAGE_URL",
    "https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/monthly-half-yearly",
)
API_REQUEST_TYPE = "SCHEME_PORTFOLIOS"
API_SUBCATEGORY = "monthly-and-half-yearly"

# The marker that identifies our own listing response among everything the
# page's own JS parses: the app never asks for this many posts per page on
# its own, and the API echoes the value straight back in every payload.
PER_PAGE = 300
_MAX_PAGES_GUARD = 50

_PAGE_LOAD_ATTEMPTS = 3
_RESPONSE_TIMEOUT_MS = 45_000
_RETRY_BACKOFF_SECONDS = 2.0

# discover -> discover again -> compare, repeated until two consecutive
# passes agree on the exact (record_id, url) set, bounded here.
_DEFAULT_MAX_DISCOVERY_ATTEMPTS = 5
# download -> validate -> rediscover -> reconcile, bounded here so a site
# that never stabilizes can't loop forever.
_DEFAULT_MAX_RECONCILE_ROUNDS = 3

_DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "bandhan_monthly_half_yearly"
_MONTHLY_DIRNAME = None  # files live directly under <output_dir>/<period>/

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


MAX_WORKERS = _env_int("BANDHAN_MHY_MAX_WORKERS", 8)
MAX_DISCOVERY_ATTEMPTS = _env_int("BANDHAN_MHY_MAX_DISCOVERY_ATTEMPTS", _DEFAULT_MAX_DISCOVERY_ATTEMPTS)
MAX_RECONCILE_ROUNDS = _env_int("BANDHAN_MHY_MAX_RECONCILE_ROUNDS", _DEFAULT_MAX_RECONCILE_ROUNDS)
DISCOVERY_DELAY_SECONDS = _env_float("BANDHAN_MHY_DISCOVERY_DELAY_SECONDS", 0.4)
# Testing/debugging escape hatch only -- comma-separated years to restrict a
# run to (e.g. "2024,2025"). Empty (the default) discovers every year the
# site itself currently offers; nothing about the years is ever hardcoded
# for a real run.
_YEARS_OVERRIDE = [y.strip() for y in os.getenv("BANDHAN_MHY_YEARS", "").split(",") if y.strip()]


def _output_dir() -> Path:
    raw = os.getenv("BANDHAN_MHY_OUTPUT_DIR", "")
    path = Path(raw) if raw else _DEFAULT_OUTPUT_DIR
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


# -- the page-side hook -------------------------------------------------

_INIT_SCRIPT_TEMPLATE = """
(() => {
  const QUERY = %(query)s;
  window.__mhyCaptures = [];
  const stringify = JSON.stringify;
  const parse = JSON.parse;
  JSON.stringify = function (value, replacer, space) {
    try {
      if (value && value.type === QUERY.type && value.data) {
        const data = Object.assign({}, value.data, QUERY.data);
        delete data.acf_key1;
        delete data.acf_value1;
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
          && (result.scheme_titles !== undefined || result.status === 'no_posts_found')) {
        window.__mhyCaptures.push(result);
      }
    } catch (error) { /* capturing is best-effort, never break the app */ }
    return result;
  };
})();
"""

_CAPTURE_SUMMARY_JS = """() => (window.__mhyCaptures || []).map(
    capture => ({
        status: capture && capture.status,
        posts_per_page: capture && capture.posts_per_page,
        current_page: capture && capture.current_page,
    })
)"""


def _init_script(*, year: str | None, month_wildcard: bool, page_number: int) -> str:
    """The page-side hook, parameterised for one listing request.

    ``year=None`` (with month always wildcarded to None here) asks for
    whatever period the page defaults to -- used once at startup purely to
    read the site's own "financial_years" list. ``year`` set and
    ``month_wildcard=True`` asks for every month of that year in one call.
    """
    data: dict[str, object] = {
        "subcategory": API_SUBCATEGORY,
        "page": page_number,
        "posts_per_page": PER_PAGE,
        "financial_year": year,
        "month": None if month_wildcard else None,
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
    """Load the page once with the hook installed and return the listing payload.

    A fresh page per request keeps this stateless: the query is baked into
    the init script, so the very first request the app makes on load is
    already the one wanted. Raises RuntimeError only on a transport-level
    failure (timeout / no response at all); a genuine "nothing published"
    answer from the site is returned as-is for the caller to classify.
    """
    config = settings()
    last_error = "no response"
    for attempt in range(1, _PAGE_LOAD_ATTEMPTS + 1):
        page = browser.new_page()
        try:
            page.add_init_script(_init_script(year=year, month_wildcard=True, page_number=page_number))
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=config.read_timeout * 1000)
            deadline = time.monotonic() + _RESPONSE_TIMEOUT_MS / 1000
            while time.monotonic() < deadline:
                index = _capture_index(page.evaluate(_CAPTURE_SUMMARY_JS), page_number)
                if index is not None:
                    return page.evaluate("index => window.__mhyCaptures[index]", index)
                page.wait_for_timeout(500)
            last_error = f"timed out waiting {_RESPONSE_TIMEOUT_MS // 1000}s for the site's listing API"
        except playwright_timeout_error as exc:
            last_error = f"page load timed out: {exc}"
        finally:
            page.close()
        if attempt < _PAGE_LOAD_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"Bandhan disclosure page returned no listing response "
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


def _canonical_scheme_name(option_name: str) -> str:
    return re.sub(r"\s*-\s*growth\s*$", "", option_name or "", flags=re.I).strip()


def _month_number(name: str) -> int | None:
    return MONTH_ALIASES.get((name or "").strip().lower())


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


@dataclass(frozen=True)
class Record:
    record_id: str
    year: str
    month: str
    period: str  # "YYYY-MM", derived from the site's own year+month fields
    scheme: str
    label: str
    url: str
    filename: str
    source_row_id: str = ""
    file_period_conflict: str | None = None  # set if the filename's own date disagrees

    def key(self) -> tuple[str, str]:
        return (self.record_id, self.url)


def records_from_payload(payload: dict, year: str) -> tuple[list[Record], list[dict]]:
    """Every downloadable document in one listing payload, as flat records.

    Returns (records, malformed_rows) -- malformed_rows are rows the API
    returned without enough information to build a Document from (missing
    url, unparseable month); they are reported, never silently dropped.
    """
    records: list[Record] = []
    malformed: list[dict] = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        acf = row.get("acf_fields") or {}
        mapping = acf.get("funds_mapping") or {}
        label = _clean_text(acf.get("document_name") or row.get("title") or "")
        scheme = _canonical_scheme_name(_clean_text(mapping.get("post_title") or "")) or label or "bandhan"
        month_name = (acf.get("month") or "").strip()
        month_number = _month_number(month_name)
        row_year = str(row.get("financial_year") or year)
        row_id = str(row.get("id") or "")
        for entry in acf.get("disclosure_files") or []:
            if not isinstance(entry, dict):
                continue
            url = direct_url(entry.get("url") or "")
            if not url.startswith(("http://", "https://")):
                malformed.append({"reason": "no usable url", "scheme": scheme, "label": label, "year": year, "raw": entry})
                continue
            if month_number is None:
                malformed.append({"reason": f"unparseable month {month_name!r}", "scheme": scheme, "label": label, "year": year, "url": url})
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
                    scheme=scheme,
                    label=label,
                    url=url,
                    filename="",  # assigned below, once every record for the period is known
                    source_row_id=row_id,
                    file_period_conflict=conflict,
                )
            )
    return records, malformed


def _assign_filenames(records: list[Record]) -> list[Record]:
    """Deterministic, collision-free filenames -- independent of discovery order.

    Grouped by (period, scheme): a scheme with one document for a period
    keeps a plain name; more than one (a monthly + a half-yearly workbook in
    the same month) gets its own document label folded in, and any
    still-remaining collision gets the record id appended rather than
    silently overwriting another file.
    """
    by_group: dict[tuple[str, str], list[Record]] = {}
    for record in records:
        by_group.setdefault((record.period, record.scheme), []).append(record)

    used: dict[str, str] = {}  # filename -> record_id that claimed it
    resolved: list[Record] = []
    for (period, scheme), group in by_group.items():
        for record in group:
            scheme_part = re.sub(r"^\s*bandhan\s+", "", record.scheme, flags=re.I)
            base = f"bandhan_{_safe_slug(scheme_part)}_{period}"
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
                    scheme=record.scheme,
                    label=record.label,
                    url=record.url,
                    filename=filename,
                    source_row_id=record.source_row_id,
                    file_period_conflict=record.file_period_conflict,
                )
            )
    return resolved


def _dedupe_records(records: list[Record]) -> tuple[list[Record], list[dict]]:
    """Collapse duplicate API/CMS rows: identical URL wins by first occurrence.

    Distinct record_ids that happen to point at the exact same URL are the
    same physical file listed twice and are collapsed too. Both cases are
    reported, never silently dropped.
    """
    by_url: dict[str, Record] = {}
    duplicates: list[dict] = []
    for record in records:
        existing = by_url.get(record.url)
        if existing is None:
            by_url[record.url] = record
        elif existing.record_id != record.record_id:
            duplicates.append({"kept_record_id": existing.record_id, "dropped_record_id": record.record_id, "url": record.url})
        # else: the exact same record_id+url pair -- pagination overlap, not
        # worth reporting as a distinct duplicate.
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
        raise RuntimeError("Bandhan disclosure page did not report any financial_years -- site structure may have changed")
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
    """Discover repeatedly until two consecutive passes agree on every
    (record_id, url), bounded by ``max_attempts`` total passes.

    Returns (snapshot, passes_run, stable). If the bound is hit without
    agreement, the last snapshot is returned with stable=False so the
    caller can proceed but flag the run as such in the final report.
    """
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
    """A shared, best-effort brake across worker threads.

    core.http's urllib3 Retry already retries a single request's own
    429/5xx at the transport level. This is one level above: if the server
    is clearly asking everyone to slow down, new requests from *other*
    threads pause too, rather than each thread only ever learning about
    its own failures.
    """

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

    Validated from an in-memory buffer, not the on-disk .part path: openpyxl
    refuses to open a file based on its extension alone, and the .part
    suffix used for the not-yet-renamed temp file would fail that check
    even though the content is perfectly valid.
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
            # A .xls file that is actually OOXML/zip under the hood (some
            # sites mislabel the extension) -- accept if openpyxl can read it.
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
                    # Different content already sits under this name --
                    # never silently overwrite it. Save alongside instead.
                    alt_name = f"{Path(record.filename).stem}_{record.record_id}{Path(record.filename).suffix}"
                    destination = destination.with_name(alt_name)

            with tempfile.NamedTemporaryFile(prefix=".bandhan-mhy-", suffix=".part", dir=destination.parent, delete=False) as handle:
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
                "scheme": r.scheme,
                "label": r.label,
                "url": r.url,
                "filename": r.filename,
            }
            for r in sorted(snapshot.records, key=lambda r: (r.period, r.scheme, r.record_id))
        ],
    }
    json_path = output_dir / "discovery_manifest.json"
    temp = json_path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, json_path)

    csv_path = output_dir / "discovery_manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", "year", "month", "period", "scheme", "label", "url", "filename"])
        for r in payload["records"]:
            writer.writerow([r["record_id"], r["year"], r["month"], r["period"], r["scheme"], r["label"], r["url"], r["filename"]])


def _write_download_csv(output_dir: Path, outcomes: list[DownloadOutcome]) -> None:
    csv_path = output_dir / "download_manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", "period", "filename", "status", "bytes", "sha256", "error"])
        for outcome in sorted(outcomes, key=lambda o: (o.record.period, o.record.scheme, o.record.record_id)):
            writer.writerow([
                outcome.record.record_id, outcome.record.period, outcome.record.filename,
                outcome.status, outcome.bytes or "", outcome.sha256 or "", outcome.error or "",
            ])


def _validate_on_disk(output_dir: Path, outcome: DownloadOutcome) -> bool:
    """Re-check a file already reported downloaded/identical against what's
    actually on disk right now -- catches disk corruption or a stray
    concurrent modification between download and report."""
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
    print(f"bandhan monthly/half-yearly: source={PAGE_URL}")
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
                # A record_id validated in an earlier round but absent from
                # this fresh snapshot is simply not "missing" -- it is no
                # longer part of what the site currently lists.
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

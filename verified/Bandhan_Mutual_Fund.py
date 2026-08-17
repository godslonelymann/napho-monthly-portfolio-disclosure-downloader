from __future__ import annotations

import html as html_module
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import settings
from core.discovery import DiscoveryResult, PeriodUnavailable, document_from_link, dedupe_documents
from core.periods import last_period, month_name, period_conflicts


AMC = "bandhan"
# The "downloads/portfolio-summary/monthly" page only ever lists one
# consolidated debt-schemes workbook -- it structurally cannot cover
# equity/hybrid/index/ETF/FOF schemes (it also offers a 2018 that this page
# doesn't, but only for that one debt workbook). This disclosure page is the
# per-scheme one: one file per scheme, every scheme type.
PAGE_URL = os.getenv(
    "BANDHAN_PAGE_URL",
    "https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/monthly-half-yearly",
)

# How the page's own React app asks its API for a listing. The request body
# on the wire is encrypted by the site's JS, and nothing here reimplements
# or bypasses that: the browser is left to build, sign and send the request
# exactly as it always does. All this adapter does is (a) hand the app a
# different set of *plaintext* query parameters just before its own
# JSON.stringify runs, and (b) read the app's own decrypted response after
# its own JSON.parse. No key, api-key, fingerprint or signature is
# recreated, stored, or hardcoded anywhere in this file.
API_REQUEST_TYPE = "SCHEME_PORTFOLIOS"
API_SUBCATEGORY = "monthly-and-half-yearly"
# One listing request returns every scheme for the month. The page's own UI
# asks for a single scheme at a time (its third dropdown is a filter on this
# same query), which is why the previous version of this adapter had to
# drive ~79 dropdown selections per month and could not finish one month
# inside the range runner's per-cell timeout. Dropping that one filter turns
# a month into a single page load.
PER_PAGE = 200
# Marker that identifies our own response among the page's: the app never
# asks for this many posts per page on its own, and the API echoes the
# value back in every payload.
_PAGE_LOAD_ATTEMPTS = 3
_RESPONSE_TIMEOUT_MS = 45_000
_RETRY_BACKOFF_SECONDS = 2.0
_MAX_PAGES_GUARD = 50

# Older documents live on the CMS host, newer ones on Google Cloud Storage;
# both are plain static files fetched over HTTP by core.cli (no browser).
# Some rows still point at the site's own download shim, which carries the
# real storage location in a "filepath" query parameter.
DOWNLOAD_SHIM_MARKER = "/investor/v1/dashboard/download-doc"

MONTHLY_SUBDIR = "monthly"


# -- the page-side hook --------------------------------------------------
# Installed with add_init_script, i.e. before any of the site's own code
# runs. It wraps JSON.stringify to substitute our query into the outgoing
# listing request (on a copy -- the app's own state object is left alone),
# and JSON.parse to capture the decrypted listing response the app receives.
_INIT_SCRIPT_TEMPLATE = """
(() => {
  const QUERY = %(query)s;
  window.__bandhanCaptures = [];
  const stringify = JSON.stringify;
  const parse = JSON.parse;
  JSON.stringify = function (value, replacer, space) {
    try {
      if (value && value.type === QUERY.type && value.data) {
        const data = Object.assign({}, value.data, QUERY.data);
        delete data.acf_key1;    // the scheme filter: dropped so the
        delete data.acf_value1;  // response covers every scheme at once
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
          && (result.scheme_titles || result.status === 'no_posts_found')) {
        window.__bandhanCaptures.push(result);
      }
    } catch (error) { /* capturing is best-effort, never break the app */ }
    return result;
  };
})();
"""


def _init_script(period: str | None, page_number: int) -> str:
    """The page-side hook, parameterised for one listing request.

    ``period`` of None asks for whatever year/month the page defaults to --
    used to read the site's own list of available years when the requested
    period produced nothing at all.
    """
    data: dict[str, object] = {
        "subcategory": API_SUBCATEGORY,
        "page": page_number,
        "posts_per_page": PER_PAGE,
        # null is stripped page-side, leaving the app's own value in place.
        "financial_year": period[:4] if period else None,
        "month": month_name(period) if period else None,
    }
    query = {"type": API_REQUEST_TYPE, "data": data}
    return _INIT_SCRIPT_TEMPLATE % {"query": json.dumps(query)}


def capture_index(summaries: list, page_number: int) -> int | None:
    """Which capture is the response to *our* request, if it has landed yet.

    PER_PAGE is the marker (the app never asks for that many by itself) and
    the page number disambiguates our own successive requests. A
    "no_posts_found" body carries no echo at all -- that is what the site
    returns for a year it doesn't publish -- so it is accepted as-is and
    left for the caller to classify.

    Takes cheap {status, posts_per_page, current_page} summaries rather than
    whole payloads: this runs every poll, and a month's listing is hundreds
    of kilobytes that would otherwise cross the browser bridge each time.
    """
    for index, summary in enumerate(summaries or []):
        if not isinstance(summary, dict):
            continue
        if summary.get("status") == "no_posts_found":
            return index
        if summary.get("posts_per_page") == PER_PAGE and summary.get("current_page") == page_number:
            return index
    return None


_CAPTURE_SUMMARY_JS = """() => (window.__bandhanCaptures || []).map(
    capture => ({
        status: capture && capture.status,
        posts_per_page: capture && capture.posts_per_page,
        current_page: capture && capture.current_page,
    })
)"""


def _fetch_listing(browser, period: str | None, page_number: int, playwright_timeout_error) -> dict:
    """Load the page once with the hook installed and return the listing payload.

    A fresh page per request keeps this stateless: the query is baked into
    the init script, so the very first request the app makes on load is
    already the one we want and no dropdown has to be driven at all.

    Raises RuntimeError if the site never answers after every attempt --
    that is an outage/timeout, deliberately distinct from the site
    answering "nothing here", which returns a payload for the caller to
    classify.
    """
    config = settings()
    last_error = "no response"
    for attempt in range(1, _PAGE_LOAD_ATTEMPTS + 1):
        page = browser.new_page()
        try:
            page.add_init_script(_init_script(period, page_number))
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=config.read_timeout * 1000)
            deadline = time.monotonic() + _RESPONSE_TIMEOUT_MS / 1000
            while time.monotonic() < deadline:
                index = capture_index(page.evaluate(_CAPTURE_SUMMARY_JS), page_number)
                if index is not None:
                    return page.evaluate("index => window.__bandhanCaptures[index]", index)
                page.wait_for_timeout(500)
            last_error = f"timed out waiting {_RESPONSE_TIMEOUT_MS // 1000}s for the site's listing API"
        except playwright_timeout_error as exc:
            last_error = f"page load timed out: {exc}"
        finally:
            page.close()
        if attempt < _PAGE_LOAD_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)  # exponential-ish backoff
    # Worded so the range runner reads this as a transport failure worth
    # retrying (backfill_range._HTTP_ERROR_RE), not as a site-structure
    # change or as "this period has nothing".
    raise RuntimeError(
        f"Bandhan disclosure page returned no listing response "
        f"(page {page_number}, attempts<={_PAGE_LOAD_ATTEMPTS}): {last_error}"
    )


# -- payload handling (pure; no browser involved) ------------------------


def direct_url(raw_url: str) -> str:
    """The real storage URL behind a listing entry.

    Most rows already carry the storage URL (googleapis.com for recent
    months, the CMS host for older ones). Some carry the site's own
    download shim instead, which only redirects: its "filepath" query
    parameter is the actual location, so it is preferred whenever it is
    itself a usable absolute URL.
    """
    raw_url = (raw_url or "").strip()
    if DOWNLOAD_SHIM_MARKER not in raw_url:
        return raw_url
    filepath = (parse_qs(urlsplit(raw_url).query).get("filepath") or [""])[0]
    filepath = unquote(filepath).strip()
    if filepath.startswith(("http://", "https://")):
        return filepath
    return raw_url


def _canonical_scheme_name(option_name: str) -> str:
    """ICRA's Fund_Name never carries the site's plan-option suffix, and the
    site's own document titles drop it too, so this is the scheme identity
    the audit's manifest matching needs to see."""
    return re.sub(r"\s*-\s*growth\s*$", "", option_name or "", flags=re.I).strip()


def _safe_filename(scheme_name: str, period: str) -> str:
    name = re.sub(r"^\s*bandhan\s+", "", scheme_name, flags=re.I)
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"bandhan_{slug}_{period}.xlsx"


def _clean_text(value: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).replace("–", "-").strip()


def _row_is_for_period(*, month_field: str, evidence: str, period: str) -> bool:
    """Does this listing row really describe ``period``?

    The payload is already scoped to the requested year and month (the API
    echoes both back), so this is a safety net against a mislabelled row --
    e.g. the site files the odd December document under a year whose other
    months are unrelated. Only the row's own month field and its
    title/filename are consulted; the storage URL's directory is *not*
    (that is the upload date, not the as-of date, and routinely differs).
    """
    month_field = (month_field or "").strip().lower()
    if month_field and month_field[:3] != month_name(period).lower()[:3]:
        return False
    return not period_conflicts(evidence, period)


def records_from_payload(payload: dict, period: str, misfiled: list | None = None) -> list[dict]:
    """Every downloadable document in one listing payload, as flat records.

    Each record is {"scheme", "label", "url", "filename"}. Filenames are
    assigned here (not at download time) because two documents for one
    scheme in the same month -- the monthly portfolio and a half-yearly one
    -- would otherwise collide, and core.cli treats a filename collision as
    a hard discovery error.

    A row the site files under this period but whose own title names a
    different one (it does happen: the site's only December-2020 entry is a
    workbook dated 31 Dec 2022) is never saved under this period -- that
    would file the wrong month's holdings as this month's. It is appended to
    ``misfiled`` instead, so it is recorded in the discovery report and
    printed rather than silently dropped.
    """
    records: list[dict] = []
    seen_urls: set[str] = set()
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        acf = row.get("acf_fields") or {}
        mapping = acf.get("funds_mapping") or {}
        label = _clean_text(acf.get("document_name") or row.get("title") or "")
        scheme = _canonical_scheme_name(_clean_text(mapping.get("post_title") or "")) or label
        for entry in acf.get("disclosure_files") or []:
            if not isinstance(entry, dict):
                continue
            url = direct_url(entry.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            basename = unquote(Path(urlsplit(url).path).name)
            evidence = f"{label} {basename}"
            if not _row_is_for_period(month_field=acf.get("month") or "", evidence=evidence, period=period):
                if misfiled is not None:
                    misfiled.append({
                        "scheme": scheme,
                        "label": label,
                        "url": url,
                        "listed_under": period,
                        # From the label alone: a filename's trailing "_1"
                        # copy-suffix reads as a month to the period parser
                        # ("...31-Dec-2022_1" -> 2022-01), and this string is
                        # what the operator sees in the report.
                        "document_period": last_period(label) or last_period(evidence) or "unknown",
                    })
                continue
            if url in seen_urls:  # the same file listed twice
                continue
            seen_urls.add(url)
            records.append({"scheme": scheme or "bandhan", "label": label, "url": url})
    return _assign_filenames(records, period)


def _assign_filenames(records: list[dict], period: str) -> list[dict]:
    by_scheme: dict[str, list[dict]] = {}
    for record in records:
        by_scheme.setdefault(record["scheme"], []).append(record)
    used: dict[str, int] = {}
    for scheme, group in by_scheme.items():
        for record in group:
            base = _safe_filename(scheme if len(group) == 1 else f"{scheme} {record['label']}", period)
            count = used.get(base, 0)
            used[base] = count + 1
            # Still-colliding names (two documents whose labels slugify the
            # same) get a deterministic suffix rather than silently
            # overwriting each other during download.
            record["filename"] = base if not count else base.replace(".xlsx", f"_{count + 1}.xlsx")
    return records


def _available_months(payload: dict) -> list[str]:
    return [str(month) for month in (payload.get("months") or [])]


def _available_years(payload: dict) -> list[str]:
    return [str(year) for year in (payload.get("financial_years") or [])]


def _absence_reason(payload: dict, fallback_payload: dict | None, period: str) -> str:
    """Why this period has no documents, in the site's own terms."""
    year = period[:4]
    month = month_name(period)
    years = _available_years(payload) or _available_years(fallback_payload or {})
    if years and year not in years:
        return (
            f"Bandhan does not list {year} as an available year for {period} "
            f"(the site offers {', '.join(sorted(years))})"
        )
    months = _available_months(payload)
    if months and month not in months:
        return (
            f"Bandhan lists no {month} in {year} for {period} "
            f"(months published that year: {', '.join(months)})"
        )
    return f"Bandhan lists no monthly portfolio documents for {period}"


# -- report / notes ------------------------------------------------------


def _report_path(period: str) -> Path:
    config = settings()
    slug = re.sub(r"[^a-z0-9._-]+", "_", AMC.lower()).strip("_")
    # Must match run_cli's own download destination (see the "subdir"
    # argument below) so the discovery report sits next to the files it
    # describes; scripts/verify_bandhan.py reads it from there.
    directory = config.output_dir / slug / MONTHLY_SUBDIR / period
    directory.mkdir(parents=True, exist_ok=True)
    return directory / ".bandhan_discovery_report.json"


def _write_report(path: Path, report: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def build_scheme_report(payload: dict, records: list[dict]) -> dict:
    """Per-scheme found/not_published verdicts over the site's own scheme list.

    Every scheme the site offers gets an explicit answer, so a month with
    fewer files than schemes reads as "the site publishes nothing for these
    N schemes this month" rather than as a silent discovery gap.
    """
    by_scheme: dict[str, list[dict]] = {}
    for record in records:
        by_scheme.setdefault(record["scheme"], []).append(record)
    report: dict[str, dict] = {}
    for title in payload.get("scheme_titles") or []:
        canonical = _canonical_scheme_name(_clean_text(title))
        found = by_scheme.pop(canonical, None)
        report[canonical] = {"status": "found", "documents": found} if found else {"status": "not_published"}
    # A document whose scheme isn't in the dropdown list (renamed fund,
    # merged scheme) is still a real file -- record it rather than drop it.
    for scheme, found in by_scheme.items():
        report[scheme] = {"status": "found", "documents": found}
    return report


def _discovery_notes_summary(schemes_report: dict) -> dict:
    """A compact, audit-friendly digest of the per-scheme report.

    Full per-scheme detail lives in .bandhan_discovery_report.json -- this
    is what's worth surfacing directly in .expected.json alongside the
    expected file list: how many schemes the site offered in total, and
    which of them it confirmed publish nothing this period (so a smaller
    expected count than "total schemes" reads as expected, not as a bug).
    """
    status_counts: dict[str, int] = {}
    not_published: list[str] = []
    for name, entry in schemes_report.items():
        status = entry.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {"not_published", "unavailable_on_site"}:
            not_published.append(name)
    return {
        "total_schemes_offered": len(schemes_report),
        "status_counts": status_counts,
        "not_published": sorted(not_published),
    }


# -- discovery -----------------------------------------------------------


def collect_records(fetch, period: str, misfiled: list | None = None) -> tuple[dict, list[dict]]:
    """Walk every page of the listing for ``period``.

    ``fetch(period, page_number)`` returns one payload -- injected so this,
    the pagination logic, can be tested without a browser.

    Returns (first payload, records). The first payload carries the site's
    own year/month/scheme metadata, which is what makes an empty result
    explainable ("no such year" vs "no such month" vs "nothing published").
    """
    first = fetch(period, 1)
    if first.get("status") == "no_posts_found":
        return first, []
    records = records_from_payload(first, period, misfiled)
    max_pages = int(first.get("max_pages") or 1)
    for page_number in range(2, min(max_pages, _MAX_PAGES_GUARD) + 1):
        payload = fetch(period, page_number)
        if payload.get("status") == "no_posts_found":
            break
        records.extend(records_from_payload(payload, period, misfiled))
    # Two pages can repeat a row if the site re-orders between requests.
    unique: dict[str, dict] = {}
    for record in records:
        unique.setdefault(record["url"], record)
    return first, _assign_filenames(list(unique.values()), period)


def discover(period: str, session=None):
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Bandhan discovery requires Playwright; install requirements and Chromium") from exc

    config = settings()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=config.headless)
        try:
            def fetch(query_period: str | None, page_number: int) -> dict:
                if page_number > 1 and config.delay_seconds:
                    time.sleep(config.delay_seconds)  # be polite between page loads
                return _fetch_listing(browser, query_period, page_number, PlaywrightTimeoutError)

            misfiled: list[dict] = []
            first, records = collect_records(fetch, period, misfiled)
            fallback = None
            if not records and not _available_years(first):
                # The site answered "no_posts_found", which carries no
                # metadata at all -- ask it once more for its own default
                # period purely to learn which years it does publish, so
                # "2017 isn't offered" can be told apart from "something
                # went wrong".
                fallback = fetch(None, 1)
        finally:
            browser.close()

    for row in misfiled:
        print(
            f"skipped    {period} {row['label']!r}: the site lists it under {period} but the "
            f"document itself is dated {row['document_period']} -- not saved as {period}"
        )

    if not records:
        reason = _absence_reason(first, fallback, period)
        if misfiled:
            reason += (
                f"; the {len(misfiled)} row(s) it does list there are dated "
                f"{', '.join(sorted({row['document_period'] for row in misfiled}))}"
            )
        raise PeriodUnavailable(reason)

    schemes_report = build_scheme_report(first, records)
    _write_report(
        _report_path(period),
        {
            "period": period,
            "total_schemes": len(schemes_report),
            "schemes": schemes_report,
            "misfiled_rows": misfiled,
        },
    )

    documents = dedupe_documents([
        document_from_link(
            amc=AMC,
            period=period,
            source_page_url=PAGE_URL,
            link=record["url"],
            label=record["label"],
            filename=record["filename"],
            scheme=record["scheme"],
        )
        for record in records
    ])
    documents = [document for document in documents if document.period == period]
    if not documents:
        raise PeriodUnavailable(_absence_reason(first, fallback, period))
    return DiscoveryResult(documents=documents, notes=_discovery_notes_summary(schemes_report))


if __name__ == "__main__":
    raise SystemExit(
        run_cli(
            amc=AMC,
            discover=discover,
            description="Download Bandhan monthly portfolio schemes",
            subdir=MONTHLY_SUBDIR,
        )
    )

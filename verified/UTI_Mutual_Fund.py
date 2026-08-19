from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.parsing import recursive_records
from amcs._shared import string_values
from core.periods import period_conflicts, period_matches
from core.periods import month_name

AMC = "uti"
API_URL = "https://www.utimf.com/api/get-consolidate-portfolio-disclosure"
PAGE_URL = "https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure"

# UTI's live API labels this row "October 2025" but links to the November
# archive.  The correct archive is still present on UTI's own CDN.  Keep this
# exception explicit so the mismatched November file is never silently saved
# as October and the override is easy to remove if UTI repairs its catalogue.
CATALOGUE_CORRECTIONS = {
    "2025-10": "https://d3ce1o48hc5oli.cloudfront.net/s3fs-public/2025-11/fw_uti_mf_portfolios_31.10.2025_0.zip",
}

# September 2017 is listed twice.  The first archive is healthy and contains
# the dividend, futures, and portfolio workbooks.  This second legacy path now
# returns HTML and its sole workbook duplicates the one in the healthy archive.
STALE_CATALOGUE_ROWS = {
    ("2017-09", "documents/ConsolidatePortfolioDisclosure/pf-utimf-Sept 2017-091017.zip"),
}


def _is_consolidated_portfolio(url: str) -> bool:
    # URL-decode first: 2024's filenames use "SCHEME%20PORTFOLIOS" (percent-
    # encoded space) where 2025's use "scheme_portfolios" -- matching the raw
    # string missed the former since "%20" isn't in the separator class below.
    # "schemes?" (not just "scheme") because some months are plural: "UTI MF
    # SCHEMES PORTFOLIOS AS OF 29.02.2024.zip".
    # Do not require the word "scheme".  UTI has also published otherwise
    # valid consolidated archives as "UTI MF PORTFOLIOS" and, in September
    # 2025, with the typo "scheme_portfoliios".  The endpoint itself is
    # already scoped to consolidated disclosures; this check only guards
    # against an unrelated attachment accidentally appearing in a row.
    return bool(
        re.search(
            r"(?:schemes?[_% -]*)?portfol+i+os?|consolidat(?:e|ed)[_% -]*portfolio",
            unquote(url),
            re.I,
        )
    )


def _filename_conflicts(url: str, period: str) -> bool:
    """Check the archive filename, ignoring its publication-month folder.

    UTI normally uploads a month-end archive in the following month's CDN
    folder, so checking the complete URL would falsely reject valid files.
    The filename itself is useful, though: the October 2025 API row currently
    points at a November 2025 archive and must not be accepted for October.
    """
    filename = unquote(Path(urlsplit(url).path).name)
    return period_conflicts(filename, period)


def discover(period: str, session=None):
    year = period.split("-")[0]
    url = API_URL + "?" + urlencode({"year": year, "month": month_name(period)})
    payload = fetch_json(session, url, headers={"Referer": PAGE_URL, "Accept": "application/json"})
    documents = []
    for row in payload.get("rows", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        label = " ".join(str(row.get(key) or "") for key in ("name", "month", "year"))
        category = str(row.get("category") or "")
        download_url = str(row.get("url") or row.get("doc") or "")
        if (period, download_url) in STALE_CATALOGUE_ROWS:
            continue
        # Older catalogue rows use opaque names such as "pf-utimf-Oct" and
        # publish futures/dividend workbooks separately from the main ZIP.
        # The endpoint's category plus its explicit month/year columns are
        # stronger evidence than requiring every individual filename to say
        # "portfolio".  Keep all such rows: collectively they are the full
        # consolidated disclosure for that month.
        if not re.search(r"consolidat(?:e|ed)?\s*portfolio", category, re.I):
            continue
        if not download_url or not re.search(r"\.(?:xls|xlsx|zip)(?:[?#]|$)", download_url, re.I):
            continue
        if not period_matches(label, period, month_end_only=True):
            continue
        # One known API row is genuinely wrong rather than merely oddly
        # named: October 2025 links to a November-dated archive.  Reject it
        # so the verified correction below wins.
        if period in CATALOGUE_CORRECTIONS and _filename_conflicts(download_url, period):
            continue
        documents.append(
            document_from_link(
                amc=AMC,
                period=period,
                source_page_url=PAGE_URL,
                link=download_url,
                label=label,
                metadata={"category": category},
            )
        )
    for record in recursive_records(payload):
        text = json.dumps(record, ensure_ascii=False)
        if not re.search(r"consolidat(?:e|ed).*portfolio|portfolio.*consolidat", text, re.I):
            continue
        urls = [value for key, child in record.items() if key.lower() in {"url", "downloadurl", "fileurl", "documenturl", "path", "attachment"} for value in string_values(child)]
        for url in urls:
            if (period, url) in STALE_CATALOGUE_ROWS:
                continue
            if not _is_consolidated_portfolio(url) or _filename_conflicts(url, period) or not re.search(r"\.(?:xls|xlsx|zip)(?:[?#]|$)", url, re.I) or not period_matches(text + " " + url, period, month_end_only=True):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text[:240]))
    correction = CATALOGUE_CORRECTIONS.get(period)
    if correction and not documents:
        documents.append(
            document_from_link(
                amc=AMC,
                period=period,
                source_page_url=PAGE_URL,
                link=correction,
                label=f"Consolidated Portfolio {month_name(period)} {year} (catalogue correction)",
                metadata={"catalogue_correction": True},
            )
        )
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"UTI catalogue has no consolidated portfolio disclosure for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download UTI consolidated portfolio disclosures"))

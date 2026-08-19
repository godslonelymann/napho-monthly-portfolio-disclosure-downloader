from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import create_session, fetch_text
from core.parsing import extract_file_urls, extract_links
from core.periods import period_conflicts, period_matches


AMC = "nippon_india"
PAGE_URL = "https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures"


def _resolve_url(session, url: str) -> str:
    """Return whichever of ``url`` or its xls/xlsx counterpart the file host
    actually serves. Confirmed against the live site: the May 2024 monthly
    portfolio's own link is authored as "...May-24.xlsx", which 404s, while
    the real file sits at "...May-24.xls" -- a stale extension in the page's
    markup, not in the underlying document.
    """
    swapped = None
    if url.lower().endswith(".xlsx"):
        swapped = url[: -len(".xlsx")] + ".xls"
    elif url.lower().endswith(".xls"):
        swapped = url[: -len(".xls")] + ".xlsx"
    for candidate in (url, swapped):
        if not candidate:
            continue
        try:
            response = session.get(candidate, stream=True, timeout=(10, 30))
            content_type = response.headers.get("Content-Type", "")
            response.close()
            if response.status_code == 200 and "html" not in content_type.lower():
                return candidate
        except Exception:
            continue
    return url


def discover(period: str, session=None):
    active_session = session or create_session()
    html = fetch_text(active_session, PAGE_URL)
    candidates = [*extract_links(html, PAGE_URL), *[document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url) for url in extract_file_urls(html, PAGE_URL)]]
    documents = []
    for item in candidates:
        url = item.href if hasattr(item, "href") else item.url
        searchable = item.searchable if hasattr(item, "searchable") else item.evidence
        evidence = f"{searchable} {url}"
        if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I):
            continue
        if not re.search(r"monthly[\s_-]*portfolio|portfolio[\s_-]*monthly", evidence, re.I):
            continue
        if re.search(r"fortnightly", evidence, re.I):
            continue
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        url = _resolve_url(active_session, url)
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=searchable))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Nippon India page has no monthly portfolio workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Nippon India monthly portfolio"))

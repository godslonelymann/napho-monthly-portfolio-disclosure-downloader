from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_file_urls, extract_links, next_payload_text
from core.periods import period_conflicts, period_matches


AMC = "360_one"
PAGE_URL = "https://www.360.one/asset/mutual-funds/downloads/"


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    payload = next_payload_text(html)
    candidates = [*extract_links(payload, PAGE_URL), *extract_links(html, PAGE_URL)]
    urls = [*extract_file_urls(payload, PAGE_URL), *extract_file_urls(html, PAGE_URL)]
    documents = []
    for link in candidates:
        urls.append(link.href)
    for url in dict.fromkeys(urls):
        if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I):
            continue
        if not re.search(r"monthly[_ -]?portfolio", url, re.I):
            continue
        if period_conflicts(url, period) or not period_matches(url, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"360 ONE RSC payload has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download 360 ONE monthly portfolios"))

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_file_urls, extract_links
from core.periods import extract_periods


AMC = "ilfs_idf"
PAGE_URL = "http://www.ilfsinfrafund.com/other.php"


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    candidates = [*extract_links(html, PAGE_URL), *[document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url) for url in extract_file_urls(html, PAGE_URL)]]
    documents = []
    for item in candidates:
        url = item.href if hasattr(item, "href") else item.url
        searchable = item.searchable if hasattr(item, "searchable") else item.evidence
        evidence = f"{searchable} {url}"
        filename = unquote(Path(urlsplit(url).path).name)
        if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I) or "/otherfile/" not in url.lower():
            continue
        if re.search(r"transaction", filename, re.I):
            continue
        if not re.search(r"dashboard|portfolio.*monthly|monthly.*portfolio", filename, re.I):
            continue
        if extract_periods(filename) != {period}:
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=searchable, scheme="combined", primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"IL&FS page has no combined monthly report for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download IL&FS combined monthly portfolio report"))

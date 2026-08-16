from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import PeriodUnavailable, document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_file_urls, extract_links
from core.periods import current_period, resolve_as_of_period


AMC = "hsbc"
PAGE_URL = "https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library"
EXCLUDED_PATHS = ("fortnightly-debt-portfolio", "weekly-fund-portfolios", "half-yearly-portfolios")


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    candidates: list[tuple[str, str]] = [(link.href, link.text) for link in extract_links(html, PAGE_URL)]
    candidates += [(url, "") for url in extract_file_urls(html, PAGE_URL)]

    before = current_period()
    documents = []
    for url, text in candidates:
        lowered = url.lower()
        if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I):
            continue
        if any(part in lowered for part in EXCLUDED_PATHS):
            continue
        searchable = f"{text} {url}"
        if "document-" not in lowered and "documents-" not in lowered and "monthly-portfolio" not in searchable.lower():
            continue
        # HSBC's folder name is a publish date, not necessarily the as-of
        # date -- see resolve_as_of_period's docstring for why filename and
        # link text are each tried, in order, before the folder is trusted.
        path_parts = urlsplit(url).path.split("/")
        name = path_parts[-1] if path_parts else ""
        folder = path_parts[-2] if len(path_parts) >= 2 else ""
        if resolve_as_of_period(name, text, folder, before=before) != period:
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text or None))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise PeriodUnavailable(f"HSBC page has no monthly portfolio workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download HSBC monthly portfolio schemes"))

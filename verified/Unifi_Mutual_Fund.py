from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import period_matches

AMC = "unifi"
PAGE_URL = "https://unifimf.com/statutory/"


def discover(period: str, session=None):
    documents = []
    for link in extract_links(fetch_text(session, PAGE_URL), PAGE_URL):
        searchable = link.searchable
        filename = Path(urlsplit(link.href).path).name
        monthly_file = filename.lower().startswith("mp-")
        if not (monthly_file or "monthly-portfolio-disclosure" in searchable.lower()) or re.search(r"notice|fortnightly|weekly|\.pdf(?:[?#]|$)", searchable, re.I):
            continue
        if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", link.href, re.I) or not period_matches(searchable, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link))
    if not documents:
        raise RuntimeError(f"Unifi monthly-portfolio-disclosure has no workbook for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Unifi monthly portfolio disclosures"))

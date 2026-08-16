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
from core.periods import period_conflicts, period_matches

AMC = "helios"
PAGE_URL = "https://www.heliosmf.in/portfolio-disclosure"


def discover(period: str, session=None):
    documents = []
    for link in extract_links(fetch_text(session, PAGE_URL), PAGE_URL):
        if "monthly-portfolio" not in link.href.lower() or not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", link.href, re.I):
            continue
        filename = Path(urlsplit(link.href).path).name
        if not period_conflicts(filename, period) and period_matches(link.searchable, period, month_end_only=True):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link))
    if not documents:
        raise RuntimeError(f"Helios has no direct monthly workbook for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Helios monthly portfolio disclosures"))

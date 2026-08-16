from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import period_matches

AMC = "dsp"
PAGE_URL = "https://www.dspim.com/mandatory-disclosures/portfolio-disclosures"


def discover(period: str, session=None):
    documents = []
    for link in extract_links(fetch_text(session, PAGE_URL), PAGE_URL):
        if not re.search(r"portfolio\s+details\s+as\s+on", link.searchable, re.I):
            continue
        if not re.search(r"\.(?:zip|xls|xlsx)(?:[?#]|$)", link.href, re.I):
            continue
        if period_matches(link.searchable, period, month_end_only=True):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, scheme="consolidated", primary=True))
    if not documents:
        raise RuntimeError(f"DSP has no month-end portfolio details for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download DSP month-end portfolio ZIPs"))

from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import post_text
from core.parsing import extract_links
from core.periods import period_conflicts, period_matches

AMC = "lic"
PAGE_URL = "https://www.licmf.com/downloads/monthly-portfolio"
API_URL = "https://www.licmf.com/downloads/consolidated-portfolio-files"
CATEGORY_ID = "639"  # "639" is "Monthly Portfolio"; the sibling "638" is Fortnightly.
HEADERS = {"Referer": PAGE_URL, "X-Requested-With": "XMLHttpRequest"}


def discover(period: str, session=None):
    year, month = period.split("-")
    body = post_text(session, API_URL, data={"id": CATEGORY_ID, "year": year, "month": str(int(month))}, headers=HEADERS)
    documents = []
    for link in extract_links(body, PAGE_URL):
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", link.href, re.I):
            continue
        evidence = link.searchable
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"LIC consolidated-portfolio-files has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download LIC consolidated monthly portfolio disclosure"))

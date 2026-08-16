from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import period_matches
from amcs._shared import docs_from_json_records, next_data

AMC = "angel_one"
PAGE_URL = "https://www.angelonemf.com/downloads"


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    documents = []
    for link in extract_links(html, PAGE_URL):
        if not re.search(r"monthly\s+portfolio", link.searchable, re.I):
            continue
        if not re.search(r"\.(?:xls|xlsx|xlsm)(?:[?#]|$)", link.href, re.I):
            continue
        if period_matches(link.searchable, period):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link))
    if not documents:
        try:
            documents = docs_from_json_records(next_data(html), amc=AMC, period=period, page_url=PAGE_URL, predicate=lambda record, label, url: bool(re.search(r"monthly\s+portfolio", f"{label} {record}", re.I)))
        except RuntimeError:
            documents = []
    if not documents:
        raise RuntimeError(f"Angel One page data contains no monthly portfolio for {period}")
    return only_period(dedupe_documents(documents), period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Angel One monthly portfolio disclosures"))

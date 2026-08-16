from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.periods import period_matches
from core.parsing import extract_links, recursive_records
from amcs._shared import parse_jsonish, string_values


AMC = "bank_of_india"
PAGE_URL = "https://www.boimf.in/investor-corner#t2"
API_URL = "https://www.boimf.in/AjaxService.asmx/GetDocuments"


def discover(period: str, session=None):
    payload = {
        "pagno": 0,
        "category": None,
        "fromDate": None,
        "toDate": None,
        "LibraryName": "InvestorCorner",
        "folderName": "MONTHLY PORTFOLIO",
        "CategoryValue": "no",
    }
    response = session.post(API_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=getattr(session, "default_timeout", (30, 120)))
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError:
        body = parse_jsonish(response.text)
    # The ASMX endpoint wraps its real payload as a JSON-encoded string under
    # "d" (classic ASP.NET AJAX convention) instead of returning the document
    # list directly, so the list of documents itself is invisible until that
    # string is decoded one more level.
    if isinstance(body, dict) and isinstance(body.get("d"), str):
        body = parse_jsonish(body["d"])
    documents = []
    for group in recursive_records(body):
        # Match text one document at a time, not the enclosing {"Documents":
        # [...], "Length": 345} container -- that container's own combined
        # text mentions every month ever published, so matching against it
        # would loosely "match" the requested period and pull in all 345
        # entries instead of just the one that's actually for this period.
        entries = group.get("Documents")
        if not isinstance(entries, list):
            continue
        for record in entries:
            if not isinstance(record, dict):
                continue
            text = json.dumps(record, ensure_ascii=False)
            for url in string_values(record):
                if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", url, re.I) or not period_matches(f"{text} {url}", period, month_end_only=True):
                    continue
                documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text[:500], primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Bank of India API has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Bank of India monthly portfolio"))

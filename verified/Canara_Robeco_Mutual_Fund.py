from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlencode

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import period_matches

AMC = "canara_robeco"
BASE_URL = "https://www.canararobeco.com/documents/statutory-disclosures/scheme-dashboard/scheme-monthly-portfolio/"


def discover(period: str, session=None):
    year, month = period.split("-")
    documents = []
    for page in range(1, 101):
        page_url = BASE_URL + "?" + urlencode({"filteryear": year, "filtermonth": month, "pagination": page})
        html = fetch_text(session, page_url)
        page_documents = []
        for link in extract_links(html, page_url):
            if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", link.href, re.I) or not period_matches(link.searchable, period):
                continue
            page_documents.append(document_from_link(amc=AMC, period=period, source_page_url=BASE_URL, link=link))
        before = len(documents)
        documents = dedupe_documents([*documents, *page_documents])
        if not page_documents or len(documents) == before:
            break
    else:
        raise RuntimeError("Canara Robeco pagination exceeded 100 pages")
    if not documents:
        raise RuntimeError(f"Canara Robeco returned no schemes for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download all Canara Robeco monthly portfolio schemes"))

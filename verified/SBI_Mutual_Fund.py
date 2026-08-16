from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import settings
from core.discovery import document_from_link, dedupe_documents, only_period
from core.parsing import extract_links, extract_file_urls
from core.periods import month_name, period_matches


AMC = "sbi"
PAGE_URL = "https://www.sbimf.com/portfolios"
API_URL = "https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets"


def discover(period: str, session=None):
    response = session.post(
        API_URL,
        json={"FundId": 0, "PSYear": period[:4], "PSMonth": month_name(period), "PSFrequency": "Monthly"},
        headers={"Content-Type": "application/json;charset=utf-8", "Referer": PAGE_URL},
        timeout=getattr(session, "default_timeout", (30, 120)),
    )
    response.raise_for_status()
    try:
        payload = response.json()
        body = payload.get("d", "") if isinstance(payload, dict) else str(payload)
    except ValueError:
        body = response.text
    if not isinstance(body, str):
        body = str(body)
    documents = []
    for link in extract_links(body, PAGE_URL):
        evidence = link.searchable
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", link.href, re.I) or re.search(r"fortnightly|half[- ]?year", evidence, re.I):
            continue
        if not period_matches(evidence + " " + link.href, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, label=evidence, primary="all-schemes" in evidence.lower()))
    for url in extract_file_urls(body, PAGE_URL):
        if re.search(r"fortnightly|half[- ]?year", url, re.I) or not period_matches(url, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, primary="all-schemes" in url.lower()))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"SBI monthly endpoint returned no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download SBI monthly portfolio workbooks"))

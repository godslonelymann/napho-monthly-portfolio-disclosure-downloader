from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_file_urls, extract_links
from core.periods import period_conflicts, period_matches


AMC = "sundaram"
PAGE_URL = "https://www.sundarammutual.com/Monthly-Fortnightly-Adhoc-Portfolios"


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    match = re.search(r"(/ajax/Modules_Disclosure_Monthly_Fortnightly_Adhoc_Portfolios,App_Web_[a-z0-9]+\.ashx)", html, re.I)
    if not match:
        raise RuntimeError("Sundaram page did not expose its rotating AjaxPro endpoint")
    endpoint = urljoin(PAGE_URL, match.group(1)) + "?_method=GetCategory&_session=no"
    response = session.post(endpoint, data="Catid=Monthly", headers={"Content-Type": "text/plain; charset=UTF-8", "Referer": PAGE_URL}, timeout=getattr(session, "default_timeout", (30, 120)))
    response.raise_for_status()
    try:
        payload = response.json()
        body = " ".join(str(value) for value in payload.values()) if isinstance(payload, dict) else str(payload)
    except ValueError:
        body = response.text
    # The .ashx fragment escapes its own HTML attribute quotes (href='...')
    # as \' even though the response is not JSON, so hrefs come back as
    # literal "\'/uploaddir/...\'" unless unescaped first.
    body = body.replace("\\'", "'")
    documents = []
    for link in extract_links(body, PAGE_URL):
        evidence = link.searchable
        # "Fixed Income" is a second, legitimate monthly workbook (alongside
        # "Equity & Fund of Funds") -- only fortnightly/ad-hoc categories
        # should be excluded here.
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", link.href, re.I) or re.search(r"fortnightly|adhoc", evidence, re.I):
            continue
        if period_conflicts(evidence + " " + link.href, period) or not period_matches(evidence + " " + link.href, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, label=evidence, primary=True))
    for url in extract_file_urls(body, PAGE_URL):
        if not period_conflicts(url, period) and period_matches(url, period, month_end_only=True):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Sundaram Monthly AjaxPro response has no workbooks for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Sundaram monthly portfolio workbooks"))

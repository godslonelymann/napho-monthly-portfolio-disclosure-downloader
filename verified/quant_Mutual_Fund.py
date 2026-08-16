from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import extract_periods, month_name, period_matches
from amcs._shared import parse_jsonish

AMC = "quant"
PAGE_URL = "https://quantmutual.com/statutorydisclosures.aspx"
AGGREGATE_URL = PAGE_URL + "/displaydisclouser"
FUND_MONTHS_URL = PAGE_URL + "/displaydisclouser1"
FUND_FILES_URL = PAGE_URL + "/displaydisclouser2"


def _post_json(session, url: str, payload: dict[str, str]):
    response = session.post(url, json=payload, headers={"Referer": PAGE_URL, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}, timeout=getattr(session, "default_timeout", (30, 120)))
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Quant endpoint returned non-JSON: {url}") from exc
    body = result.get("d") if isinstance(result, dict) else result
    return body if isinstance(body, str) else json.dumps(body)


def _docs_from_html(body: str, period: str, *, primary: bool):
    documents = []
    for link in extract_links(body, PAGE_URL):
        if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", link.href, re.I):
            continue
        evidence = link.searchable
        fallback = f"{evidence} {month_name(period)} {period.split('-')[0]}"
        if period_matches(evidence, period) or (not extract_periods(evidence) and period_matches(fallback, period)):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, primary=primary))
    return documents


def discover(period: str, session=None):
    year, month = period.split("-")
    documents = []
    errors = []
    try:
        documents.extend(_docs_from_html(_post_json(session, AGGREGATE_URL, {"id": year, "cat": "MONTHLY PORTFOLIO"}), period, primary=True))
    except Exception as exc:
        errors.append(f"aggregate: {exc}")
    try:
        _post_json(session, FUND_MONTHS_URL, {"id": year, "cat": "MONTHLY PORTFOLIO - FUND - WISE"})
        body = _post_json(session, FUND_FILES_URL, {"id": str(int(month)), "cat": "MONTHLY PORTFOLIO - FUND - WISE", "tab": year})
        documents.extend(_docs_from_html(body, period, primary=False))
    except Exception as exc:
        errors.append(f"fund-wise: {exc}")
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Quant returned no consolidated/fund-wise files for {period}; {' | '.join(errors)}")
    return documents


if __name__ == "__main__":
    raise SystemExit(
        run_cli(
            amc=AMC,
            discover=discover,
            description="Download Quant consolidated and fund-wise monthly portfolios",
            # Plain HTTP calls, no browser -- cheap enough to re-run once if
            # a file comes up missing, to tell "our download failed" apart
            # from "the site stopped listing it mid-run".
            rediscoverable=True,
        )
    )


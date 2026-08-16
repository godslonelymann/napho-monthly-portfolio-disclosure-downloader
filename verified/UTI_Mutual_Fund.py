from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.parsing import recursive_records
from amcs._shared import string_values
from core.periods import period_conflicts, period_matches
from core.periods import month_name

AMC = "uti"
API_URL = "https://www.utimf.com/api/get-consolidate-portfolio-disclosure"
PAGE_URL = "https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure"


def _is_consolidated_portfolio(url: str) -> bool:
    return bool(re.search(r"scheme[_% -]*portfolios?|consolidat(?:e|ed)[_% -]*portfolio", url, re.I))


def discover(period: str, session=None):
    year = period.split("-")[0]
    url = API_URL + "?" + urlencode({"year": year, "month": month_name(period)})
    payload = fetch_json(session, url, headers={"Referer": PAGE_URL, "Accept": "application/json"})
    documents = []
    for row in payload.get("rows", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        label = " ".join(str(row.get(key) or "") for key in ("name", "month", "year"))
        download_url = str(row.get("url") or row.get("doc") or "")
        if not _is_consolidated_portfolio(download_url) or not re.search(r"\.(?:xls|xlsx|zip)(?:[?#]|$)", download_url, re.I) or not period_matches(label + " " + download_url, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=download_url, label=label, scheme="consolidated", primary=True))
    for record in recursive_records(payload):
        text = json.dumps(record, ensure_ascii=False)
        if not re.search(r"consolidat(?:e|ed).*portfolio|portfolio.*consolidat", text, re.I):
            continue
        urls = [value for key, child in record.items() if key.lower() in {"url", "downloadurl", "fileurl", "documenturl", "path", "attachment"} for value in string_values(child)]
        for url in urls:
            if not _is_consolidated_portfolio(url) or period_conflicts(url, period) or not re.search(r"\.(?:xls|xlsx|zip)(?:[?#]|$)", url, re.I) or not period_matches(text + " " + url, period, month_end_only=True):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text[:240], scheme="consolidated", primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"UTI catalogue has no consolidated portfolio disclosure for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download UTI consolidated portfolio disclosures"))

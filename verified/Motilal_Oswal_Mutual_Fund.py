from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.parsing import recursive_records
from amcs._shared import string_values
from core.periods import extract_periods

AMC = "motilal_oswal"
API_URL = "https://www.motilaloswalmf.com/content/aem-cloud-dept-backend-motilal-oswal/api/search-documents.json"
PAGE_URL = "https://www.motilaloswalmf.com/downloads"


def discover(period: str, session=None):
    payload = fetch_json(session, API_URL + "?" + urlencode({"year": "", "category": "month end portfolio", "month": "", "type": "mf"}), headers={"Referer": PAGE_URL, "Accept": "application/json"})
    documents = []
    for record in recursive_records(payload):
        text = json.dumps(record, ensure_ascii=False)
        title = str(record.get("title") or record.get("name") or record.get("label") or "")
        if not re.search(r"month\s*end\s*portfolio", text, re.I):
            continue
        urls = [value for key, child in record.items() if key.lower() in {"url", "downloadurl", "fileurl", "path", "documenturl"} for value in string_values(child)]
        for url in urls:
            filename = unquote(Path(urlsplit(url).path).name)
            if re.search(r"forth?nightly|weekly|half[- ]?year", filename, re.I):
                continue
            # The title is authoritative when a published filename is malformed
            # (June 2026 is currently named ``June 20261.xlsx``).
            if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I) or period not in extract_periods(title):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text[:240]))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Motilal Oswal search-documents returned no month-end portfolio for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Motilal Oswal month-end portfolios"))

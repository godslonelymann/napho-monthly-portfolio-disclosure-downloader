from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import post_json
from core.periods import period_conflicts, period_matches

AMC = "trust"
PAGE_URL = "https://www.trustmf.com/disclosures?activeTab=portfolio-disclosures"
API_URL = "https://www.trustmf.com/api/api/Trust/GetData"
PAYLOAD = {
    "systemQueryFileName": "disclosuresweb.xml",
    "tagName": "GetDisclosureByType",
    "searchField": "",
    "searchValue": "",
    "sortField": "uploaddate",
    "sortDirection": "DESC",
    "replaceField": "_slug_",
    "replaceValue": "portfolio-monthly-disclosure",
}
# The site's WAF returns 406 for the project's default User-Agent (it carries
# a "portfolio-downloader/1.0" product token); a plain browser UA passes. Set
# it on the session itself, not just this call, so the later file download
# (which reuses this same session) doesn't get 406'd too.
HEADERS = {
    "Referer": PAGE_URL,
    "Accept": "*/*",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def discover(period: str, session=None):
    if session is not None:
        session.headers.update(HEADERS)
    payload = post_json(session, API_URL, json=PAYLOAD, headers=HEADERS)
    documents = []
    for record in payload.get("resultSetArray") or []:
        if "portfolio-monthly-disclosure" not in record.get("matching_slugs", "").split(","):
            continue
        url = record.get("fileurl")
        title = record.get("title", "")
        if not url or not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", url, re.I):
            continue
        evidence = f"{title} {record.get('slug', '')}"
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=title, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Trust disclosure API has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Trust monthly portfolio disclosure"))

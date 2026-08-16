from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urljoin

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import post_json
from core.periods import period_conflicts, period_matches

AMC = "mirae"
PAGE_URL = "https://www.miraeassetmf.co.in/downloads/portfolio"
API_URL = "https://www.miraeassetmf.co.in/AjaxService/GetDownloadsData"
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json;charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": PAGE_URL,
}
PAGE_SIZE = 100


def discover(period: str, session=None):
    documents: list = []
    page = 1
    while True:
        payload = post_json(
            session,
            API_URL,
            json={"request": {"modulename": "portfolio_tab1", "pgno": page, "pgsize": PAGE_SIZE}},
            headers=HEADERS,
        )
        if payload.get("ReturnCode") != "0":
            raise RuntimeError(f"Mirae downloads API rejected the request: {payload.get('ReturnMsg')}")
        records = payload.get("Data") or []
        page_matches = 0
        for record in records:
            url = record.get("URL")
            title = record.get("Title", "")
            if not url or period_conflicts(title, period) or not period_matches(title, period):
                continue
            page_matches += 1
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=urljoin(PAGE_URL, url), label=title, primary=True))
        # The listing is sorted newest-first (verified by inspecting the raw
        # feed), so once a full page turns up no matches after we've already
        # collected some, every later page is older still -- stop paging
        # instead of walking all 3,600+ records for one month.
        if documents and page_matches == 0:
            break
        total = payload.get("DataCount", 0)
        if not records or page * PAGE_SIZE >= total:
            break
        page += 1
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Mirae downloads API has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Mirae Asset monthly portfolio disclosures"))

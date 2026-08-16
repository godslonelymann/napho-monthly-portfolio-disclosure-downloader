from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import post_json
from core.periods import period_conflicts, period_matches

AMC = "icici_prudential"
PAGE_URL = "https://www.icicipruamc.com/media-center/downloads?currentTabFilter=Disclosures&&subCatTabFilter=MonthlyPortfolioDisclosures"
API_URL = "https://apimf.icicipruamc.com/nms/v1/downloads/files"
BLOB_BASE = "https://www.icicipruamc.com/blob"
CATEGORY_ID = "26a073d7-08d2-4a95-95fa-f83a4ee51e40"
# The gateway serves an HTML error page instead of JSON when Accept
# prioritises text/html (this project's default session Accept header does),
# even though the request otherwise succeeds -- override it per-call.
HEADERS = {"Referer": "https://www.icicipruamc.com/", "env": "api", "Accept": "application/json"}


def discover(period: str, session=None):
    documents = []
    page = 1
    while True:
        payload = post_json(
            session,
            API_URL,
            json={
                "categoryId": CATEGORY_ID,
                "schemeCategory": "",
                "userType": "Investor",
                "fileType": "All",
                "page": str(page),
                "size": "100",
                "filter": [],
                "categoryName": "OTHERS",
            },
            headers=HEADERS,
        )
        data = payload.get("success", {}).get("data", {})
        for record in data.get("files", []):
            url = record.get("url")
            title = record.get("title", {}).get("text", "")
            if not url or "Monthly Portfolio Disclosure" not in title:
                continue
            if period_conflicts(title, period) or not period_matches(title, period):
                continue
            download_url = BLOB_BASE + quote(url, safe="/")
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=download_url, label=title, primary=True))
        if not data.get("isNext"):
            break
        page += 1
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"ICICI Prudential downloads catalog has no monthly disclosure for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download ICICI Prudential monthly portfolio disclosure"))

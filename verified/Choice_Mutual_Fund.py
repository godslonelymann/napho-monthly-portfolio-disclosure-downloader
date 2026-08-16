from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urljoin

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import post_json

AMC = "choice"
PAGE_URL = "https://choicemf.com/statutory-disclosure"
API_URL = "https://choicemf.com/api/monthly-portfolio-report/portfolio-website-list"
DOCUMENT_BASE = "https://doc.choicemf.com/"


def discover(period: str, session=None):
    payload = post_json(session, API_URL, json={})
    documents = []
    for scheme in payload.get("body", {}).get("data", []):
        scheme_name = scheme.get("scheme_name", "")
        for report in scheme.get("reports", []):
            report_date = report.get("report_date", "")
            if not report_date.startswith(period):
                continue
            url = urljoin(DOCUMENT_BASE, report["file_path"])
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=f"{scheme_name} {report_date}", scheme=scheme_name, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Choice portfolio-website-list has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Choice monthly portfolio disclosure"))

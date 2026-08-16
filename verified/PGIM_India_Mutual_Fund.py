from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import post_json
from core.periods import period_conflicts, period_matches

AMC = "pgim"
PAGE_URL = "https://www.pgimindia.com/mutual-funds/disclosures/Portfolios/Monthly-Portfolio"
API_URL = "https://www.pgimindia.com/api/v1/brochure/published/disclosure"
PAYLOAD = {"headerId": 2, "sectionId": "SECTION_747960037", "source": "W", "branchCode": None}


def discover(period: str, session=None):
    payload = post_json(session, API_URL, json=PAYLOAD)
    documents = []
    for group in payload.get("data", []):
        for record in group.get("content", []):
            url = record.get("pdfPath")
            if not url:
                continue
            # The record's own "month"/"year" fields are reliable here (unlike
            # ITI/Trust, where those track the upload date) but dateMonthYear
            # and the filename both carry the same disclosure date, so use all
            # three as corroborating evidence rather than trusting one field.
            evidence = " ".join(str(record.get(key, "")) for key in ("title", "dateMonthYear", "pdfPath"))
            if period_conflicts(evidence, period) or not period_matches(evidence, period):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=str(record.get("title", "")), primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"PGIM disclosure manifest has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download PGIM India monthly portfolio disclosures"))

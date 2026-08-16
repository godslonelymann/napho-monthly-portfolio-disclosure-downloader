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
from core.parsing import extract_json_script, recursive_records
from core.periods import period_conflicts, period_matches

AMC = "zerodha"
PAGE_URL = "https://www.zerodhafundhouse.com/resources/disclosures?source=footer"


def discover(period: str, session=None):
    payload = extract_json_script(fetch_text(session, PAGE_URL), script_id="__NEXT_DATA__")
    documents = []
    for record in recursive_records(payload):
        if str(record.get("id", "")).lower() not in {"monthly-portfolio-disclosures", "monthly_portfolio_disclosures"} and not ("files" in record and "monthly" in json.dumps(record).lower()):
            continue
        text = json.dumps(record, ensure_ascii=False)
        for child in record.get("files", []) if isinstance(record.get("files"), list) else [record]:
            if not isinstance(child, dict):
                continue
            name = str(child.get("name") or child.get("title") or "")
            url = str(child.get("url") or child.get("downloadUrl") or "")
            if (
                period_conflicts(url, period)
                or "portfolio-disclosures" not in url.lower()
                or not re.search(r"\bmonthly\s+portfolio\b", name, re.I)
                or not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I)
                or not period_matches(f"{name} {text} {url}", period, month_end_only=True)
            ):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=name or url))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Zerodha __NEXT_DATA__ has no monthly portfolio file for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Zerodha Fund House monthly portfolios"))


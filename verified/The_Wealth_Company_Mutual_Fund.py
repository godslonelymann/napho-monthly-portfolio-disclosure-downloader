from __future__ import annotations

import html as html_module
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
from core.periods import extract_periods, period_matches

AMC = "wealth_company"
BASE_URL = "https://www.wealthcompanyamc.in/literature-forms/portfolio-documents/monthly/"


def _download_records(html: str) -> list[dict]:
    """Decode each streamed ``downloads`` array without mixing records."""
    text = html_module.unescape(html).replace('\\"', '"').replace("\\/", "/").replace("\\u002F", "/")
    decoder = json.JSONDecoder()
    records: list[dict] = []
    marker = '"downloads":'
    offset = 0
    while (start := text.find(marker, offset)) >= 0:
        start += len(marker)
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            offset = start
            continue
        if isinstance(value, list):
            records.extend(record for record in value if isinstance(record, dict))
        offset = end
    return records


def discover(period: str, session=None):
    documents = []
    for page in range(1, 101):
        page_url = BASE_URL + f"?page={page}"
        html = fetch_text(session, page_url)
        records = _download_records(html)
        if not records:
            break
        for record in records:
            upload_date = str(record.get("uploadDate") or "")
            name = str(record.get("name") or "")
            attachment = record.get("attachment")
            url = attachment.get("url") if isinstance(attachment, dict) else None
            if not isinstance(url, str) or not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", url, re.I):
                continue
            if not (upload_date.startswith(period) or period_matches(name, period, month_end_only=True)):
                continue
            absolute = urljoin(page_url, url)
            if extract_periods(name) and not period_matches(name, period, month_end_only=True):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=BASE_URL, link=absolute, label=name, metadata={"uploadDate": upload_date}))
    else:
        raise RuntimeError("Wealth Company pagination exceeded 100 pages")
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Wealth Company Flight records contain no attachment for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Wealth Company paginated monthly portfolios"))

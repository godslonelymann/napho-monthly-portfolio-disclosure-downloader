from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_file_urls
from core.periods import period_conflicts, period_matches


AMC = "union"
PAGE_URL = "https://www.unionmf.com/about-us/downloads/monthly-portfolio"
PUSH_RE = re.compile(r"downloadMonthPortfolio\.push\(\s*\{(.*?)\}\s*\)", re.I | re.S)
VALUE_RE = re.compile(r"(?:Title|title|Url|url)\s*:\s*(['\"])((?:\\.|(?!\1).)*)\1", re.S)


def _records(markup: str):
    for match in PUSH_RE.finditer(markup):
        values = {}
        for field_match in VALUE_RE.finditer(match.group(1)):
            value = field_match.group(2).replace("\\'", "'").replace('\\"', '"')
            key = field_match.group(0).split(":", 1)[0].strip().lower()
            values[key] = value
        title = values.get("title", "")
        url = values.get("url", "")
        if url:
            yield title, url


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    documents = []
    for title, url in _records(html):
        absolute = urljoin(PAGE_URL, url)
        evidence = f"{title} {absolute}"
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", absolute, re.I) or re.search(r"fortnightly|weekly|half[- ]?year", evidence, re.I):
            continue
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=absolute, label=title))
    for url in extract_file_urls(html, PAGE_URL):
        if not period_conflicts(url, period) and period_matches(url, period):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Union page has no monthly portfolio workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Union monthly portfolio schemes"))

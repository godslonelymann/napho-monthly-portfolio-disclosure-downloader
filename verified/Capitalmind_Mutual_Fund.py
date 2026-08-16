from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import extract_periods

AMC = "capitalmind"
PAGE_URL = "https://capitalmindmf.com/statutory-disclosures.html"


def _file_periods(url: str) -> set[str]:
    name = unquote(Path(urlsplit(url).path).name)
    # Strapi appends a hex content hash.  Without removing it, a hash that
    # starts with 01..12 can be misread as a second month after the year.
    name = re.sub(r"_[0-9a-f]{8,}(?=\.(?:xls|xlsx)$)", "", name, flags=re.I)
    return extract_periods(name)


def discover(period: str, session=None):
    documents = []
    for link in extract_links(fetch_text(session, PAGE_URL), PAGE_URL):
        if "monthly_portfolio" not in link.href.lower() or not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", link.href, re.I):
            continue
        if _file_periods(link.href) == {period}:
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link))
    if not documents:
        raise RuntimeError(f"Capitalmind has no hashed Monthly_Portfolio workbook for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Capitalmind monthly portfolio disclosures"))

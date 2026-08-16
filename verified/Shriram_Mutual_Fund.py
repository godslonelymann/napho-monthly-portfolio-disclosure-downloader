from __future__ import annotations

import os
import re
import sys
import html as html_module
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import dedupe_documents, document_from_link, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import period_matches
from amcs._shared import fetch_first_html

AMC = "shriram"
PAGE_ALIASES = (
    os.getenv("SHRIRAM_PAGE_URL", "https://www.shriramamc.in/investor-statutory-disclosures"),
    "https://www.shriramamc.in/statutory-disclosures",
    "https://www.shriramamc.in/",
)


def discover(period: str, session=None):
    html, page_url = fetch_first_html(session, PAGE_ALIASES)
    documents = []
    for link in extract_links(html, page_url):
        searchable = link.searchable
        if "monthly-portfolio" not in searchable.lower() or re.search(r"fortnightly|weekly|_rsc=", searchable, re.I):
            continue
        if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", link.href, re.I) or not period_matches(searchable, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=page_url, link=link))
    # The Next.js redesign stores disclosures in serialized page data rather
    # than anchor elements.  Extract only URLs from the monthly-portfolio tree.
    serialized = html_module.unescape(html).replace("\\/", "/").replace("\\u002F", "/")
    # "xlsx" must precede "xls" in the alternation: with the lazy prefix and no
    # end anchor, "xls" matches first and silently truncates a .xlsx link to
    # .xls, which the CDN answers with an S3 AccessDenied (403).
    for url in re.findall(r'https?://[^"<>\\ ]+?\.(?:xlsx|xls)(?:\?[^"<>\\ ]*)?', serialized, re.I):
        if "/monthly-" not in url.lower() or "portfolio" not in url.lower():
            continue
        filename = Path(urlsplit(url).path).name
        if re.search(r"fortnightly|weekly", filename, re.I) or not period_matches(url, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=page_url, link=url))
    if not documents:
        raise RuntimeError(f"Shriram page has no monthly CDN workbook for {period}")
    # The same workbook is listed both as an anchor and in the serialized page
    # data, so dedupe before returning to avoid downloading it twice.
    return only_period(dedupe_documents(documents), period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Shriram monthly CDN portfolios"))

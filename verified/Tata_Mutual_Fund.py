from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.periods import period_conflicts, period_matches

AMC = "tata"
PAGE_URL = "https://www.tatamutualfund.com/schemes-related/portfolio"
# The page is a Next.js RSC stream whose JSON is escaped a second time
# (backslash-quote), so this matches the doubly-escaped field pairs
# directly rather than decoding the stream first.
RECORD_RE = re.compile(
    r'field_document_title\\":\\"(?P<title>[^\\"]*)\\",'
    r'\\"field_media_document\\":\\"(?P<url>https?://[^\\"]+\.(?:pdf|xlsx?)(?:\?[^\\"]*)?)\\"',
    re.DOTALL,
)


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    documents = []
    for match in RECORD_RE.finditer(html):
        title = match.group("title").replace(r"\/", "/")
        url = match.group("url").replace(r"\/", "/")
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", url, re.I):
            continue
        # Match on the title alone, not the URL: the CMS uploads each file
        # into a folder named for the *upload* month (system/files/2026-07/
        # for a June disclosure posted in July), and that folder segment is
        # itself a valid ISO year-month that would otherwise satisfy the
        # period match for the wrong month.
        if period_conflicts(title, period) or not period_matches(title, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=title, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Tata portfolio archive has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Tata monthly portfolio disclosure"))

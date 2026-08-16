from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_js_json
from core.periods import period_matches

AMC = "abakkus"
PAGE_URL = "https://www.abakkusmf.com/statutory-disclosures.html"

# The disclosures page renders every category (monthly portfolio, fortnightly,
# half-yearly, proxy voting, ...) from a single client-side data structure
# assigned to a bare JS variable in an inline <script> tag -- there is no
# server-rendered HTML section or heading to scrape any more, and no JSON
# script tag (like __NEXT_DATA__) either.
_VERTICALS_VAR = "verticals"
_MONTHLY_SUBTITLE = "monthly portfolio disclosure"


def _monthly_portfolio_items(html: str) -> list[dict]:
    verticals = extract_js_json(html, _VERTICALS_VAR)
    if not isinstance(verticals, list):
        raise RuntimeError(f"Abakkus page's {_VERTICALS_VAR!r} data is not a list; page structure changed")
    group = next(
        (
            vertical
            for vertical in verticals
            if isinstance(vertical, dict) and str(vertical.get("subTitle") or "").strip().lower() == _MONTHLY_SUBTITLE
        ),
        None,
    )
    if group is None:
        raise RuntimeError("Abakkus page no longer has a 'Monthly Portfolio Disclosure' section; page structure changed")
    items = []
    for section in group.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for sub_section in section.get("subSections") or []:
            if not isinstance(sub_section, dict):
                continue
            for item in sub_section.get("items") or []:
                if isinstance(item, dict):
                    items.append(item)
    return items


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    documents = []
    for item in _monthly_portfolio_items(html):
        title = str(item.get("title") or "").strip()
        media = item.get("downloadMedia") if isinstance(item.get("downloadMedia"), dict) else {}
        url = media.get("url") or item.get("downloadUrl")
        if not title or not url or not period_matches(title, period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=title, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Abakkus Monthly Portfolio Disclosure section has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Abakkus monthly portfolio disclosure"))

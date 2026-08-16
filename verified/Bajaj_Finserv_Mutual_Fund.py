from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text, post_json
from core.parsing import extract_links
from core.periods import month_name, period_conflicts, period_matches

AMC = "bajaj_finserv"
PAGE_URL = "https://www.bajajamc.com/downloads?statutory-disclosures="
AJAX_URL = "https://www.bajajamc.com/wp-admin/admin-ajax.php"
_SECTION_RE = re.compile(r'data-section-id="(\d+)"\s*data-filter="year_month">')
_TITLE_RE = re.compile(r'bd-accordion-title">([^<]+)<')
_NONCE_RE = re.compile(r'data-[\w-]*nonce="([0-9a-f]{10})"')


def _monthly_section(html: str) -> str:
    for match in _SECTION_RE.finditer(html):
        title_match = _TITLE_RE.search(html[match.end():match.end() + 1500])
        if title_match and title_match.group(1).strip() == "Monthly Portfolio":
            return match.group(1)
    raise RuntimeError("Bajaj downloads page no longer has a 'Monthly Portfolio' year/month accordion")


def _nonce(html: str) -> str:
    match = _NONCE_RE.search(html)
    if not match:
        raise RuntimeError("Bajaj downloads page no longer exposes its widgets' ajax nonce")
    return match.group(1)


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    section_id = _monthly_section(html)
    nonce = _nonce(html)
    year, month = period.split("-")
    fy_start = int(year) if int(month) >= 4 else int(year) - 1
    financial_year = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    payload = post_json(
        session,
        AJAX_URL,
        data={
            "action": "bajaj_get_downloads",
            "nonce": nonce,
            "section_id": section_id,
            "year": financial_year,
            "month": month_name(period),
        },
        headers={"Referer": PAGE_URL, "X-Requested-With": "XMLHttpRequest"},
    )
    if not payload.get("success"):
        raise RuntimeError(f"Bajaj bajaj_get_downloads call failed: {payload}")
    fragment = payload.get("data", {}).get("html", "")
    documents = []
    for link in extract_links(fragment, PAGE_URL):
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", link.href, re.I):
            continue
        evidence = link.searchable
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Bajaj Monthly Portfolio accordion has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Bajaj Finserv monthly portfolio disclosure"))

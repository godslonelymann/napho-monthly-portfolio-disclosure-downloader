from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text, post_json
from core.periods import month_name, period_conflicts, period_matches

AMC = "navi"
PAGE_URL = "https://navi.com/mutual-fund/downloads/portfolio"
API_URL = "https://navi.com/wp-json/nv/v1/documents"
CATEGORY = "884"  # Monthly disclosures; 885/886/887/928 cover fortnightly/half-yearly/quarterly/overlap.
_NONCE_RE = re.compile(r'var\s+navi_property\s*=\s*\{.*?"nonce":"([^"]+)"')


def _financial_year(period: str) -> str:
    year, month = period.split("-")
    start = int(year) if int(month) >= 4 else int(year) - 1
    return f"{start}-{start + 1}"


def _nonce(session) -> str:
    html = fetch_text(session, PAGE_URL)
    match = _NONCE_RE.search(html)
    if not match:
        raise RuntimeError("Navi portfolio page no longer exposes its REST nonce")
    return match.group(1)


def discover(period: str, session=None):
    nonce = _nonce(session)
    payload = post_json(
        session,
        API_URL,
        data={"financial_year": _financial_year(period), "value": month_name(period), "category": CATEGORY, "type": "Monthly", "order": "DESC"},
        headers={"WP-NONCE": nonce},
    )
    if not payload.get("success"):
        raise RuntimeError(f"Navi documents API rejected the request: {payload}")
    documents = []
    for record in payload.get("data") or []:
        url = record.get("url")
        title = record.get("title", "")
        if isinstance(url, list):
            urls = [item.get("link") for item in url if isinstance(item, dict) and item.get("link")]
        elif isinstance(url, str) and url:
            urls = [url]
        else:
            urls = []
        for link in urls:
            evidence = f"{title} {link}"
            if period_conflicts(evidence, period) or not period_matches(evidence, period):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, label=title, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Navi documents API has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Navi monthly portfolio disclosure"))

from __future__ import annotations

import html as html_module
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import extract_periods, month_name, period_matches

AMC = "taurus"
BASE_URL = "https://taurusmutualfund.com/monthly-portfolio"


def _options(html: str, target_text: str) -> str:
    matches = []
    for match in re.finditer(r"<option[^>]*value=[\"']([^\"']+)[\"'][^>]*>(.*?)</option>", html, re.I | re.S):
        text = " ".join(re.sub(r"<[^>]+>", " ", html_module.unescape(match.group(2))).split())
        if re.search(target_text, text, re.I):
            matches.append(match.group(1))
    if len(matches) != 1:
        raise RuntimeError(f"Taurus taxonomy lookup {target_text!r} returned {len(matches)} values")
    return matches[0]


def discover(period: str, session=None):
    year, _ = period.split("-")
    initial = fetch_text(session, BASE_URL)
    year_id = _options(initial, rf"\b{year}\b")
    month_id = _options(initial, rf"\b{re.escape(month_name(period))}\b")
    documents = []
    for page in range(0, 51):
        params = {"field_monthly_portfolio_target_id": year_id, "field_month_target_id": month_id}
        if page:
            params["page"] = str(page)
        page_url = BASE_URL + "?" + urlencode(params)
        html = fetch_text(session, page_url)
        page_documents = []
        for link in extract_links(html, page_url):
            evidence = link.searchable
            fallback = evidence + " " + month_name(period) + " " + year
            if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", link.href, re.I) or not (period_matches(evidence, period) or (not extract_periods(evidence) and period_matches(fallback, period))):
                continue
            page_documents.append(document_from_link(amc=AMC, period=period, source_page_url=BASE_URL, link=link))
        before = len(documents)
        documents = dedupe_documents([*documents, *page_documents])
        if page and (not page_documents or len(documents) == before):
            break
    else:
        raise RuntimeError("Taurus result pagination exceeded 50 pages")
    if not documents:
        raise RuntimeError(f"Taurus taxonomy returned no scheme workbook for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download all Taurus monthly portfolio schemes"))

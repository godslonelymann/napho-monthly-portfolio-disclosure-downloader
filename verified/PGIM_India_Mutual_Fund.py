from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote, unquote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import create_session, post_json
from core.periods import period_conflicts, period_matches

AMC = "pgim"
PAGE_URL = "https://www.pgimindia.com/mutual-funds/disclosures/Portfolios/Monthly-Portfolio"
API_URL = "https://www.pgimindia.com/api/v1/brochure/published/disclosure"
PAYLOAD = {"headerId": 2, "sectionId": "SECTION_747960037", "source": "W", "branchCode": None}


def _url_casing_variants(url: str) -> list[str]:
    """Alternate filenames for PGIM's file host, which is case-sensitive.

    The disclosure API's own ``pdfPath`` sometimes doesn't match the filename
    actually stored on the host -- e.g. "...fund Aug 2021.xlsb" (API) vs
    "...fund aug 2021.xlsb" (stored). Observed mismatches: stray capitals,
    an "&" the API includes but the stored file doesn't
    ("banking-&-psu-debt-fund" vs "banking-psu-debt-fund"), and "sep"/"sept"
    abbreviation swaps.
    """
    base, _, name = url.rpartition("/")
    name = unquote(name)
    lower = name.lower()
    seen = {name}
    candidates = []

    def add(candidate: str) -> None:
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add(lower)
    add(lower.replace("-&-", "-"))
    if " sep " in lower:
        add(lower.replace(" sep ", " sept "))
    if " sept " in lower:
        add(lower.replace(" sept ", " sep "))

    return [f"{base}/{quote(candidate)}" for candidate in candidates]


def _resolve_url(session, url: str) -> str:
    """Return whichever casing of ``url`` the file host actually serves.

    A mismatched filename comes back as HTTP 204 (empty body), not a 404, so
    a candidate is confirmed by status code rather than by catching an
    error. Falls back to the original URL, unresolved, if nothing matches --
    the download step's own validation still reports that clearly.
    """
    timeout = (10, 30)
    try:
        if session.head(url, timeout=timeout, allow_redirects=True).status_code == 200:
            return url
    except Exception:
        pass
    for candidate in _url_casing_variants(url):
        try:
            if session.head(candidate, timeout=timeout, allow_redirects=True).status_code == 200:
                return candidate
        except Exception:
            continue
    return url


def discover(period: str, session=None):
    session = session or create_session()
    payload = post_json(session, API_URL, json=PAYLOAD)
    documents = []
    for group in payload.get("data", []):
        for record in group.get("content", []):
            url = record.get("pdfPath")
            if not url:
                continue
            # The record's own "month"/"year" fields are reliable here (unlike
            # ITI/Trust, where those track the upload date) but dateMonthYear
            # and the filename both carry the same disclosure date, so use all
            # three as corroborating evidence rather than trusting one field.
            evidence = " ".join(str(record.get(key, "")) for key in ("title", "dateMonthYear", "pdfPath"))
            if period_conflicts(evidence, period) or not period_matches(evidence, period):
                continue
            url = _resolve_url(session, url)
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=str(record.get("title", "")), primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"PGIM disclosure manifest has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download PGIM India monthly portfolio disclosures"))

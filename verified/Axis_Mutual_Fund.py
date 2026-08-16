from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import dedupe_documents, document_from_link, only_period
from core.http import create_session
from core.periods import MONTHS, month_name


AMC = "axis"
BASE_URL = os.getenv("AXIS_BASE_URL", "https://www.axismf.com")
PAGE_URL = f"{BASE_URL}/statutory-disclosures"
TOKEN_URL = f"{BASE_URL}/cms/token"
CATALOGUE_URL = f"{BASE_URL}/cms/get-scheme-documents"

# The catalogue keeps one consolidated workbook per month alongside the
# scheme-level copies; asking for "Consolidated" returns the whole fund house
# in a single file instead of ~50 near-duplicates.
SCHEME_CODE = "Consolidated"

MONTH_TOKENS = "|".join(
    [month.lower() for month in MONTHS]
    + ["jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"]
)
# Everything Axis files under a month that is *not* the monthly portfolio.
NOISE = re.compile(r"\b(?:weekly|daily|adhoc|quants|select|axistaa|fortnightly)\b", re.I)
# Pre-2020 dailies are named "Portfolio - Axis Liquid Fund for 9 November 2018".
DAILY_FUND = re.compile(rf"\bfor\s+\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_TOKENS})\b", re.I)
# Pre-2020 monthlies are named with a bare month token: "13-Jan", "January".
BARE_TOKEN = re.compile(rf"^(?:\d{{2}}[\s\-_]?(?:{MONTH_TOKENS})|(?:{MONTH_TOKENS})|portfolio)$", re.I)


class NoMonthlyPortfolio(RuntimeError):
    """Axis published no monthly portfolio for the requested month."""


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _is_monthly(document: dict) -> bool:
    # The document name alone misses the 2012-2019 era, where the name is a
    # bare month token but the URL still says "monthly_portfolio"; the URL
    # alone misses ~25 other months.  Checking both catches 140 of 167.
    filename = str(document.get("docuementURL", "")).rsplit("/", 1)[-1]
    return "monthly portfolio" in _normalize(document.get("documentName", "")) or "monthly portfolio" in _normalize(filename)


def select_monthly(documents: list[dict]) -> list[dict]:
    """Pick the monthly portfolio records out of one month's catalogue entries."""
    keyword = [document for document in documents if _is_monthly(document)]
    if keyword:
        return keyword
    # Oldest era: neither the name nor the URL carries the keyword.  Drop the
    # weekly/daily/scheme noise and keep what looks like a bare month token.
    # Three months (2018-11, 2019-08, 2019-11) hide the monthly file among
    # daily fund portfolios, so "take the only document" is not safe here.
    survivors = [
        document
        for document in documents
        if not NOISE.search(str(document.get("documentName", "")))
        and not DAILY_FUND.search(str(document.get("documentName", "")))
    ]
    bare = [document for document in survivors if BARE_TOKEN.match(str(document.get("documentName", "")).strip())]
    if bare:
        return bare
    return survivors if len(survivors) == 1 else []


def _timeout(session):
    return getattr(session, "default_timeout", (30, 120))


def fetch_token(session) -> str:
    """Return a fresh catalogue token.  The value already includes 'Bearer '."""
    response = session.post(TOKEN_URL, json={}, timeout=_timeout(session))
    response.raise_for_status()
    token = (response.json().get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"{TOKEN_URL} returned no data.token")
    return token


def _post_catalogue(session, period: str, authorization: str):
    year, month = period.split("-")
    payload = {
        "sdType": "yearMonthSchemeDocs",
        "sdID": "sdMonthSchemePortfolio",
        "year": year,
        "month": month_name(period),
        "schemeCode": SCHEME_CODE,
    }
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": BASE_URL,
        "Referer": PAGE_URL,
    }
    # The shared session only auto-retries idempotent methods, so back off on
    # throttling and server errors here.
    for attempt in range(3):
        response = session.post(CATALOGUE_URL, headers=headers, json=payload, timeout=_timeout(session))
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2**attempt)
            continue
        return response
    return response


def fetch_catalogue(session, period: str) -> list[dict]:
    authorization = fetch_token(session)
    response = _post_catalogue(session, period, authorization)
    if response.status_code in (401, 403):
        # Tokens are short-lived; one refresh is enough in practice.
        response = _post_catalogue(session, period, fetch_token(session))
    response.raise_for_status()
    data = response.json().get("data") or {}
    return data.get("documentList") or []


def discover(period: str, session=None):
    session = session or create_session()
    documents = []
    for record in select_monthly(fetch_catalogue(session, period)):
        url = record.get("docuementURL")  # the API really does misspell it
        if not url:
            continue
        # No filename date guard here: Axis encodes spaces as "_20", so the
        # shared period parser reads nothing from most of these names and
        # actively misreads two of them ("13-Nov" scans as 2013-12).  The
        # year/month sent to the API is the authoritative filter.
        documents.append(
            document_from_link(
                amc=AMC,
                period=period,
                source_page_url=PAGE_URL,
                link=url,
                label=str(record.get("documentName") or ""),
                scheme=SCHEME_CODE,
                primary=True,
                metadata={"posted_date": record.get("documentPostedDate")},
            )
        )
    if not documents:
        raise NoMonthlyPortfolio(f"Axis published no monthly portfolio for {period}")
    return only_period(dedupe_documents(documents), period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Axis monthly portfolio workbooks"))

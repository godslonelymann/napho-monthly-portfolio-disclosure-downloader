from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import settings
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import create_session
from core.periods import current_period, resolve_as_of_period


AMC = "hdfc"
PAGE_URL = os.getenv("HDFC_PAGE_URL", "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio")

# The listing page renders from a CMS API rather than static HTML -- the
# page's own JS calls this endpoint (found in its _app bundle) to populate
# the "view by fiscal year" dropdown. It answers plain (non-browser-shaped)
# requests fine; the Akamai wall that blocks a plain GET of the *page* does
# not sit in front of this API subdomain. The body must be multipart
# FormData, not JSON -- posting JSON is accepted (200) but silently returns
# an unrelated "notices" payload instead of erroring, which is easy to miss.
_API_BASE = "https://cms.hdfcfund.com/en/hdfc/api/v2/disclosures"

# From April 2021 onward, querying a specific (year, month) reliably returns
# just that month's per-scheme workbooks -- confirmed by portfolioMonths
# reporting real month numbers, and the month values line up with calendar
# months (not the fiscal year the dropdown label suggests). Before that, the
# API only supports "give me everything filed under fiscal-year bucket Y"
# (month=0), and which calendar months land in bucket Y is inconsistent --
# some years dump Jan-Dec, others dump Apr-Mar of the following year, and
# one (2020) omits its own Jan-Mar entirely because those got filed under
# 2019's bucket instead. So below this threshold, several buckets are
# queried and every candidate file's actual as-of date is what decides
# whether it belongs to the requested period, not which bucket it came from.
_STRUCTURED_FROM = "2021-04"


def _api_files(session, config, *, year: int, month: int) -> list[dict]:
    response = session.post(
        f"{_API_BASE}/monthfortportfolio",
        files={"year": (None, str(year)), "type": (None, "monthly"), "month": (None, str(month))},
        headers={"Origin": "https://www.hdfcfund.com", "Referer": PAGE_URL},
        timeout=(config.connect_timeout, config.read_timeout),
    )
    if response.status_code != 200:
        raise RuntimeError(f"HDFC disclosures API returned {response.status_code} for year={year} month={month}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"HDFC disclosures API returned non-JSON for year={year} month={month}") from exc
    return ((payload.get("data") or {}).get("files")) or []


def discover(period: str, session=None):
    session = session or create_session()
    config = settings()
    before = current_period()
    year, month = int(period[:4]), int(period[5:7])

    if period >= _STRUCTURED_FROM:
        entries = _api_files(session, config, year=year, month=month)
    else:
        # Query the target calendar year plus its neighbours so whichever
        # fiscal bucket actually holds this period's files gets fetched --
        # the per-file period match below discards everything else.
        entries = []
        for candidate_year in dict.fromkeys((year, year - 1, year + 1)):
            entries.extend(_api_files(session, config, year=candidate_year, month=0))

    documents = []
    for entry in entries:
        title = entry.get("title") or ""
        # Some fiscal-year buckets (2020's, at least) mix in fortnightly
        # debt-scheme disclosures alongside the monthly ones even though the
        # request asked for type=monthly -- these aren't the report we want.
        if "fortnightly" in title.lower():
            continue
        if (entry.get("extension") or "").lower() not in {"xls", "xlsx"}:
            continue
        file_info = entry.get("file") or {}
        url = file_info.get("url")
        filename = file_info.get("filename") or ""
        if not url:
            continue
        # Some schemes (FMPs, target-maturity index funds) carry their own
        # launch/maturity date in the name -- "HDFC FMP 3360D March 2014 (1)
        # - 31 December 2021.xlsx" -- alongside the real as-of date. The
        # as-of date is the one stated last, which resolve_as_of_period
        # picks over an earlier scheme-name date.
        if resolve_as_of_period(filename, title, before=before) != period:
            continue
        documents.append(
            document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=title, primary=True)
        )
    documents = dedupe_documents(documents)
    if not documents:
        raise RuntimeError(f"HDFC disclosures API has no monthly workbook for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download HDFC monthly portfolio schemes"))

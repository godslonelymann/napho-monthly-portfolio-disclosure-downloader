from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.periods import period_conflicts, period_matches

AMC = "kotak"
PAGE_URL = "https://www.kotakmf.com/forms-and-downloads/portfolios"
API_BASE = "https://www.kotakmf.com/api/kotakapi/forms/user/"
FILE_BASE = "https://vatseelabs-s3.kotakmf.com/"
HEADERS = {"Referer": PAGE_URL}


def discover(period: str, session=None):
    catalogue = fetch_json(session, urljoin(API_BASE, "getsuperheaderlist"), headers=HEADERS)
    portfolio_header = next(
        header
        for group in catalogue["superHeaderList"]
        for header in group["headerList"]
        if header["headerTitle"].strip().lower() == "portfolios"
    )
    # Dropdown option 51 is "Consolidated & Fortnightly Portfolio" -- the AMC-wide
    # SEBI monthly portfolio AMFI links to.  The header's other options (52, 53,
    # 66, ...) are look-through disclosures for individual overseas feeder funds
    # (CI Global Alpha Innovators, CI Emerging Markets Fund, an iShares UCITS
    # ETF, ...) whose own "as on <date>" titles can coincidentally match the
    # requested period despite being a different, unrelated document.
    if 51 not in portfolio_header["optionList"]:
        raise RuntimeError("Kotak Portfolios header no longer exposes option 51 (Consolidated & Fortnightly Portfolio)")
    payload = fetch_json(session, urljoin(API_BASE, f"getsubheaderList/{portfolio_header['headerId']}"), params={"option": 51}, headers=HEADERS)
    documents = []
    for record in payload.get("subHeaderList", []):
        if record.get("contentType") != "upload" or not record.get("content"):
            continue
        filename = record.get("fileName") or Path(record["content"]).name
        if Path(filename).suffix.lower() not in {".xls", ".xlsx", ".xlsm"}:
            continue
        title = record.get("subHeaderTitle", "")
        # "Fortnightly Portfolio as on July 31, 2026" carries the same
        # month-end date as the real monthly file, so it must be excluded
        # by label -- period matching alone can't tell them apart.
        if re.search(r"fortnightly", title, re.I):
            continue
        evidence = f"{title} {filename}"
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        url = urljoin(FILE_BASE, quote(record["content"], safe="/"))
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=title, filename=filename, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Kotak Portfolios catalogue has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Kotak Mahindra monthly portfolio disclosure"))

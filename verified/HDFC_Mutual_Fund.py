from __future__ import annotations

import os
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import latest_day, settings
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import create_session
from core.parsing import extract_file_urls
from core.periods import current_period, extract_periods, month_name, resolve_as_of_period


AMC = "hdfc"
PAGE_URL = os.getenv("HDFC_PAGE_URL", "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio")

# The listing page (and files.hdfcfund.com generally) has no archive or
# month filter -- it only ever shows whichever month was most recently
# uploaded.  The files are still hosted, though, at a predictable address:
# "https://files.hdfcfund.com/s3fs-public/<upload-folder>/Monthly <Scheme> -
# <last day> <Month> <Year>.xlsx", where the upload folder is normally the
# data month plus one (July 2026 data landed in the "2026-08" folder).  For
# a back month, reconstruct that address per scheme from the live listing
# and confirm each guess by sniffing its response before trusting it.
_FOLDER_RE = re.compile(r"/s3fs-public/(20\d\d-\d\d)/")
_DATE_TOKEN_RE = re.compile(r"(\d{1,2})%20([A-Za-z]+)%20(20\d{2})")


def _period_index(period: str) -> int:
    return int(period[:4]) * 12 + int(period[5:7])


def _shift_period(period: str, delta_months: int) -> str:
    index = _period_index(period) + delta_months - 1
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _retarget_url(url: str, target_period: str) -> str | None:
    folder_match = _FOLDER_RE.search(url)
    date_match = _DATE_TOKEN_RE.search(url)
    if not folder_match or not date_match:
        return None
    # Extract the period from just the "31 July 2026" date token, not the
    # whole URL -- the folder segment itself ("/2026-08/") also parses as an
    # ISO-style date and would otherwise make this ambiguous.
    day, month_text, year = date_match.groups()
    data_periods = extract_periods(f"{day} {month_text} {year}")
    if len(data_periods) != 1:
        return None
    data_period = next(iter(data_periods))
    folder_offset = _period_index(folder_match.group(1)) - _period_index(data_period)
    target_folder = _shift_period(target_period, folder_offset)
    target_year, target_month = int(target_period[:4]), int(target_period[5:7])
    target_token = f"{latest_day(target_year, target_month)}%20{month_name(target_period)}%20{target_year}"
    url = _FOLDER_RE.sub(f"/s3fs-public/{target_folder}/", url, count=1)
    url = _DATE_TOKEN_RE.sub(target_token, url, count=1)
    return url


def _url_serves_workbook(session, url: str, config) -> bool:
    try:
        response = session.get(url, stream=True, timeout=(config.connect_timeout, config.read_timeout))
    except Exception:
        return False
    try:
        if response.status_code != 200:
            return False
        head = next(response.iter_content(8), b"")
    finally:
        response.close()
    if url.lower().split("?", 1)[0].endswith(".xls"):
        return head.startswith(b"PK") or head.startswith(b"\xd0\xcf\x11\xe0")
    return head.startswith(b"PK")


def _fetch_html() -> str:
    # Plain requests (and even a real headless-Chromium Playwright session)
    # get an Akamai 403 "Access Denied" on this listing page.  files.hdfcfund.com
    # itself (where the actual workbooks live) is open to plain requests --
    # only this listing page is blocked, and it is blocked specifically for
    # non-browser-shaped / headless-shaped TLS-HTTP2 fingerprints.  curl_cffi
    # replays a real Chrome TLS+HTTP2 fingerprint without needing a browser.
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError(
            "HDFC discovery requires curl_cffi to get past Akamai's bot wall; install requirements ('pip install curl_cffi')"
        ) from exc

    config = settings()
    response = curl_requests.get(
        PAGE_URL,
        impersonate="chrome124",
        timeout=(config.connect_timeout, config.read_timeout),
    )
    if response.status_code != 200:
        raise RuntimeError(f"HDFC disclosure page returned {response.status_code} even with a browser-impersonated request")
    return response.text


def discover(period: str, session=None):
    html = _fetch_html()
    # The scheme links aren't <a href> tags -- they're embedded in an inline
    # JSON payload (title/file/url fields) that hydrates a JS-rendered table,
    # so a flat file-URL regex scan is needed instead of anchor parsing.  The
    # URL itself already carries the human-readable "31 July 2026" filename.
    scheme_urls = [
        url
        for url in extract_file_urls(html, PAGE_URL)
        if re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", url, re.I) and "files.hdfcfund.com" in url.lower()
    ]
    before = current_period()
    documents = []
    for url in scheme_urls:
        # Some schemes (FMPs, target-maturity index funds) carry their own
        # launch/maturity date in the name -- "...FMP 1876D March 2022 - 31
        # July 2026.xlsx" -- alongside the real as-of date. A flat "does the
        # requested period appear anywhere in this URL" match treated both
        # as equally valid, so a query for 2022-03 wrongly matched a file
        # that is actually July 2026 data. The as-of date is the one stated
        # last, which resolve_as_of_period picks over an earlier scheme-name
        # date the same way it already does for HSBC.
        if resolve_as_of_period(url, before=before) != period:
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, primary=True))
    documents = dedupe_documents(documents)
    if documents:
        return only_period(documents, period, required=True)

    # The listing is always the newest month, so a back month means none of
    # scheme_urls matched above -- reconstruct each one's address for the
    # requested period and verify before trusting it.
    candidates = list(dict.fromkeys(filter(None, (_retarget_url(url, period) for url in scheme_urls))))
    if not candidates:
        raise RuntimeError(f"HDFC listing has no current monthly workbook for {period}, and no scheme URL could be reconstructed for it")
    config = settings()
    verify_session = session or create_session()
    for url in candidates:
        if _url_serves_workbook(verify_session, url, config):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, primary=True, metadata={"constructed": True}))
    documents = dedupe_documents(documents)
    if not documents:
        raise RuntimeError(f"HDFC listing has no current monthly workbook for {period}, and no reconstructed scheme URL verified as a real file")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download HDFC monthly portfolio schemes"))

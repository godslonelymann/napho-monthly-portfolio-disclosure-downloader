from __future__ import annotations

import html as html_module
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import PeriodUnavailable, document_from_link, dedupe_documents, only_period
from core.periods import extract_periods
from amcs._shared import fetch_first_html

AMC = "baroda_bnp"
PAGE_ALIASES = (
    "https://www.barodabnpparibasmf.in/downloads/monthly-portfolio-scheme",
    "https://www.barodabnpparibasmf.in/downloads",
    "https://www.barodabnpparibasmf.in/statutory-disclosures",
    "https://www.barodabnpparibasmf.in/",
)
AJAX_URL = "https://www.barodabnpparibasmf.in/ajax-load-more-documents"
LIST_ITEM_RE = re.compile(r"<li\b.*?</li>", re.I | re.S)
TITLE_RE = re.compile(r'file-name["\'][^>]*>(.*?)</p>', re.I | re.S)
HREF_RE = re.compile(r'href=(["\'])((?:(?!\1).)*?/download_documents/(?:(?!\1).)*?)\1', re.I | re.S)


def _csrf(html: str) -> str:
    match = re.search(r'name=["\']csrf_test_name["\'][^>]*value=["\']([^"\']+)', html, re.I)
    if not match:
        match = re.search(r'csrf_test_name["\']?\s*[:=]\s*["\']([^"\']+)', html, re.I)
    if not match:
        raise RuntimeError("Baroda BNP page did not expose csrf_test_name")
    return match.group(1)


def _title_periods(title: str) -> set[str]:
    # Some scheme names embed a date of their own -- e.g. "NIFTY SDL December
    # 2028 Index Fund" -- which would otherwise be mistaken for the document's
    # own period.  The real "as on <date>" always trails the scheme name, so
    # matching only the text after it avoids that trap.
    tail = re.split(r"\bas\s+on\b", title, flags=re.I)[-1]
    return extract_periods(tail)


def _title_matches(title: str, period: str) -> bool:
    # Older (pre-2023) titles have no "as on" phrase at all -- e.g. "... Fund
    # - Nov 30, 2022" -- so the tail is the whole title, and the day number
    # ("30") is sometimes *also* misread as a short-form year ("2030") by one
    # of the shared parser's fallback patterns, alongside the correct
    # "2022-11" reading.  Requiring the target period merely be *one of* the
    # candidates (matching core.periods.period_matches' own semantics)
    # accepts the correct reading instead of discarding it for being
    # ambiguous.
    return period in _title_periods(title)


def _parse_list_items(markup: str, page_url: str, period: str):
    # Titles live in a <p class="file-name"> next to the <a>, not inside it,
    # so they have to be paired up per <li> rather than read off the anchor.
    documents = []
    for block in LIST_ITEM_RE.findall(markup):
        title_match = TITLE_RE.search(block)
        href_match = HREF_RE.search(block)
        if not title_match or not href_match:
            continue
        title = html_module.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        if not _title_matches(title, period):
            continue
        href = html_module.unescape(href_match.group(2))
        documents.append(
            document_from_link(
                amc=AMC,
                period=period,
                source_page_url=page_url,
                link=href,
                label=title,
                primary=bool(re.search(r"all\s+funds|combined", title, re.I)),
            )
        )
    return documents


def _collect_year(session, page_url: str, token: str, year: int, period: str, documents: list) -> bool:
    """Page through one year's archive bucket, newest first, collecting matches.

    Returns whether any document for ``period`` was found in this bucket.
    """

    page, total, remaining = "0", "0", "0"
    found = False
    for _ in range(200):
        response = session.post(
            AJAX_URL,
            data={
                "csrf_test_name": token,
                "cnt": total,
                "pagination": page,
                "send_category": "17",
                "send_year": str(year),
                "remaining_cnt": remaining,
            },
            headers={"Referer": page_url, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json,*/*"},
            timeout=getattr(session, "default_timeout", (30, 120)),
        )
        response.raise_for_status()
        payload = response.json()
        batch_documents = _parse_list_items(payload.get("data", ""), page_url, period)
        if batch_documents:
            found = True
            documents.extend(batch_documents)
        elif found:
            # Batches arrive newest-first; once a batch with none of the
            # target period follows one that had matches, we've moved past
            # the month and the rest of the bucket is strictly older.
            break
        page = str(payload.get("pagination", 0))
        total = str(payload.get("total_row", 0))
        remaining = str(payload.get("remaining_cnt", 0))
        if payload.get("status") != "Y":
            break
    else:
        raise RuntimeError(f"Baroda BNP archive pagination for year {year} exceeded 200 pages")
    return found


def _year_bucket(period: str) -> int:
    # The year dropdown buckets by upload date, not portfolio month: bucket
    # 2026 holds Dec 2025 through Jul 2026, bucket 2025 holds Dec 2024
    # through Nov 2025, etc. -- so December of year Y is filed under Y + 1.
    year, month = (int(part) for part in period.split("-"))
    return year + 1 if month == 12 else year


def _dropdown_years(html: str) -> list[int]:
    return [int(year) for year in re.findall(r'class="onptionMenu"\s+id="(\d{4})"', html)]


def _current_titles(markup: str) -> list[str]:
    titles = []
    for block in LIST_ITEM_RE.findall(markup):
        match = TITLE_RE.search(block)
        if match:
            titles.append(html_module.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip())
    return titles


def discover(period: str, session=None):
    html, page_url = fetch_first_html(session, PAGE_ALIASES)
    is_redesigned = "/downloads/monthly-portfolio-scheme" in page_url
    if not is_redesigned:
        raise RuntimeError("Baroda BNP page structure changed: expected the redesigned downloads page")

    # The static page only ever renders the first archive batch (6 items) --
    # even the current month's remaining schemes sit behind the same
    # "Load More" pagination, so every period (including the latest) has to
    # go through the AJAX archive rather than trusting the static render.
    token = _csrf(html)
    primary_bucket = _year_bucket(period)
    # The AJAX endpoint 500s for a send_year outside the dropdown's own
    # options (e.g. one past the most recent year it has created), so only
    # ever query years the page itself advertises.  Include the latest
    # available year as a safety net in case a new bucket hasn't been
    # created yet for a very recent period.
    valid_years = _dropdown_years(html)
    preferred = [primary_bucket, primary_bucket - 1, *([max(valid_years)] if valid_years else [])]
    candidates = [year for year in dict.fromkeys(preferred) if not valid_years or year in valid_years]
    if not candidates:
        candidates = [max(valid_years)]

    all_documents: list = []
    for bucket_year in candidates:
        if _collect_year(session, page_url, token, bucket_year, period, all_documents):
            break

    documents = dedupe_documents(all_documents)
    if not documents:
        # The archive across the candidate years has nothing for this period.
        # Check what the page's current listing does publish, so a "just not
        # published yet" period is distinguishable from a page structure that
        # broke the adapter outright.
        published: set[str] = set()
        for title in _current_titles(html):
            published |= _title_periods(title)
        if published:
            raise PeriodUnavailable(
                f"Baroda BNP's downloads page currently publishes only {', '.join(sorted(published))}; "
                f"the archive endpoint returned no results for {period} in years {sorted(set(candidates))}"
            )
        raise RuntimeError(f"Baroda BNP has no monthly/combined record for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Baroda BNP Paribas monthly portfolios"))

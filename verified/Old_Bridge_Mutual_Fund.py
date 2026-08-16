from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import PeriodUnavailable, document_from_link, dedupe_documents, only_period
from core.http import fetch_text
from core.parsing import extract_file_urls, extract_links
from core.periods import current_period, resolve_as_of_period


AMC = "old_bridge"
PAGE_URL = "https://oldbridgemf.com/statutory-disclosures.html#v-pills-tabContent2"


def discover(period: str, session=None):
    html = fetch_text(session, PAGE_URL)
    candidates: list[tuple[str, str]] = [(link.href, link.text) for link in extract_links(html, PAGE_URL)]
    candidates += [(url, "") for url in extract_file_urls(html, PAGE_URL)]

    before = current_period()
    documents = []
    monthly_candidates = 0
    for url, text in candidates:
        evidence = f"{text} {url}"
        if not re.search(r"\.(?:xlsx|xls)(?:[?#]|$)", url, re.I) or "/uploads/" not in url.lower():
            continue
        if not re.search(r"portfolio", evidence, re.I) or re.search(r"fortnightly|weekly|half[- ]?year|scheme[_ -]?dashboard|performance|proxy|sebi|ir[_ -]", evidence, re.I):
            continue
        monthly_candidates += 1
        # The CMS appends a random content-hash id to every filename (e.g.
        # "..._20e8c1644b.xlsx"); the id can start with digits that read as
        # part of a date once glued to a day-of-month, so the filename and
        # link text are each resolved independently rather than matched as
        # one combined blob -- see resolve_as_of_period's docstring.
        name = urlsplit(url).path.rsplit("/", 1)[-1]
        if resolve_as_of_period(name, text, before=before) != period:
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text or None))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        if monthly_candidates:
            raise PeriodUnavailable(f"Old Bridge page has no monthly scheme workbook for {period}")
        raise RuntimeError("Old Bridge page contains no recognizable monthly portfolio workbooks")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Old Bridge monthly portfolio schemes"))

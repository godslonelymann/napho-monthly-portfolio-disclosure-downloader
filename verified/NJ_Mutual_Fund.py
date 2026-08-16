from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, only_period
from core.http import fetch_text
from core.parsing import extract_links
from core.periods import period_matches

AMC = "nj_mutual_fund"
PAGE_URL = "https://downloads.njmutualfund.com/njmf_download.php?nme=127"


def discover(period: str, session=None):
    documents = []
    for link in extract_links(fetch_text(session, PAGE_URL), PAGE_URL):
        parsed = urlsplit(link.href)
        file_name = parse_qs(parsed.query).get("file", [""])[0]
        if parsed.path.lower().endswith("viewfile.php") and re.search(r"\.(?:xls|xlsx)(?:$|[?#])", file_name, re.I) and period_matches(f"{link.searchable} {file_name}", period):
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=link, label=unquote(file_name), filename=unquote(Path(file_name).name), file_type=Path(file_name).suffix.lstrip(".")))
    if not documents:
        raise RuntimeError(f"NJ Mutual Fund has no viewfile.php scheme link for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(
        run_cli(
            amc=AMC,
            discover=discover,
            description="Download all NJ Mutual Fund monthly portfolio schemes",
            # A single fast HTML page fetch -- cheap enough to re-run once if
            # a file comes up missing, to tell "our download failed" apart
            # from "the site stopped listing it mid-run".
            rediscoverable=True,
        )
    )

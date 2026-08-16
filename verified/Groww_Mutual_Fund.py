from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.parsing import recursive_records
from core.periods import period_conflicts, period_matches

AMC = "groww"
API_URL = "https://mapi.growwmf.in/v1/api/mf-data/v1/files"
PAGE_URL = "https://growwmf.in/"


def discover(period: str, session=None):
    payload = fetch_json(session, API_URL, headers={"Referer": PAGE_URL, "Accept": "application/json"})
    documents = []
    for record in recursive_records(payload):
        text = str(record)
        if not re.search(r"monthly\s*portfolio", text, re.I) or re.search(r"fortnightly|weekly|half[- ]?year", text, re.I):
            continue
        name = next((str(record[key]) for key in record if key.lower() in {"name", "filename", "title"} and record[key]), "")
        url = next((str(record[key]) for key in record if key.lower() in {"publicurl", "url", "downloadurl"} and isinstance(record[key], str)), "")
        if not url or re.search(r"fortnightly|weekly|half[- ]?year", f"{name} {url}", re.I) or period_conflicts(url, period) or not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I) or not period_matches(f"{name} {url}", period, month_end_only=True):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=name or url))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Groww file tree has no Portfolio/{period} workbooks")
    return documents


if __name__ == "__main__":
    raise SystemExit(
        run_cli(
            amc=AMC,
            discover=discover,
            description="Download Groww Portfolio file-tree disclosures",
            # A single fast JSON API call -- cheap enough to re-run once if
            # the file comes up missing, to tell "our download failed" apart
            # from "the site stopped listing it mid-run".
            rediscoverable=True,
        )
    )

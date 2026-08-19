from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.parsing import recursive_records
from amcs._shared import string_values
from core.periods import period_conflicts, period_matches
from core.periods import month_name

AMC = "invesco"
BASE_URL = "https://www.invescomutualfund.com/api/CompleteMonthlyHoldings"
PAGE_URL = "https://www.invescomutualfund.com/"
CLASSIFICATIONS = ("equity", "fixed-income", "hybrid", "solution", "other", "commodities")


def discover(period: str, session=None):
    year = period.split("-")[0]
    month_key = month_name(period, abbreviated=True) + "Url"
    documents = []
    errors = []
    for classification in CLASSIFICATIONS:
        api_url = BASE_URL + "?" + urlencode({"year": year, "classification": classification})
        try:
            payload = fetch_json(session, api_url, headers={"Referer": PAGE_URL, "Accept": "application/json"})
        except Exception as exc:
            # A failed classification call used to be swallowed here and
            # treated the same as "this classification legitimately has
            # nothing published" -- so a transient network hiccup or a
            # temporary 500 from Invesco's API silently turned into "no
            # portfolio disclosure for this period" for the whole month,
            # and that false negative then stuck: NO_DATA is terminal and
            # never automatically retried (see backfill_range.py's
            # _RETRYABLE_STATUSES). Recording it and raising below instead
            # keeps a real failure classified as retryable.
            errors.append(f"{classification}: {exc}")
            continue
        for record in recursive_records(payload):
            text = json.dumps(record, ensure_ascii=False)
            # The live API uses month-specific fields (JanUrl, FebUrl, ...),
            # while the file URLs themselves are opaque and contain no date.
            direct = record.get(month_key)
            if isinstance(direct, str) and direct:
                label = f"{record.get('Name') or record.get('name') or ''} {month_name(period)} {year}"
                if "/docs/" in direct.lower() and re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", direct, re.I):
                    documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=direct, label=label))
                continue
            urls = [value for key, value in record.items() if key.lower() in {"url", "fileurl", "downloadurl", "path", "documenturl"} for value in string_values(value)]
            if not urls:
                urls = [value for value in string_values(record) if value.startswith("http")]
            for url in urls:
                if period_conflicts(url, period) or "/docs/" not in url.lower() or not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I):
                    continue
                if period_matches(text + " " + url, period):
                    documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text[:240]))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        if errors:
            raise RuntimeError(
                f"Invesco classifications API unreachable for {period} "
                f"({len(errors)}/{len(CLASSIFICATIONS)} classifications failed): {'; '.join(errors)}"
            )
        raise RuntimeError(f"Invesco classifications returned no monthly holding for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Invesco monthly holdings across classifications"))

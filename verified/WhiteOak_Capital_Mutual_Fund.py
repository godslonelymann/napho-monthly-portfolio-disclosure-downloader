from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.parsing import recursive_records
from amcs._shared import string_values
from core.periods import extract_periods

AMC = "whiteoak"
BASE_URL = "https://cms.whiteoakamc.com/api/scheme-portfolios"
PAGE_URL = "https://www.whiteoakamc.com/scheme-portfolios"


def _file_periods(url: str) -> set[str]:
    name = unquote(Path(urlsplit(url).path).name)
    name = re.sub(r"_[0-9a-f]{8,}(?=\.(?:xls|xlsx)$)", "", name, flags=re.I)
    return extract_periods(name)


def discover(period: str, session=None):
    documents = []
    page_count = None
    for page in range(1, 101):
        params = {"pagination[page]": page, "pagination[pageSize]": 100, "populate": "*", "filters[period][$eq]": "monthly"}
        api_url = BASE_URL + "?" + urlencode(params)
        payload = fetch_json(session, api_url, headers={"Referer": PAGE_URL, "Accept": "application/json"})
        if page_count is None:
            page_count = _page_count(payload)
        records = list(recursive_records(payload))
        page_documents = []
        for record in records:
            text = json.dumps(record, ensure_ascii=False)
            if not re.search(r"monthly", text, re.I):
                continue
            urls = [value for key, child in record.items() if key.lower() in {"url", "downloadurl", "fileurl", "path", "documenturl", "attachment", "file"} for value in string_values(child)]
            if not urls:
                urls = [value for value in string_values(record) if value.startswith(("http://", "https://"))]
            for url in urls:
                if not re.search(r"\.(?:xls|xlsx)(?:[?#]|$)", url, re.I) or _file_periods(url) != {period}:
                    continue
                page_documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=text[:240]))
        documents = dedupe_documents([*documents, *page_documents])
        if page_count is not None and page >= page_count:
            break
        if not records:
            break
    else:
        raise RuntimeError("WhiteOak Strapi pagination exceeded 100 pages")
    documents = only_period(documents, period)
    if not documents:
        raise RuntimeError(f"WhiteOak Strapi catalogue has no monthly scheme for {period}")
    return documents


def _page_count(payload) -> int | None:
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict):
            pagination = meta.get("pagination")
            if isinstance(pagination, dict) and str(pagination.get("pageCount", "")).isdigit():
                return int(pagination["pageCount"])
    return None


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download WhiteOak paginated monthly scheme portfolios"))

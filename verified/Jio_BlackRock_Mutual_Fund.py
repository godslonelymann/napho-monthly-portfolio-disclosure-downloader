from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_text, post_text
from core.periods import period_conflicts, period_matches

AMC = "jio_blackrock"
PAGE_URL = "https://www.jioblackrockamc.com/statutory-disclosure/disclosures/monthly-portfolio-disclosure"
_CHUNK_RE = re.compile(r"/_next/static/chunks/app/\(mf\)/\(public\)/statutory-disclosure/[^\"'\\]+?page-[0-9a-f]+\.js")
_ACTION_ID_RE = re.compile(r"[0-9a-f]{40,44}")


def _action_id(session) -> str:
    # The page's file list loads through a Next.js Server Action, addressed
    # by a build-specific hash id that isn't in the page HTML itself -- it
    # lives inside the route's own JS chunk, whose filename hash also changes
    # every deploy.  Discover the chunk path from the page, then the action
    # id from the chunk, instead of hardcoding either.
    html = fetch_text(session, PAGE_URL)
    chunk_match = _CHUNK_RE.search(html)
    if not chunk_match:
        raise RuntimeError("JioBlackRock page no longer references its statutory-disclosure route chunk")
    chunk_js = fetch_text(session, "https://www.jioblackrockamc.com" + chunk_match.group(0))
    action_match = _ACTION_ID_RE.search(chunk_js)
    if not action_match:
        raise RuntimeError("JioBlackRock route chunk no longer contains a Server Action id")
    return action_match.group(0)


def discover(period: str, session=None):
    year, month = period.split("-")
    month_name = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")[int(month) - 1]
    action_id = _action_id(session)
    headers = {
        "accept": "text/x-component",
        "content-type": "text/plain;charset=UTF-8",
        "next-action": action_id,
        "Referer": PAGE_URL,
    }
    body = f'["monthly-portfolio-disclosure",{{"year":"$undefined","month":"{month_name}","date":"$undefined"}},"MF"]'
    text = post_text(session, PAGE_URL, data=body.encode("utf-8"), headers=headers)
    # The response is a React Server Components stream: one JSON value per
    # "<index>:<json>" line, not a single JSON document.  The file list is
    # whichever line decodes to an object with a "data" list.
    records = []
    for line in text.splitlines():
        _, _, value = line.partition(":")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict) and isinstance(decoded.get("data"), list):
            records = decoded["data"]
            break
    documents = []
    for record in records:
        file_info = record.get("file") or {}
        url = file_info.get("url")
        title = record.get("title", "")
        # The API mixes in stale prior-year records that share the same
        # "month" label (e.g. a July 2025 entry alongside July 2026) -- the
        # title's own DD-MM-YYYY is the only reliable period signal.
        if not url or period_conflicts(title, period) or not period_matches(title, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=title, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"JioBlackRock monthly-portfolio-disclosure action has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Jio BlackRock monthly portfolio disclosure"))

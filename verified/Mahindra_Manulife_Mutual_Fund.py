from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import fetch_json
from core.periods import period_conflicts, period_matches
from amcs._shared import aes_cbc_decrypt

AMC = "mahindra_manulife"
PAGE_URL = "https://www.mahindramanulife.com/downloads#disclosures-portfolio-disclosure-monthly-portfolio-disclosure"
API_URL = "https://investorapi.mahindramanulife.com/api/v1/web/preLogin/downloads"
# Lifted from the site's public JS bundle (assets/index-<hash>.js).
AES_KEY = b"mahindra2024mahindra2024mahindra"
AES_IV = b"hasnainsheikh202"


def _find_category(nodes, name):
    for node in nodes:
        if node.get("categoryName") == name:
            return node
        found = _find_category(node.get("subcategories") or [], name)
        if found is not None:
            return found
    return None


def discover(period: str, session=None):
    response = fetch_json(session, API_URL)
    decrypted = aes_cbc_decrypt(base64.b64decode(response["payload"]), AES_KEY, AES_IV)
    payload = json.loads(decrypted)
    monthly = _find_category(payload.get("data") or [], "Monthly Portfolio Disclosure")
    if monthly is None:
        raise RuntimeError("Mahindra Manulife catalogue no longer has a 'Monthly Portfolio Disclosure' category")
    documents = []
    for year_node in monthly.get("subcategories", []):
        for record in year_node.get("files", []):
            url = record.get("fileUrl")
            title = record.get("title", "")
            if not url or period_conflicts(title, period) or not period_matches(title, period):
                continue
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=title, primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"Mahindra Manulife Monthly Portfolio Disclosure catalogue has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Mahindra Manulife monthly portfolio disclosure"))

from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import document_from_link, dedupe_documents, only_period
from core.http import post_json
from core.periods import period_conflicts, period_matches
from amcs._shared import aes_cbc_decrypt, aes_cbc_encrypt

AMC = "iti"
PAGE_URL = "https://www.itiamc.com/statuory-disclosure?type=Portfolio%20Disclosures"
API_URL = "https://itiamc.com/jeeth/api/v1/catalog/getPartnerDocumentByType"
# Lifted from the site's public Angular bundle (main.<hash>.js); the catalog
# endpoint's request and response bodies are both AES-128-CBC/PKCS7, base64
# wrapped as {"eData": "..."}.
AES_KEY = b"aar6tzij8o1snaar"
AES_IV = b"0123456789ABCDEF"


def _catalog(session):
    request_body = {"type": "Disclosure", "guid": uuid.uuid4().hex, "timeStamp": int(time.time() * 1000)}
    encrypted = base64.b64encode(aes_cbc_encrypt(json.dumps(request_body).encode("utf-8"), AES_KEY, AES_IV)).decode("ascii")
    response = post_json(session, API_URL, json={"eData": encrypted})
    decrypted = aes_cbc_decrypt(base64.b64decode(response["eData"]), AES_KEY, AES_IV)
    payload = json.loads(decrypted)
    if payload.get("status") != 0:
        raise RuntimeError(f"ITI catalog request failed: {payload.get('message')}")
    return payload["data"]


def discover(period: str, session=None):
    data = _catalog(session)
    disclosures = next((entry for entry in data["typeList"] if entry.get("subType") == "Portfolio Disclosures"), None)
    if disclosures is None:
        raise RuntimeError("ITI catalog no longer has a 'Portfolio Disclosures' section")
    monthly = next((topic for topic in disclosures.get("subTypesList", []) if topic.get("topic") == "Monthly"), None)
    if monthly is None:
        raise RuntimeError("ITI catalog no longer has a 'Monthly' Portfolio Disclosures topic")
    documents = []
    for record in monthly.get("topicsList", []):
        url = record.get("url")
        # The API's own "month"/"year" fields track the upload date, not the
        # disclosure date -- July's portfolio is stamped "August" -- so the
        # period must come from fileName ("Monthly Portfolio - July 2026"),
        # falling back to the URL's filename if that ever changes shape.
        evidence = f"{record.get('fileName', '')} {url}"
        if not url or period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=url, label=record.get("fileName", ""), primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if not documents:
        raise RuntimeError(f"ITI Portfolio Disclosures catalog has no monthly workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download ITI monthly portfolio disclosure"))

from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.config import settings
from core.discovery import DiscoveryResult, PeriodUnavailable, ResolutionResult, dedupe_documents, document_from_link, only_period
from core.http import create_session, post_json
from core.periods import period_conflicts, period_matches


AMC = "jm_financial"
PAGE_URL = os.getenv("JM_PAGE_URL", "https://www.jmfinancialmf.com/downloads/Portfolio-Disclosure")
API_URL = os.getenv("JM_API_URL", "https://jmmfapi.jmfinancialmf.com/api/")
FILE_BASE_URL = os.getenv("JM_FILE_BASE_URL", "https://www.jmfinancialmf.com/")
_CATEGORY_ID = "2"  # Portfolio Disclosure
_MONTHLY_SUBCATEGORY = "Monthly Portfolio of Schemes"
_AES_KEY = b"6fa979f20126cb08aa645a8f495f6d85"
_AES_IV = b"I8zyA4lVhMCaJ5Kg"


def _decrypt_api_payload(payload) -> object:
    """Decode the AES-CBC envelope returned by the JM API.

    The browser bundle uses Latin-1 after decrypting, so mirror that detail
    here rather than assuming UTF-8.  A valid HTTP response with an invalid
    envelope is a schema/site failure, never an unavailable period.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        raise RuntimeError("JM Financial GetDownloadNew returned an unrecognizable response envelope")
    try:
        encrypted = base64.b64decode(payload["data"], validate=True)
        decrypted = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV).decrypt(encrypted)
        padding = decrypted[-1]
        if not 1 <= padding <= AES.block_size or decrypted[-padding:] != bytes([padding]) * padding:
            raise ValueError("invalid PKCS#7 padding")
        return json.loads(decrypted[:-padding].decode("latin1"))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise RuntimeError("JM Financial GetDownloadNew returned an invalid encrypted payload") from exc


def _records_from_payload(payload) -> list[dict]:
    records = _decrypt_api_payload(payload)
    if not isinstance(records, list):
        raise RuntimeError("JM Financial GetDownloadNew payload is not a record list")
    required = {"SubCategoryName", "Title", "FileName", "FileEXT"}
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not required.issubset(record):
            raise RuntimeError(
                f"JM Financial GetDownloadNew record {index} is missing one of "
                f"{', '.join(sorted(required))}"
            )
    return records


def _fetch_records(session) -> list[dict]:
    config = settings()
    endpoint = urljoin(API_URL.rstrip("/") + "/", "GetDownloadNew")
    try:
        payload = post_json(
            session,
            endpoint,
            json={"IICategoryID": _CATEGORY_ID, "IISubCategoryID": "0", "IVsearch": ""},
            headers={
                "Referer": PAGE_URL,
                "Origin": "https://www.jmfinancialmf.com",
                "Accept": "application/json, text/plain, */*",
            },
            phase="discovery",
            timeout=(config.connect_timeout, config.discovery_timeout),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"JM Financial discovery request failed: {endpoint}: {exc}") from exc
    return _records_from_payload(payload)


_YEAR_TYPO_RE = re.compile(r"\b(20\d{2})(\d)\b")


def _drop_duplicated_year_digit(url: str) -> str | None:
    """Undo a specific typo the JM API's catalog sometimes has: a year like
    "2018" followed by one extra digit that just repeats its own last digit
    ("20188"). Confirmed against the live file host -- some entries in the
    same monthly batch carry this typo and 404, while a sibling entry for
    the same month has the correct un-typo'd filename and downloads fine.
    """
    def _fix(match: re.Match) -> str:
        year, extra = match.group(1), match.group(2)
        return year if extra == year[-1] else match.group(0)

    fixed = _YEAR_TYPO_RE.sub(_fix, url)
    return fixed if fixed != url else None


def _resolve_url(session, url: str) -> ResolutionResult:
    """Return whichever of ``url`` or its typo-corrected form the file host
    actually serves. A broken filename here doesn't 404 -- the WAF answers
    200 with the site's own homepage HTML, so a candidate is only trusted
    once its Content-Type confirms an actual document.
    """
    candidates = [url]
    variant = _drop_duplicated_year_digit(url)
    if variant:
        candidates.append(variant)
    for candidate in candidates:
        response = None
        try:
            response = session.get(candidate, stream=True, timeout=(10, 30))
            content_type = response.headers.get("Content-Type", "")
            status = getattr(response, "status_code", None)
            if status == 200 and "html" not in content_type.lower():
                return ResolutionResult(url=candidate, content_type=content_type)
            if status == 200:
                result = ResolutionResult(url=candidate, status="html", reason="HTTP 200 response is HTML", status_code=status, content_type=content_type)
            elif status == 404:
                result = ResolutionResult(url=url, status="not_found", reason="file host returned HTTP 404", status_code=status, content_type=content_type)
            elif status is not None and status >= 500:
                result = ResolutionResult(url=url, status="http_error", reason=f"file host returned HTTP {status}", status_code=status, content_type=content_type)
            else:
                result = ResolutionResult(url=url, status="empty", reason=f"file host returned HTTP {status}", status_code=status, content_type=content_type)
            if result.status == "html":
                # Keep probing a typo-corrected sibling candidate; if none
                # resolves, the HTML record is retained by discovery.
                continue
        except Exception:
            result = ResolutionResult(url=url, status="transport", reason="file-host probe failed")
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
    return result


def _documents_from_records(records: list[dict], period: str, session):
    candidates = []
    unavailable = []
    for record in records:
        if record["SubCategoryName"].strip().casefold() != _MONTHLY_SUBCATEGORY.casefold():
            continue
        title = str(record["Title"]).strip()
        filename = str(record["FileName"]).strip()
        file_type = str(record["FileEXT"]).lstrip(".").lower()
        evidence = f"{title} {filename}"
        if period_conflicts(evidence, period) or not period_matches(evidence, period):
            continue
        if file_type not in {"xls", "xlsx", "xlsm"}:
            raise RuntimeError(
                f"JM Financial monthly catalog exposed unsupported file type {record['FileEXT']!r}"
            )
        url = urljoin(FILE_BASE_URL.rstrip("/") + "/", filename.lstrip("/"))
        resolution = _resolve_url(session, url)
        # A probe transport failure is not proof that a catalog record is
        # unavailable. Keep the record so the download phase can report the
        # actual transport failure; deterministic 404/empty/5xx results are
        # recorded as source-side gaps instead.
        if resolution.status in {"not_found", "empty"}:
            unavailable.append({
                "period": period,
                "title": title,
                "filename": Path(filename).name,
                "url": url,
                "download_id": record.get("DownloadID"),
                "status": resolution.status,
                "reason": resolution.reason,
            })
            continue
        if resolution.status == "http_error":
            # Keep a 500 record in the downloadable set: the download phase
            # owns bounded retry policy for transient server failures.
            unavailable.append({
                "period": period,
                "title": title,
                "filename": Path(filename).name,
                "url": url,
                "download_id": record.get("DownloadID"),
                "status": resolution.status,
                "reason": resolution.reason,
            })
        candidates.append((record, resolution, document_from_link(
                amc=AMC,
                period=period,
                source_page_url=PAGE_URL,
                link=resolution.url,
                label=title,
                filename=Path(filename).name,
                file_type=file_type,
                primary=True,
                metadata={
                    "download_id": record.get("DownloadID"),
                    "document_date": record.get("DocumentDate"),
                    "category": record.get("CategoryName"),
                    "subcategory": record.get("SubCategoryName"),
                    "resolution_status": resolution.status,
                },
            )))

    # The same title can occur more than once in a monthly catalog. Resolve
    # the group before creating the final document list so an HTML-only
    # duplicate is suppressed only when a sibling is a real document.
    groups: dict[tuple[str, str, str], list[tuple[dict, ResolutionResult, object]]] = {}
    for record, resolution, document in candidates:
        key = (period, re.sub(r"\s+", " ", str(record["Title"]).strip()).casefold(), record["SubCategoryName"].strip().casefold())
        groups.setdefault(key, []).append((record, resolution, document))

    documents = []
    rejected = []
    for group in groups.values():
        ordered = sorted(group, key=lambda entry: (entry[1].status != "resolved",))
        chosen = ordered[0]
        documents.append(chosen[2])
        if len(ordered) > 1:
            for duplicate, duplicate_resolution, _document in ordered[1:]:
                rejected.append({
                    "download_id": duplicate.get("DownloadID"),
                    "title": duplicate.get("Title"),
                    "reason": "equivalent catalog record; preferred a non-HTML sibling" if chosen[1].status == "resolved" else "equivalent duplicate; kept first HTML-only record",
                })
            # A deterministic error on an equivalent sibling is represented
            # by the rejected-duplicate note, not as an independent source
            # gap when a valid sibling won the group.
            if chosen[1].status == "resolved":
                group_ids = {entry[0].get("DownloadID") for entry in group}
                unavailable[:] = [entry for entry in unavailable if entry.get("download_id") not in group_ids]
    documents = only_period(dedupe_documents(documents), period)
    notes = {}
    if unavailable:
        notes["source_unavailable"] = unavailable
    if rejected:
        notes["rejected_duplicates"] = rejected
    return documents, notes


def discover(period: str, session=None):
    active_session = session or _browser_ua_session()
    documents, notes = _documents_from_records(_fetch_records(active_session), period, active_session)
    if not documents:
        if notes.get("source_unavailable"):
            return DiscoveryResult(documents=[], notes=notes)
        raise PeriodUnavailable(f"JM Financial publishes no monthly portfolio for {period}")
    return DiscoveryResult(documents=documents, notes=notes) if notes else documents


def _browser_ua_session():
    # The CMS file host sits behind an AppTrana WAF that answers 406 to any
    # User-Agent it does not recognise as a browser -- including the project's
    # default, whose trailing "portfolio-downloader/1.0" token is enough on its
    # own to trip it.  Both API discovery and downloads use this session.
    session = create_session()
    session.headers["User-Agent"] = os.getenv(
        "JM_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36",
    )
    return session


if __name__ == "__main__":
    raise SystemExit(
        run_cli(
            amc=AMC,
            discover=discover,
            description="Download JM Financial monthly portfolio schemes",
            session=_browser_ua_session(),
        )
    )

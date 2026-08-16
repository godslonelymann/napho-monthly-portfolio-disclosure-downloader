from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import PeriodUnavailable, dedupe_documents, document_from_link, only_period
from core.http import create_session, fetch_json
from core.periods import extract_periods


AMC = "quantum"
PAGE_URL = "https://www.quantumamc.com/portfolio/combined/-1/1/0/0"
API_URL = "https://www.quantumamc.com/ProductPortfolio/GetProductPortfolioPaginatedList"
ALL_FUNDS_SCHEME_ID = -1
MONTHLY_FREQUENCY = 1
MAX_PAGES = 100
ALLOWED_DOWNLOAD_HOST = "www.quantumamc.com"
ALLOWED_FILE_TYPES = {"xls", "xlsx"}


def _integer(value, *, field: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"Quantum API returned an invalid {field}: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Quantum API returned an invalid {field}: {value!r}") from exc


def _fetch_records(session, *, year: int, month: int) -> list[dict]:
    records: list[dict] = []
    page = 1

    while True:
        payload = fetch_json(
            session,
            API_URL,
            params={
                "productSchemeId": ALL_FUNDS_SCHEME_ID,
                "yearId": year,
                "monthId": month,
                "Frequency": MONTHLY_FREQUENCY,
                "pageIndex": page,
            },
            headers={"Referer": PAGE_URL, "Accept": "application/json"},
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"Quantum API page {page} returned {type(payload).__name__}, not an object")
        if payload.get("success") is not True:
            raise RuntimeError(f"Quantum API reported failure on page {page}: {payload!r}")

        batch = payload.get("objProductPortfolioList") or []
        if not isinstance(batch, list):
            raise RuntimeError(f"Quantum API page {page} returned a non-list portfolio collection")
        if any(not isinstance(record, dict) for record in batch):
            raise RuntimeError(f"Quantum API page {page} returned a malformed portfolio record")

        response_page = _integer(payload.get("pageIndex"), field="pageIndex")
        total_pages = _integer(payload.get("totalPageCount", 1), field="totalPageCount")
        if response_page != page:
            raise RuntimeError(f"Quantum API returned page {response_page} while page {page} was requested")
        if total_pages == 0 and page == 1 and not batch:
            return records
        if total_pages < 1 or total_pages > MAX_PAGES or page > total_pages:
            raise RuntimeError(f"Quantum API returned an invalid totalPageCount: {total_pages!r}")

        records.extend(batch)

        if page >= total_pages:
            return records
        page += 1


def _validated_file(record: dict, period: str) -> tuple[int, str, str]:
    fact_sheet_id = _integer(record.get("FactSheetId"), field="FactSheetId")
    scheme_id = _integer(record.get("SchemeId"), field="SchemeId")
    frequency = _integer(record.get("FactSheetFreq"), field="FactSheetFreq")
    if scheme_id != ALL_FUNDS_SCHEME_ID or frequency != MONTHLY_FREQUENCY:
        raise RuntimeError(
            f"Quantum API returned unexpected scheme/frequency for FactSheetId {fact_sheet_id}: "
            f"scheme={scheme_id}, frequency={frequency}"
        )

    reported_period = str(record.get("FSMonth") or "").strip()
    if extract_periods(reported_period) != {period}:
        raise RuntimeError(
            f"Quantum API returned the wrong period for FactSheetId {fact_sheet_id}: "
            f"expected {period}, got {reported_period!r}"
        )

    url = str(record.get("FileUrl") or "").strip()
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"Quantum API returned an invalid download URL: {url!r}") from exc
    if (
        parsed.scheme.lower() != "https"
        or (hostname or "").lower() != ALLOWED_DOWNLOAD_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise RuntimeError(f"Quantum API returned an unexpected download URL: {url!r}")

    api_file_type = str(record.get("FileExt") or "").strip().lower().lstrip(".")
    url_file_type = Path(parsed.path).suffix.lower().lstrip(".")
    file_type = api_file_type or url_file_type
    if file_type not in ALLOWED_FILE_TYPES or (url_file_type and url_file_type != file_type):
        raise RuntimeError(f"Quantum API returned an unsupported file type for {url!r}: {api_file_type!r}")
    return fact_sheet_id, url, file_type


def discover(period: str, session=None):
    year_text, month_text = period.split("-")
    active_session = session or create_session()
    records = _fetch_records(active_session, year=int(year_text), month=int(month_text))

    documents = []
    seen_fact_sheet_ids: set[int] = set()
    seen_urls: set[str] = set()
    for record in records:
        fact_sheet_id, url, file_type = _validated_file(record, period)
        if fact_sheet_id in seen_fact_sheet_ids or url in seen_urls:
            continue
        seen_fact_sheet_ids.add(fact_sheet_id)
        seen_urls.add(url)

        scheme_name = str(record.get("SchemeName") or "All Funds Portfolio").strip()
        documents.append(
            document_from_link(
                amc=AMC,
                period=period,
                source_page_url=PAGE_URL,
                link=url,
                label=str(record.get("FSMonth") or f"Quantum monthly portfolio {period}"),
                filename=f"quantum_all_funds_{period}_{fact_sheet_id}.{file_type}",
                file_type=file_type,
                scheme=scheme_name,
                primary=True,
                metadata={
                    "fact_sheet_id": fact_sheet_id,
                    "scheme_id": _integer(record.get("SchemeId"), field="SchemeId"),
                    "frequency": _integer(record.get("FactSheetFreq"), field="FactSheetFreq"),
                    "reported_period": record.get("FSMonth"),
                    "original_filename": record.get("OriginalFileName"),
                    "fact_sheet_date": record.get("FactSheetDate"),
                    "modified_on": record.get("ModifiedOn"),
                },
            )
        )

    documents = dedupe_documents(documents)
    if not documents:
        raise PeriodUnavailable(f"Quantum publishes no monthly all-funds portfolio for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(
        run_cli(
            amc=AMC,
            discover=discover,
            description="Download Quantum's monthly all-funds portfolio from its public API",
        )
    )

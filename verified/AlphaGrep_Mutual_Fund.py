from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urljoin

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import PeriodUnavailable, document_from_link, only_period
from core.http import fetch_json
from core.parsing import walk
from core.periods import extract_periods, period_matches

AMC = "alphagrep"
MANIFEST_URL = "https://www.alphagrepmf.ai/assets/documents/files.json"
DOCUMENT_ROOT = "https://www.alphagrepmf.ai/assets/documents/"


def _as_url(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return urljoin(MANIFEST_URL, value)
    return urljoin(DOCUMENT_ROOT, value)


def discover(period: str, session=None):
    payload = fetch_json(session, MANIFEST_URL)
    documents = []
    seen = set()
    seen_labels: list[str] = []

    # The current manifest is hierarchical and supplies extension-less file
    # names.  Preserve the folder names supplied by the server instead of
    # trying to infer the financial year from the requested month.
    for scheme in payload.get("monthly", []) if isinstance(payload, dict) else []:
        if not isinstance(scheme, dict):
            continue
        scheme_folder = str(scheme.get("folderName") or "").strip("/")
        for financial_year in scheme.get("financialYears", []):
            if not isinstance(financial_year, dict):
                continue
            year_folder = str(financial_year.get("yearFolder") or "").strip("/")
            for record in financial_year.get("documents", []):
                if not isinstance(record, dict):
                    continue
                filename = str(record.get("fileName") or "").strip()
                label = " ".join(str(record.get(key) or "") for key in ("title", "fileName"))
                if not filename:
                    continue
                seen_labels.append(label)
                if not period_matches(label, period):
                    continue
                if not filename.lower().endswith((".xls", ".xlsx", ".xlsm")):
                    filename += ".xls"
                relative = "/".join(part for part in (scheme_folder, "monthly", year_folder, filename) if part)
                url = _as_url(relative)
                if url in seen:
                    continue
                seen.add(url)
                documents.append(
                    document_from_link(
                        amc=AMC,
                        period=period,
                        source_page_url=MANIFEST_URL,
                        link=url,
                        label=label,
                        metadata={"manifest_path": relative},
                    )
                )

    # Backward compatibility for the previous flat manifest schema.
    for value in walk(payload):
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value.lower().split("?", 1)[0].endswith((".xls", ".xlsx", ".xlsm")):
            continue
        seen_labels.append(value)
        url = _as_url(value)
        if url in seen or not period_matches(value, period):
            continue
        seen.add(url)
        documents.append(
            document_from_link(
                amc=AMC,
                period=period,
                source_page_url=MANIFEST_URL,
                link=url,
                label=value,
                metadata={"manifest_path": value},
            )
        )
    if not documents:
        # The manifest currently appears to list only whichever month(s) are
        # newest -- check what periods it actually names before assuming the
        # adapter itself is broken, so "not published yet/any more" reads
        # differently from "the manifest schema changed".
        published: set[str] = set()
        for label in seen_labels:
            published |= extract_periods(label)
        if published and period not in published:
            raise PeriodUnavailable(
                f"AlphaGrep's files.json currently only lists {', '.join(sorted(published))}; {period} is not among them"
            )
        raise RuntimeError(f"files.json contains no AlphaGrep workbook for {period}")
    return only_period(documents, period, required=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download AlphaGrep monthly portfolio disclosures"))

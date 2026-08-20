from __future__ import annotations

import sys
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import DiscoveryResult, ResolutionResult, document_from_link, dedupe_documents, only_period
from core.http import create_session, post_json
from core.periods import period_conflicts, period_matches

AMC = "pgim"
PAGE_URL = "https://www.pgimindia.com/mutual-funds/disclosures/Portfolios/Monthly-Portfolio"
API_URL = "https://www.pgimindia.com/api/v1/brochure/published/disclosure"
PAYLOAD = {"headerId": 2, "sectionId": "SECTION_747960037", "source": "W", "branchCode": None}


_PGIM_MAX_CANDIDATES = 40
_MONTHS = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april",
    "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept",
    "september", "oct", "october", "nov", "november", "dec", "december",
}
_ACRONYMS = ("ELSS", "PSU", "IBX", "FOF")


def _url_casing_variants(url: str) -> list[str]:
    """Alternate filenames for PGIM's file host, which is case-sensitive.

    The disclosure API's own ``pdfPath`` sometimes doesn't match the filename
    actually stored on the host -- e.g. "...fund Aug 2021.xlsb" (API) vs
    "...fund aug 2021.xlsb" (stored). Observed mismatches: stray capitals,
    an "&" the API includes but the stored file doesn't
    ("banking-&-psu-debt-fund" vs "banking-psu-debt-fund"), and "sep"/"sept"
    abbreviation swaps.
    """
    parts = urlsplit(url)
    path_prefix, _, encoded_name = parts.path.rpartition("/")
    original_name = unquote(encoded_name)
    # Keep the API URL as the first candidate, byte-for-byte. Every alternate
    # candidate below starts from the decoded filename and is quoted exactly
    # once when the URL is rebuilt.
    candidates = [url]
    seen = {url}

    def add_name(name: str) -> None:
        if len(candidates) >= _PGIM_MAX_CANDIDATES:
            return
        candidate_path = f"{path_prefix}/{quote(name, safe='._-')}"
        candidate = urlunsplit((parts.scheme, parts.netloc, candidate_path, parts.query, parts.fragment))
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    def replace_months(name: str, form: str) -> str:
        def repl(match: re.Match) -> str:
            token = match.group(0)
            if token.casefold() not in _MONTHS:
                return token
            return form(token)

        return re.sub(r"(?<![A-Za-z])(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![A-Za-z])", repl, name, flags=re.IGNORECASE)

    def normalize_tokens(name: str, *, upper: bool = False) -> str:
        normalized = name
        for acronym in _ACRONYMS:
            normalized = re.sub(rf"(?<![A-Za-z]){acronym}(?![A-Za-z])", acronym if upper else acronym.title(), normalized, flags=re.IGNORECASE)
        return normalized

    stem, dot, extension = original_name.rpartition(".")
    if not dot:
        stem, extension = original_name, ""
    lower = stem.lower()
    title = stem.title()
    base_names: list[str] = []
    base_seen: set[str] = set()

    def add_base(name: str) -> None:
        if name not in base_seen and len(base_names) < 12:
            base_seen.add(name)
            base_names.append(name)

    bases = [
        stem,
        lower,
        title,
        stem.replace("-&-", "-"),
        lower.replace("-&-", "-"),
        title.replace("-&-", "-"),
    ]
    for base in bases:
        add_base(base)
        add_base(normalize_tokens(base))
        add_base(normalize_tokens(base, upper=True))
        add_base(replace_months(base, lambda _: _.lower()))
        add_base(replace_months(base, lambda _: _.title()))
        add_base(replace_months(base, lambda _: _.upper()))
        for old, new in (("sep", "sept"), ("Sep", "Sept"), ("SEP", "SEPT"), ("sept", "sep"), ("Sept", "Sep"), ("SEPT", "SEP")):
            swapped = re.sub(rf"(?<![A-Za-z]){old}(?![A-Za-z])", new, base)
            if swapped != base:
                add_base(swapped)

    # Extension changes are applied to the already-small, deterministic set
    # of filename forms; this catches e.g. lower-case month + .xlsx without a
    # Cartesian product over every word in the filename.
    for base in base_names:
        for replacement in (extension, "xlsb", "xlsx", "xls"):
            if replacement:
                add_name(f"{base}.{replacement}")
    return candidates[:_PGIM_MAX_CANDIDATES]


def _resolve_url(session, url: str) -> ResolutionResult:
    """Return whichever casing of ``url`` the file host actually serves.

    A mismatched filename comes back as HTTP 204 (empty body), not a 404, so
    a candidate is confirmed by status code rather than by catching an
    error. Falls back to the original URL, unresolved, if nothing matches --
    the download step's own validation still reports that clearly.
    """
    timeout = (10, 30)
    last = ResolutionResult(url=url, status="not_found", reason="no candidate returned HTTP 200")
    for candidate in _url_casing_variants(url):
        response = None
        try:
            response = session.head(candidate, timeout=timeout, allow_redirects=True)
            status = getattr(response, "status_code", None)
            if status == 200:
                return ResolutionResult(url=candidate)
            if status == 204:
                last = ResolutionResult(url=url, status="empty", reason=f"candidate returned HTTP {status}", status_code=status)
            elif status is not None and status >= 500:
                last = ResolutionResult(url=url, status="http_error", reason=f"candidate returned HTTP {status}", status_code=status)
            elif status is not None:
                last = ResolutionResult(url=url, status="not_found", reason=f"candidate returned HTTP {status}", status_code=status)
        except Exception:
            last = ResolutionResult(url=url, status="transport", reason="candidate HEAD request failed")
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
    return last


def discover(period: str, session=None):
    session = session or create_session()
    payload = post_json(session, API_URL, json=PAYLOAD)
    documents = []
    unavailable = []
    for group in payload.get("data", []):
        for record in group.get("content", []):
            url = record.get("pdfPath")
            if not url:
                continue
            # The record's own "month"/"year" fields are reliable here (unlike
            # ITI/Trust, where those track the upload date) but dateMonthYear
            # and the filename both carry the same disclosure date, so use all
            # three as corroborating evidence rather than trusting one field.
            evidence = " ".join(str(record.get(key, "")) for key in ("title", "dateMonthYear", "pdfPath"))
            if period_conflicts(evidence, period) or not period_matches(evidence, period):
                continue
            resolution = _resolve_url(session, url)
            if not resolution.resolved:
                if resolution.status in {"not_found", "empty"}:
                    unavailable.append({
                        "period": period,
                        "title": str(record.get("title", "")),
                        "filename": unquote(urlsplit(url).path.rpartition("/")[2]),
                        "url": url,
                        "status": resolution.status,
                        "reason": resolution.reason,
                    })
                    continue
                raise RuntimeError(f"PGIM URL resolution failed for {url}: {resolution.reason}")
            documents.append(document_from_link(amc=AMC, period=period, source_page_url=PAGE_URL, link=resolution.url, label=str(record.get("title", "")), primary=True))
    documents = only_period(dedupe_documents(documents), period)
    if unavailable:
        return DiscoveryResult(documents=documents, notes={"source_unavailable": unavailable})
    if not documents:
        raise RuntimeError(f"PGIM disclosure manifest has no workbook for {period}")
    return documents


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download PGIM India monthly portfolio disclosures"))

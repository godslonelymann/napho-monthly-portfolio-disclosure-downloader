from __future__ import annotations

import html
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import run_cli
from core.discovery import PeriodUnavailable, document_from_link
from core.http import fetch_json, fetch_text

AMC = "aditya_birla"
PAGE_URL = "https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio"
BASE_URL = "https://mutualfund.adityabirlacapital.com"

MONTHS = {
    "january": 1,
    "janaury": 1,  # Historical typo on the website
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


class AccordionParser(HTMLParser):
    """Find the endpoint belonging to the Monthly Portfolio accordion."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.candidates: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.depth += 1

        endpoint = attrs.get("data-accordian-api")
        if tag == "li" and endpoint:
            self.candidates.append(
                {
                    "depth": self.depth,
                    "endpoint": html.unescape(endpoint),
                    "text": [],
                }
            )

    def handle_data(self, data):
        for candidate in self.candidates:
            if self.depth >= candidate["depth"]:
                candidate["text"].append(data)

    def handle_endtag(self, tag):
        self.depth = max(0, self.depth - 1)

    def monthly_endpoint(self) -> str:
        for candidate in self.candidates:
            text = " ".join(candidate["text"])
            if re.search(r"\bmonthly\s+portfolio\b", text, re.I):
                return candidate["endpoint"]
        raise RuntimeError("Monthly Portfolio endpoint was not found; page structure changed")


def discover_endpoint(session) -> str:
    parser = AccordionParser()
    parser.feed(fetch_text(session, PAGE_URL))

    endpoint = urljoin(BASE_URL, parser.monthly_endpoint())
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}month=%20&year=0"


def fetch_disclosures(session, endpoint: str) -> list[dict]:
    payload = fetch_json(
        session,
        endpoint,
        headers={
            "Referer": PAGE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    if str(payload.get("ReturnCode")) != "1":
        raise RuntimeError(f"Disclosure endpoint failed: {payload.get('ReturnCode')!r} {payload.get('ReturnMsg')!r}")

    records = payload.get("AccordionList")
    if not isinstance(records, list):
        raise RuntimeError("AccordionList is missing or is not a list")

    return records


def parse_period(label: str) -> str | None:
    # Excludes April 2020 ad-hoc updates because they do not begin with
    # "Monthly Portfolio".
    if not re.match(r"^\s*Monthly\s+Portfolios?\b", label, re.I):
        return None

    match = re.search(
        r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b",
        label,
        re.I,
    )
    if not match:
        return None

    month = MONTHS[match.group(1).lower()]
    day = int(match.group(2))
    year = int(match.group(3))

    try:
        date = datetime(year, month, day)
    except ValueError:
        return None

    return date.strftime("%Y-%m")


def normalize_download_url(url: str) -> str:
    parsed = urlsplit(url)
    if "/media/bsl/" in parsed.path.lower():
        return urljoin(BASE_URL, parsed.path)
    return url


def _zip_filename(url: str) -> str:
    name = unquote(Path(urlsplit(url).path).name)
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    if not name:
        name = "portfolio.zip"
    if not name.lower().endswith(".zip"):
        name += ".zip"
    return name


def discover(period: str, session=None):
    endpoint = discover_endpoint(session)
    records = fetch_disclosures(session, endpoint)

    for record in records:
        if not isinstance(record, dict):
            continue
        label = str(record.get("ResourceLink") or "").strip()
        if parse_period(label) != period:
            continue
        original_url = str(record.get("pdfUrl") or "").strip()
        if not original_url:
            continue
        # AccordionList is newest-first, so the first record whose label
        # parses to `period` is the one to use -- matches the original
        # "keep the first listing for a period" behavior exactly.
        url = normalize_download_url(original_url)
        return [
            document_from_link(
                amc=AMC,
                period=period,
                source_page_url=PAGE_URL,
                link=url,
                label=label,
                filename=_zip_filename(url),
                file_type="zip",
                scheme="consolidated",
                primary=True,
            )
        ]

    raise PeriodUnavailable(f"Aditya Birla Sun Life lists no monthly portfolio for {period}")


if __name__ == "__main__":
    raise SystemExit(run_cli(amc=AMC, discover=discover, description="Download Aditya Birla Sun Life monthly portfolio ZIP"))

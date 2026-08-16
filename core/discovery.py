"""Document records and strict discovery result handling."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from .parsing import Link


class PeriodUnavailable(RuntimeError):
    """The AMC's site structurally does not publish the requested period.

    Distinct from a plain RuntimeError (which signals the adapter itself is
    broken): this means discovery worked correctly and established that the
    period simply isn't published anywhere reachable, so callers can report
    "not published" separately from "this downloader needs fixing".
    """


# A real document link is a short path/query, not free text.  The Bank of
# India adapter once fed an entire unparsed JSON response through this
# function as if it were a single URL, which urljoin() happily accepted and
# which then blew up downstream as a 414 Request-URI Too Large.  Reject
# anything that couldn't plausibly be a URL up front instead.
_MAX_URL_LENGTH = 2000
_SUSPICIOUS_URL_RE = re.compile(r'[{}\r\n]')


@dataclass(frozen=True)
class Document:
    amc: str
    period: str
    url: str
    source_page_url: str
    label: str = ""
    filename: str = ""
    file_type: str = ""
    scheme: str | None = None
    primary: bool = False
    metadata: dict = field(default_factory=dict)

    @property
    def evidence(self) -> str:
        return " ".join(part for part in (self.label, self.filename, self.url) if part)


def _filename(url: str) -> str:
    name = unquote(Path(urlsplit(url).path).name)
    return re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .") or "portfolio"


def document_from_link(
    *,
    amc: str,
    period: str,
    source_page_url: str,
    link: Link | str,
    label: str | None = None,
    filename: str | None = None,
    file_type: str | None = None,
    scheme: str | None = None,
    primary: bool = False,
    metadata: dict | None = None,
) -> Document:
    if isinstance(link, Link):
        url = link.href
        default_label = link.searchable or link.text
    else:
        url = str(link)
        default_label = url
    url = urljoin(source_page_url, url)
    if len(url) > _MAX_URL_LENGTH or _SUSPICIOUS_URL_RE.search(url):
        raise ValueError(
            f"Refusing to treat this as a document URL -- looks like unparsed JSON/text, not a link: {url[:200]}..."
        )
    clean_name = filename or _filename(url)
    suffix = (file_type or Path(clean_name).suffix.lstrip(".") or Path(urlsplit(url).path).suffix.lstrip(".")).lower()
    return Document(
        amc=amc,
        period=period,
        url=url,
        source_page_url=source_page_url,
        label=(label or default_label).strip(),
        filename=clean_name,
        file_type=suffix,
        scheme=scheme,
        primary=primary,
        metadata=dict(metadata or {}),
    )


def dedupe_documents(documents: list[Document]) -> list[Document]:
    by_url: dict[str, Document] = {}
    for document in documents:
        current = by_url.get(document.url)
        if current is None or (document.primary and not current.primary):
            by_url[document.url] = document
    return list(by_url.values())


def only_period(documents: list[Document], period: str, *, required: bool = False) -> list[Document]:
    """Return documents for ``period``.

    Most adapters follow this with an AMC-specific empty-result error, so an
    empty selection is returned by default.  Adapters that return this helper
    directly opt into strict handling with ``required=True``.
    """
    selected = [document for document in documents if document.period == period]
    if required and not selected:
        raise RuntimeError(f"Discovery returned no documents for {period}")
    return selected


@dataclass
class DiscoveryResult:
    """Wraps a ``discover()`` return value when the adapter has extra context
    worth recording alongside the documents themselves -- e.g. Bandhan's own
    per-scheme found/not_published/error verdicts, which say something real
    ("2 of 80 schemes confirmed not published this month") that a bare
    document list can't express.

    Most adapters just return ``list[Document]``; ``run_cli`` accepts either
    form, so this is opt-in and every other script is unaffected.
    """

    documents: list[Document]
    notes: dict = field(default_factory=dict)

"""The expected file set for one AMC/period, recorded right after discovery.

`discover()` already knows everything an AMC's site offered for a period --
every URL, filename, and (where the adapter tracks it) scheme name. Nothing
currently writes that down before downloading starts, so there is no record
to compare a download run against afterwards. This module turns a
`discover()` result into a durable `ExpectationSet` and persists it next to
the files it describes, so a later validation pass has something concrete
to check "what we got" against.

Building this from `Document` objects only -- no network, no filesystem
access to the downloads themselves -- keeps it usable from any adapter and
from tests without a live site or a download step.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .discovery import Document

# Query parameters that sites append purely for cache-busting or CMS
# bookkeeping (upload ids, "?sfvrsn=", "?v="). Stripping only *these* -- and
# nothing else -- keeps identity stable across reruns without collapsing
# cases like NJ Mutual Fund, where "?file=Scheme-A.xlsx" *is* the identity
# (see core.cli._safe_name's own comment on the same trap).
_NOISE_QUERY_KEYS = {"sfvrsn", "v", "ver", "version", "cb", "cache", "_", "t", "ts", "timestamp", "rand", "nocache"}

_EXPECTED_FILENAME = ".expected.json"


def normalized_url(url: str) -> str:
    """A stable form of `url` for identity comparison.

    Lowercases scheme/host (case-insensitive per RFC), drops the fragment
    (never sent to the server), strips known noise query params, and sorts
    the rest so param order doesn't matter.
    """
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _NOISE_QUERY_KEYS
    ]
    query.sort()
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def destination_filename(document: Document) -> str:
    """The filename this document would be saved under.

    Mirrors core.cli._safe_name exactly (same rules, same reasoning: the
    adapter-supplied filename must win over the URL's path basename, since
    some AMCs -- NJ Mutual Fund -- put the real filename in a query string
    behind a generic path like viewfile.php). Duplicated rather than
    imported to keep this module import-light and usable before a download
    session or output directory exists; if the two ever drift, the
    filename-collision tests in both modules will catch it.
    """
    name = document.filename or unquote(Path(urlsplit(document.url).path).name) or "portfolio"
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    if not Path(name).suffix and document.file_type:
        name += "." + document.file_type
    return name or "portfolio"


def identity_key(document: Document) -> str:
    """The strongest available identity for `document`.

    A scheme name (when the adapter tracks one) survives the AMC renaming
    its files between runs, so it wins whenever available. Falling back to
    the normalized URL covers adapters that discover one file with no
    scheme label at all. Deliberately period-scoped so the same scheme in
    two different months never collides.
    """
    if document.scheme:
        return f"scheme:{document.period}:{document.scheme.strip().lower()}"
    return f"url:{normalized_url(document.url)}"


@dataclass(frozen=True)
class ExpectedFile:
    key: str
    url: str
    filename: str
    scheme: str | None
    label: str
    source_page_url: str
    kind: str  # "file" | "archive" -- a pre-download guess; validation confirms it
    metadata: dict = field(default_factory=dict)
    # Other raw document URLs that collapsed into this one entry (repeated
    # links to the exact same file). Kept for audit, not treated as a
    # problem on its own.
    duplicate_urls: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "url": self.url,
            "filename": self.filename,
            "scheme": self.scheme,
            "label": self.label,
            "source_page_url": self.source_page_url,
            "kind": self.kind,
            "metadata": self.metadata,
            "duplicate_urls": list(self.duplicate_urls),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExpectedFile":
        return cls(
            key=data["key"],
            url=data["url"],
            filename=data["filename"],
            scheme=data.get("scheme"),
            label=data.get("label", ""),
            source_page_url=data.get("source_page_url", ""),
            kind=data.get("kind", "file"),
            metadata=data.get("metadata", {}) or {},
            duplicate_urls=tuple(data.get("duplicate_urls", []) or []),
        )


@dataclass
class DuplicateReport:
    """Collision cases spotted at discovery time, before anything downloads."""

    # Same URL discovered more than once (e.g. two <a> tags to one file).
    # Harmless: collapsed into a single ExpectedFile automatically.
    repeated_url: list[dict] = field(default_factory=list)
    # Same scheme identity, but two different URLs -- the site is exposing
    # the same scheme's file twice under different links. Needs a human to
    # confirm which one is authoritative; the first (or primary) is kept.
    duplicate_listing: list[dict] = field(default_factory=list)
    # Two different expected identities would save to the same destination
    # filename and silently overwrite each other on disk.
    filename_collision: list[dict] = field(default_factory=list)
    # Same URL, but discovery produced inconsistent filenames/labels for it.
    url_filename_conflict: list[dict] = field(default_factory=list)

    @property
    def has_blocking_issues(self) -> bool:
        return bool(self.filename_collision)

    def to_dict(self) -> dict:
        return {
            "repeated_url": self.repeated_url,
            "duplicate_listing": self.duplicate_listing,
            "filename_collision": self.filename_collision,
            "url_filename_conflict": self.url_filename_conflict,
        }


def detect_duplicates(documents: list[Document]) -> DuplicateReport:
    report = DuplicateReport()

    by_url: dict[str, list[Document]] = {}
    for document in documents:
        by_url.setdefault(document.url, []).append(document)
    for url, group in by_url.items():
        if len(group) < 2:
            continue
        filenames = {destination_filename(document) for document in group}
        if len(filenames) > 1:
            report.url_filename_conflict.append({"url": url, "filenames": sorted(filenames)})
        else:
            report.repeated_url.append({"url": url, "count": len(group)})

    by_identity: dict[str, list[Document]] = {}
    for document in documents:
        by_identity.setdefault(identity_key(document), []).append(document)
    for key, group in by_identity.items():
        urls = sorted({document.url for document in group})
        if len(urls) > 1:
            report.duplicate_listing.append({"key": key, "urls": urls})

    by_destination: dict[str, set[str]] = {}
    for document in documents:
        by_destination.setdefault(destination_filename(document), set()).add(identity_key(document))
    for filename, keys in by_destination.items():
        if len(keys) > 1:
            report.filename_collision.append({"filename": filename, "keys": sorted(keys)})

    return report


@dataclass
class ExpectationSet:
    amc: str
    period: str
    discovered_at: str
    source_pages: tuple[str, ...]
    items: tuple[ExpectedFile, ...]
    duplicates: DuplicateReport = field(default_factory=DuplicateReport)
    discovery_notes: dict = field(default_factory=dict)
    truncated_by_max_files: bool = False

    @property
    def count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict:
        return {
            "amc": self.amc,
            "period": self.period,
            "discovered_at": self.discovered_at,
            "source_pages": list(self.source_pages),
            "items": [item.to_dict() for item in self.items],
            "duplicates": self.duplicates.to_dict(),
            "discovery_notes": self.discovery_notes,
            "truncated_by_max_files": self.truncated_by_max_files,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExpectationSet":
        duplicates_data = data.get("duplicates", {}) or {}
        return cls(
            amc=data["amc"],
            period=data["period"],
            discovered_at=data.get("discovered_at", ""),
            source_pages=tuple(data.get("source_pages", []) or []),
            items=tuple(ExpectedFile.from_dict(item) for item in data.get("items", [])),
            duplicates=DuplicateReport(
                repeated_url=duplicates_data.get("repeated_url", []),
                duplicate_listing=duplicates_data.get("duplicate_listing", []),
                filename_collision=duplicates_data.get("filename_collision", []),
                url_filename_conflict=duplicates_data.get("url_filename_conflict", []),
            ),
            discovery_notes=data.get("discovery_notes", {}) or {},
            truncated_by_max_files=bool(data.get("truncated_by_max_files", False)),
        )


def from_documents(
    documents: list[Document],
    *,
    discovery_notes: dict | None = None,
    truncated_by_max_files: bool = False,
) -> ExpectationSet:
    """Build the expected set discovery just produced.

    Requires at least one document -- an adapter with zero results should
    already have raised (PeriodUnavailable for "genuinely nothing published",
    RuntimeError otherwise); a silent empty ExpectationSet would make
    "unavailable" indistinguishable from "discovery quietly returned
    nothing", which is exactly the failure mode this system exists to catch.
    """
    if not documents:
        raise ValueError("Refusing to build an ExpectationSet from zero documents")

    amcs = {document.amc for document in documents}
    periods = {document.period for document in documents}
    if len(amcs) > 1:
        raise ValueError(f"Documents span multiple AMCs: {sorted(amcs)}")
    if len(periods) > 1:
        raise ValueError(f"Documents span multiple periods: {sorted(periods)}")

    duplicates = detect_duplicates(documents)

    winners: dict[str, Document] = {}
    duplicate_urls_by_key: dict[str, list[str]] = {}
    for document in documents:
        key = identity_key(document)
        current = winners.get(key)
        duplicate_urls_by_key.setdefault(key, [])
        if current is None:
            winners[key] = document
        elif document.url != current.url:
            # A genuine duplicate-listing case (recorded above too): keep
            # the primary document if one is marked primary, otherwise the
            # first one discovery produced.
            duplicate_urls_by_key[key].append(document.url)
            if document.primary and not current.primary:
                winners[key] = document
        # else: identical URL seen again -- nothing new to record.

    items = []
    for key, document in winners.items():
        items.append(
            ExpectedFile(
                key=key,
                url=document.url,
                filename=destination_filename(document),
                scheme=document.scheme,
                label=document.label,
                source_page_url=document.source_page_url,
                kind="archive" if document.file_type.lower() == "zip" else "file",
                metadata=dict(document.metadata),
                duplicate_urls=tuple(sorted(duplicate_urls_by_key.get(key, []))),
            )
        )
    items.sort(key=lambda item: item.key)

    return ExpectationSet(
        amc=next(iter(amcs)),
        period=next(iter(periods)),
        discovered_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        source_pages=tuple(sorted({document.source_page_url for document in documents})),
        items=tuple(items),
        duplicates=duplicates,
        discovery_notes=discovery_notes or {},
        truncated_by_max_files=truncated_by_max_files,
    )


def expectations_path(output_root: Path) -> Path:
    return output_root / _EXPECTED_FILENAME


def write_expectations(output_root: Path, expectations: ExpectationSet) -> Path:
    path = expectations_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(expectations.to_dict(), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    return path


def load_expectations(output_root: Path) -> ExpectationSet | None:
    path = expectations_path(output_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return ExpectationSet.from_dict(data)

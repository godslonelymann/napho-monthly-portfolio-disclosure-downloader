"""Reconcile an ExpectationSet against what actually landed on disk.

Three views of the same period directory are compared, not two:

  E (expected)  -- core.expectations.ExpectationSet, built at discovery time
  M (manifest)  -- core.cli's manifest.json, written by the download step
  D (disk)      -- whatever files are actually sitting in the directory

"Downloaded == Discovered" alone is never enough: a re-run that overwrote 79
of 80 files with one stale leftover would count 80/80 by number while still
being wrong. Every expected item is checked by identity (its URL, or a
duplicate URL that collapsed into it), then the manifest's own claim about
that file is re-verified against the live bytes on disk -- size, hash, and a
cheap magic-byte / HTML-error-page sniff -- rather than trusted at face
value.

This module only looks at what is already on disk; it does not download
anything and is not wired into core.cli yet.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .expectations import ExpectationSet, ExpectedFile

_VALIDATION_FILENAME = ".validation.json"
_RESERVED_NAMES = {"manifest.json", ".expected.json", ".validation.json"}

# Suffixes whose files must never actually be an HTML page -- the "site
# returned its error page but we saved it with a .xlsx extension anyway"
# case. csv/pdf are left out: some sites legitimately serve pdf portfolios,
# and a stray "<" is not unusual in CSV free text.
_NON_HTML_SUFFIXES = {"xlsx", "xls", "xlsm", "xlsb", "zip"}
_HTML_MARKERS = (b"<!doctype html", b"<html")

STATUS_SUCCESS = "SUCCESS"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
STATUS_CORRUPT = "CORRUPT"
STATUS_PARTIAL_BY_CONFIG = "PARTIAL_BY_CONFIG"
# Set only by core.cli.run_cli's optional re-discovery pass (rediscoverable=
# True): every missing item was independently confirmed gone from a fresh,
# live discovery pass run after the download attempt -- the site changed
# mid-run, not our download that failed. validate() itself never sets this;
# it has no way to re-run discovery on its own.
STATUS_SITE_CHANGED = "SITE_CHANGED"
STATUS_UPSTREAM_GAP = "UPSTREAM_GAP"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _looks_like_html(path: Path) -> bool:
    head = path.read_bytes()[:512].lstrip().lower()
    return any(head.startswith(marker) for marker in _HTML_MARKERS)


def _display_name(item: ExpectedFile) -> str:
    return item.scheme or item.label or item.filename


@dataclass
class ItemOutcome:
    item: ExpectedFile
    status: str  # "ok" | "missing" | "corrupt"
    matched_url: str | None = None
    paths: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "key": self.item.key,
            "name": _display_name(self.item),
            "url": self.item.url,
            "status": self.status,
            "matched_url": self.matched_url,
            "paths": list(self.paths),
            "reasons": list(self.reasons),
        }


@dataclass
class ValidationReport:
    amc: str
    period: str
    discovered: int
    outcomes: tuple[ItemOutcome, ...]
    unexpected: tuple[str, ...] = ()
    stale: tuple[dict, ...] = ()
    duplicate_content: tuple[dict, ...] = ()
    source_unavailable: tuple[dict, ...] = ()
    truncated_by_max_files: bool = False
    # Set post-hoc by core.cli.run_cli after an optional re-discovery pass
    # (see STATUS_SITE_CHANGED above) -- never set by validate() itself.
    site_changed: bool = False

    @property
    def ok(self) -> tuple[ItemOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.status == "ok")

    @property
    def missing(self) -> tuple[ItemOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.status == "missing")

    @property
    def corrupt(self) -> tuple[ItemOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.status == "corrupt")

    @property
    def downloaded(self) -> int:
        return len(self.ok)

    @property
    def status(self) -> str:
        if self.truncated_by_max_files:
            return STATUS_PARTIAL_BY_CONFIG
        if self.source_unavailable:
            return STATUS_UPSTREAM_GAP
        if not self.missing and not self.corrupt:
            return STATUS_SUCCESS
        # site_changed only ever gets set when every missing item -- not
        # some of them -- was independently confirmed gone from a fresh
        # discovery pass; a corrupt item is never a "site changed" story
        # (something WAS downloaded, it just failed integrity), so that
        # still takes priority below.
        if self.site_changed and not self.corrupt:
            return STATUS_SITE_CHANGED
        # Every expected item was at least attempted (none outright missing)
        # but some failed integrity -- distinct from nothing having been
        # attempted at all, which is DOWNLOAD_FAILED below.
        if not self.missing and self.corrupt:
            return STATUS_CORRUPT
        if self.downloaded == 0 and not self.corrupt:
            return STATUS_DOWNLOAD_FAILED
        return STATUS_INCOMPLETE

    def to_dict(self) -> dict:
        return {
            "amc": self.amc,
            "period": self.period,
            "discovered": self.discovered,
            "downloaded": self.downloaded,
            "missing": len(self.missing),
            "corrupt": len(self.corrupt),
            "duplicates": len(self.duplicate_content),
            "unexpected": len(self.unexpected),
            "status": self.status,
            "truncated_by_max_files": self.truncated_by_max_files,
            "site_changed": self.site_changed,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "unexpected_files": list(self.unexpected),
            "stale_manifest_entries": list(self.stale),
            "duplicate_content": list(self.duplicate_content),
            "source_unavailable": list(self.source_unavailable),
        }

    def render(self) -> str:
        lines = [
            f"AMC: {self.amc}",
            f"Period: {self.period}",
            "",
            f"Discovered: {self.discovered}",
            f"Downloaded: {self.downloaded}",
            f"Missing: {len(self.missing)}",
            f"Corrupt: {len(self.corrupt)}",
            f"Duplicates: {len(self.duplicate_content)}",
            f"Unexpected: {len(self.unexpected)}",
            "",
        ]
        if self.missing:
            lines.append("Missing:")
            lines.extend(f"- {_display_name(outcome.item)}" for outcome in self.missing)
            lines.append("")
        if self.corrupt:
            lines.append("Corrupt:")
            for outcome in self.corrupt:
                reason = "; ".join(outcome.reasons) or "failed integrity check"
                lines.append(f"- {_display_name(outcome.item)} ({reason})")
            lines.append("")
        if self.unexpected:
            lines.append("Unexpected:")
            lines.extend(f"- {path}" for path in self.unexpected)
            lines.append("")
        if self.source_unavailable:
            lines.append("Source unavailable:")
            for entry in self.source_unavailable:
                name = entry.get("filename") or entry.get("title") or entry.get("url") or "<unknown>"
                reason = entry.get("reason") or entry.get("status") or "candidate URLs exhausted"
                lines.append(f"- {name} ({reason})")
            lines.append("")
        if self.site_changed:
            lines.append(
                "Note: every missing file above was independently confirmed absent "
                "from a fresh discovery pass -- the site changed mid-run, not the download."
            )
            lines.append("")
        lines.append(f"Status: {self.status}")
        return "\n".join(lines)


def _read_manifest(output_root: Path) -> dict:
    path = output_root / "manifest.json"
    if not path.exists():
        return {"downloads": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"downloads": {}}
    return data if isinstance(data, dict) else {"downloads": {}}


def _check_single_file(entry: dict, output_root: Path) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Returns (status, paths, reasons) for a non-archive manifest entry."""
    relative = entry.get("path")
    if not relative:
        return "corrupt", (), ("manifest entry has no path",)
    path = output_root / relative
    if not path.is_file():
        return "missing", (), ("manifest records a download but the file is gone from disk",)

    reasons = []
    size = path.stat().st_size
    if size == 0:
        reasons.append("zero-byte file")
    expected_bytes = entry.get("bytes")
    if expected_bytes is not None and size != expected_bytes:
        reasons.append(f"size on disk ({size}) does not match manifest ({expected_bytes})")
    expected_sha256 = entry.get("sha256")
    if size and expected_sha256 and _sha256(path) != expected_sha256:
        reasons.append("sha256 on disk does not match manifest -- file was modified or overwritten")
    if size and path.suffix.lstrip(".").lower() in _NON_HTML_SUFFIXES and _looks_like_html(path):
        reasons.append("file content looks like an HTML page, not the expected file type")

    if reasons:
        return "corrupt", (relative,), tuple(reasons)
    return "ok", (relative,), ()


def _check_archive(entry: dict, output_root: Path) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Returns (status, paths, reasons) for an archive manifest entry."""
    extracted = entry.get("extracted") or []
    if not extracted:
        return "corrupt", (), ("archive extracted zero usable files",)

    reasons = []
    paths = []
    for member in extracted:
        relative = member.get("path")
        path = output_root / relative if relative else None
        if not relative or not path.is_file():
            reasons.append(f"extracted member missing from disk: {relative or '<unknown>'}")
            continue
        paths.append(relative)
        size = path.stat().st_size
        if size == 0:
            reasons.append(f"{relative}: zero-byte file")
            continue
        expected_sha256 = member.get("sha256")
        if expected_sha256 and _sha256(path) != expected_sha256:
            reasons.append(f"{relative}: sha256 on disk does not match manifest")

    if reasons:
        return "corrupt", tuple(paths), tuple(reasons)
    return "ok", tuple(paths), ()


def _iter_disk_files(output_root: Path):
    for path in output_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(output_root)
        # Any dotfile is our own or an adapter's bookkeeping, never a real
        # downloaded portfolio file -- the filename sanitizer every adapter
        # goes through (core.expectations.destination_filename /
        # core.cli._safe_name) strips leading dots, so a real file can never
        # start with one. This also covers per-AMC sidecars the generic
        # _RESERVED_NAMES set doesn't know about, e.g. Bandhan's own
        # .bandhan_discovery_report.json resume file.
        if relative.name in _RESERVED_NAMES or relative.name.startswith("."):
            continue
        yield relative.as_posix()


def validate(expectations: ExpectationSet, output_root: Path, *, manifest: dict | None = None) -> ValidationReport:
    source_candidates = tuple(expectations.discovery_notes.get("source_unavailable", []) or [])
    if not expectations.items and not source_candidates:
        raise ValueError("Refusing to validate an ExpectationSet with zero items")

    manifest = manifest if manifest is not None else _read_manifest(output_root)
    downloads: dict = manifest.get("downloads", {}) or {}
    failures: dict = manifest.get("failures", {}) or {}
    # A probe-time 500 is allowed to recover during the actual download. Only
    # retain it as an upstream gap if no successful manifest entry superseded
    # that catalog record.
    source_unavailable = tuple(
        entry for entry in source_candidates if entry.get("url") not in downloads
    )

    outcomes: list[ItemOutcome] = []
    accounted_paths: set[str] = set()
    matched_manifest_urls: set[str] = set()
    content_hash_by_key: dict[str, list[tuple[str, str]]] = {}  # sha256 -> [(key, path)]

    for item in expectations.items:
        candidate_urls = (item.url, *item.duplicate_urls)
        entry = None
        matched_url = None
        for url in candidate_urls:
            if url in downloads:
                entry = downloads[url]
                matched_url = url
                break

        if entry is None:
            failure = next((failures.get(url) for url in candidate_urls if url in failures), None)
            if failure:
                details = [failure.get("error") or "download failed"]
                if failure.get("category"):
                    details.insert(0, f"category={failure['category']}")
                if failure.get("status") is not None:
                    details.insert(1, f"status={failure['status']}")
                outcomes.append(ItemOutcome(item=item, status="missing", reasons=tuple(details)))
            else:
                outcomes.append(ItemOutcome(item=item, status="missing", reasons=("never appears in manifest.json",)))
            continue

        matched_manifest_urls.add(matched_url)
        is_archive = "archive" in entry
        status, paths, reasons = _check_archive(entry, output_root) if is_archive else _check_single_file(entry, output_root)
        outcomes.append(ItemOutcome(item=item, status=status, matched_url=matched_url, paths=paths, reasons=reasons))
        accounted_paths.update(paths)

        if status == "ok":
            for path in paths:
                digest = _sha256(output_root / path)
                content_hash_by_key.setdefault(digest, []).append((item.key, path))

    # Manifest entries that don't belong to any currently-expected item --
    # either genuinely stale (the scheme vanished from the site since a
    # prior run) or belong to a document discovery collapsed as a duplicate.
    all_expected_urls = {url for item in expectations.items for url in (item.url, *item.duplicate_urls)}
    stale = []
    for url, entry in downloads.items():
        if url in all_expected_urls:
            continue
        relative = entry.get("path") or (entry.get("archive") or {}).get("name")
        stale.append({"url": url, "path": relative})
        # A stale entry's file is accounted for (it's tracked in the
        # manifest, just for a scheme this run no longer expects) -- it
        # belongs in `stale`, not `unexpected`, which is for files the
        # manifest has no record of at all.
        for member in entry.get("extracted") or []:
            if member.get("path"):
                accounted_paths.add(member["path"])
        if relative:
            accounted_paths.add(relative)

    disk_files = set(_iter_disk_files(output_root))
    unexpected = sorted(disk_files - accounted_paths)

    duplicate_content = [
        {"sha256": digest, "items": [key for key, _ in group], "paths": [path for _, path in group]}
        for digest, group in content_hash_by_key.items()
        if len({key for key, _ in group}) > 1
    ]

    return ValidationReport(
        amc=expectations.amc,
        period=expectations.period,
        discovered=expectations.count,
        outcomes=tuple(outcomes),
        unexpected=tuple(unexpected),
        stale=tuple(stale),
        duplicate_content=tuple(duplicate_content),
        source_unavailable=source_unavailable,
        truncated_by_max_files=expectations.truncated_by_max_files,
    )


def validation_path(output_root: Path) -> Path:
    return output_root / _VALIDATION_FILENAME


def write_validation(output_root: Path, report: ValidationReport) -> Path:
    path = validation_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    return path

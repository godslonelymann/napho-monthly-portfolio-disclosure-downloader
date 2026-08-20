"""No-argument command runner and safe file downloader."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

from .archives import extract_archive, looks_like_archive
from .config import settings
from .discovery import Document, DiscoveryResult, PeriodUnavailable
from .expectations import from_documents, write_expectations
from .http import create_session
from .validation import (
    STATUS_CORRUPT,
    STATUS_DOWNLOAD_FAILED,
    STATUS_INCOMPLETE,
    STATUS_PARTIAL_BY_CONFIG,
    STATUS_SITE_CHANGED,
    STATUS_UPSTREAM_GAP,
    STATUS_SUCCESS,
    validate,
    write_validation,
)

# Exit codes for each validation verdict. 0/1/2 are already spoken for
# (success, uncaught exception, PeriodUnavailable) by the rest of this
# module and by run_verified.py's own handling, so the new statuses start
# at 5 to leave room without colliding.
_STATUS_EXIT_CODES = {
    STATUS_SUCCESS: 0,
    STATUS_INCOMPLETE: 5,
    STATUS_DOWNLOAD_FAILED: 6,
    STATUS_CORRUPT: 7,
    STATUS_PARTIAL_BY_CONFIG: 8,
    STATUS_SITE_CHANGED: 9,
    STATUS_UPSTREAM_GAP: 10,
}


@dataclass(frozen=True)
class DownloadOutcome:
    """What happened to one Document during a download_documents() run."""

    document: Document
    status: str  # "downloaded" | "extracted" | "skipped" | "failed"
    destination: str | None = None  # path relative to output_root, when known
    error: str | None = None


class DownloadFailure(RuntimeError):
    """Typed failure used to decide whether another full download is useful."""

    def __init__(
        self,
        reason: str,
        *,
        category: str,
        retryable: bool,
        status: int | None = None,
        content_type: str | None = None,
        fingerprint: str | None = None,
    ):
        super().__init__(reason)
        self.category = category
        self.retryable = retryable
        self.status = status
        self.content_type = content_type
        self.fingerprint = fingerprint
        self.attempts = 1


def _safe_name(document: Document) -> str:
    # document.filename is set by the adapter (see discovery.document_from_link)
    # and must win: some AMCs (e.g. NJ Mutual Fund) put the real filename in a
    # query string like ?file=Scheme-A.xlsx behind a generic path such as
    # viewfile.php, so falling back to the URL's path basename first collapses
    # every distinct scheme onto the same "viewfile.php" destination.
    name = document.filename or unquote(Path(urlsplit(document.url).path).name) or "portfolio"
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    if not Path(name).suffix and document.file_type:
        name += "." + document.file_type
    return name or "portfolio"


def _validate_magic(path: Path, document: Document, response_context: dict | None = None) -> None:
    context = response_context or {}
    size = path.stat().st_size
    status = context.get("status")
    content_type = context.get("content_type") or "<missing>"
    response_url = context.get("url") or document.url
    content_length = context.get("content_length")
    if status == 204 or content_length == 0 or size == 0:
        raise DownloadFailure(
            f"Empty response: status={status or 200} content_type={content_type} url={response_url}; "
            f"{document.url} did not return a ZIP/XLSX payload",
            category="empty_response",
            retryable=False,
            status=status,
            content_type=content_type,
        )
    data = path.read_bytes()[:512]
    suffix = document.file_type.lower()
    fingerprint = hashlib.sha256(path.read_bytes()[:512]).hexdigest()
    if "html" in str(content_type).lower() or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise DownloadFailure(
            f"HTML response: status={status or 200} content_type={content_type} url={response_url}",
            category="html_response",
            retryable=True,
            status=status,
            content_type=content_type,
            fingerprint=fingerprint,
        )
    # .xlsb is binary *inside* the sheet parts but is still OOXML/ZIP packaging,
    # so it carries the same "PK" header as .xlsx.  Without it listed here the
    # file falls through every branch below and gets no validation at all.
    if suffix in {"xlsx", "xlsm", "xlsb", "zip"} and not (
        data.startswith(b"PK") or (suffix == "xlsx" and data.startswith(b"\xd0\xcf\x11\xe0"))
    ):
        raise DownloadFailure(
            f"{document.url} did not return a ZIP/XLSX payload (status={status or 200} content_type={content_type} url={response_url})",
            category="invalid_payload",
            retryable=True,
            status=status,
            content_type=content_type,
            fingerprint=fingerprint,
        )
    # SpreadsheetML 2003 (an XML dialect, optionally BOM-prefixed) is also a
    # legitimate ".xls" payload -- e.g. Navi serves some months as
    # "\xef\xbb\xbf<?xml ...", which isn't PK/OLE2 but isn't corrupt either.
    if suffix == "xls" and not (
        data.startswith(b"PK")
        or data.startswith(b"\xd0\xcf\x11\xe0")
        or data.lstrip(b"\xef\xbb\xbf").startswith(b"<?xml")
        or data.startswith((b"\x09\x00\x04\x00", b"\x09\x02\x06\x00", b"\x09\x04\x06\x00"))
    ):
        raise DownloadFailure(
            f"{document.url} did not return an XLS/XLSX payload (status={status or 200} content_type={content_type} url={response_url})",
            category="invalid_payload",
            retryable=True,
            status=status,
            content_type=content_type,
            fingerprint=fingerprint,
        )


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _read_manifest(path: Path) -> dict:
    if not path.exists():
        return {"downloads": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"downloads": {}}
    except json.JSONDecodeError:
        return {"downloads": {}}


def _write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _record_failure(manifest: dict, document: Document, exc: Exception) -> None:
    failure = exc if isinstance(exc, DownloadFailure) else None
    manifest.setdefault("failures", {})[document.url] = {
        "amc": document.amc,
        "period": document.period,
        "filename": _safe_name(document),
        "scheme": document.scheme,
        "category": failure.category if failure else "transport",
        "status": failure.status if failure else None,
        "content_type": failure.content_type if failure else None,
        "fingerprint": failure.fingerprint if failure else None,
        "attempts": getattr(exc, "attempts", 1),
        "error": str(exc),
        "failed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _response_header(response, name: str) -> str | None:
    headers = getattr(response, "headers", None) or {}
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    return str(value) if value else None


def _response_context(response, document: Document) -> dict:
    raw_length = _response_header(response, "Content-Length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError:
        content_length = None
    return {
        "status": getattr(response, "status_code", None),
        "content_type": _response_header(response, "Content-Type"),
        "content_length": content_length,
        "url": getattr(response, "url", None) or document.url,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_still_valid(existing: dict | None, output_root: Path, digest: str) -> bool:
    """True if a prior archive extraction is still intact on disk.

    Lets a re-run skip re-extracting an archive whose zip we already deleted:
    if the freshly downloaded zip hashes the same as last time and every file
    we extracted from it is still where the manifest says it is, there's
    nothing to do.
    """
    if not existing or existing.get("archive", {}).get("sha256") != digest:
        return False
    extracted = existing.get("extracted")
    if not extracted:
        return False
    for entry in extracted:
        path = output_root / entry["path"]
        if not path.is_file() or _sha256(path) != entry["sha256"]:
            return False
    return True


def _extract_and_record(
    *,
    document: Document,
    temporary_path: Path,
    digest: str,
    archive_name: str,
    output_root: Path,
    existing: dict | None,
    keep_archive: bool,
) -> tuple[dict, str]:
    """Extract an archive download and build its manifest entry.

    Returns (entry, status) where status is "skipped" or "extracted", for
    the caller's log line.
    """
    if _archive_still_valid(existing, output_root, digest):
        return existing, "skipped"

    archive_bytes = temporary_path.stat().st_size
    result = extract_archive(temporary_path, output_root)

    entry = {
        "amc": document.amc,
        "period": document.period,
        "scheme": document.scheme,
        "source_page_url": document.source_page_url,
        "archive": {"name": archive_name, "bytes": archive_bytes, "sha256": digest},
        "extracted": [
            {"path": path.relative_to(output_root).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in result.written
        ],
        "downloaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "metadata": document.metadata,
    }
    if result.skipped:
        entry["skipped_members"] = result.skipped

    if keep_archive:
        # Same collision handling as the non-archive path below: if a prior
        # run already left an identical zip in place, drop the new temp
        # file instead of overwriting it -- the caller's finally block does
        # the unlink either way, this just decides whether os.replace runs.
        destination = output_root / archive_name
        if not (destination.exists() and _sha256(destination) == digest):
            os.replace(temporary_path, destination)
    return entry, "extracted"


def _unchanged_request_headers(existing: dict | None, destination: Path, output_root: Path) -> dict[str, str]:
    """If-None-Match / If-Modified-Since for a file we already have.

    Returned empty unless a previous run recorded this exact URL *and* the
    file it wrote is still on disk with the hash it recorded -- otherwise a
    304 would let us "keep" a file that isn't there any more. With them the
    server can answer 304 and we skip re-downloading an unchanged workbook;
    without them (no validators recorded, e.g. a first run) nothing changes.
    """
    if not existing:
        return {}
    recorded_path = existing.get("path")
    digest = existing.get("sha256")
    if not recorded_path or not digest:
        return {}
    path = output_root / recorded_path
    if path != destination or not path.is_file() or _sha256(path) != digest:
        return {}
    headers = {}
    if existing.get("etag"):
        headers["If-None-Match"] = existing["etag"]
    if existing.get("last_modified"):
        headers["If-Modified-Since"] = existing["last_modified"]
    return headers


def _download_one(
    session,
    document: Document,
    destination: Path,
    output_root: Path,
    config,
    downloads: dict,
    timeout,
) -> DownloadOutcome:
    """Download a single document and record it in `downloads`.

    Raises on any failure (network, integrity, extraction) -- the caller in
    download_documents() decides whether that aborts the whole run or is
    recorded as a per-document failure and the run continues.
    """
    temporary_path: Path | None = None
    try:
        # Not every session is a real requests.Session -- curl_cffi's
        # Session (used for sites that need a browser TLS fingerprint)
        # returns a response object without context-manager support, so
        # this closes explicitly in the finally block below instead of
        # using "with".
        existing_entry = downloads.get(document.url)
        headers = {"Referer": document.source_page_url}
        headers.update(_unchanged_request_headers(existing_entry, destination, output_root))
        try:
            response = session.get(
                document.url,
                headers=headers,
                stream=True,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            attempts = getattr(session, "retry_total", 0) + 1
            raise DownloadFailure(
                f"Timeout: url={document.url} phase=download attempts<={attempts}: {exc}",
                category="transport", retryable=True,
            ) from exc
        except requests.RequestException as exc:
            raise DownloadFailure(
                f"Request failed: url={document.url} phase=download: {exc}",
                category="transport", retryable=True,
            ) from exc
        try:
            response_context = _response_context(response, document)
            if getattr(response, "status_code", None) == 304:
                # The file host confirmed our copy is still current, so the
                # bytes on disk (already hash-checked by
                # _unchanged_request_headers) stand as they are.
                print(f"unchanged  {document.period} {_safe_name(document)}")
                return DownloadOutcome(
                    document=document,
                    status="skipped",
                    destination=existing_entry.get("path"),
                )
            status = response_context["status"]
            if status is not None and status >= 400:
                retryable = status == 429 or status >= 500
                raise DownloadFailure(
                    f"HTTP response: status={status} content_type={response_context['content_type'] or '<missing>'} url={response_context['url']}",
                    category="http_error",
                    retryable=retryable,
                    status=status,
                    content_type=response_context["content_type"],
                )
            if status == 204 or response_context["content_length"] == 0:
                raise DownloadFailure(
                    f"Empty response: status={status or 200} content_type={response_context['content_type'] or '<missing>'} url={response_context['url']}",
                    category="empty_response",
                    retryable=False,
                    status=status,
                    content_type=response_context["content_type"],
                )
            with tempfile.NamedTemporaryFile(prefix=".portfolio-", suffix=".part", dir=output_root, delete=False) as handle:
                temporary_path = Path(handle.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        except requests.Timeout as exc:
            attempts = getattr(session, "retry_total", 0) + 1
            raise DownloadFailure(
                f"Timeout: url={document.url} phase=download attempts<={attempts}: {exc}",
                category="transport", retryable=True,
            ) from exc
        except requests.RequestException as exc:
            raise DownloadFailure(
                f"Request failed: url={document.url} phase=download: {exc}",
                category="transport", retryable=True,
            ) from exc
        finally:
            response.close()
        _validate_magic(temporary_path, document, response_context)
        digest = _sha256(temporary_path)
        if config.extract_archives and looks_like_archive(document.file_type, temporary_path):
            entry, status = _extract_and_record(
                document=document,
                temporary_path=temporary_path,
                digest=digest,
                archive_name=destination.name,
                output_root=output_root,
                existing=downloads.get(document.url),
                keep_archive=config.keep_archives,
            )
            downloads[document.url] = entry
            print(f"{status:10} {document.period} {destination.name} -> {len(entry.get('extracted', []))} file(s)")
            return DownloadOutcome(document=document, status=status, destination=destination.name)
        else:
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
                temporary_path.unlink(missing_ok=True)
            else:
                os.replace(temporary_path, destination)
            downloads[document.url] = {
                "amc": document.amc,
                "period": document.period,
                "scheme": document.scheme,
                "source_page_url": document.source_page_url,
                "path": destination.relative_to(output_root).as_posix(),
                "bytes": destination.stat().st_size,
                "sha256": digest,
                "content_type": response_context.get("content_type") or mimetypes.guess_type(destination.name)[0],
                # Recorded so the next run can ask the file host whether
                # anything changed instead of re-fetching every workbook.
                "etag": _response_header(response, "ETag"),
                "last_modified": _response_header(response, "Last-Modified"),
                "downloaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "metadata": document.metadata,
            }
            print(f"downloaded {document.period} {_safe_name(document)}")
            return DownloadOutcome(
                document=document,
                status="downloaded",
                destination=destination.relative_to(output_root).as_posix(),
            )
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def _download_with_retries(
    session,
    document: Document,
    destination: Path,
    output_root: Path,
    config,
    downloads: dict,
    timeout,
) -> DownloadOutcome:
    """Retry a failed download attempt before giving up on it.

    core.http's urllib3 Retry already covers transport-level flakes within
    a single request (connection reset, a 503). This is a level above that:
    a failure can also show up *after* a response came back fine -- an
    empty body, a truncated download, a momentary bot-wall HTML page where
    a file was expected -- and those aren't retried by urllib3 at all since
    they don't look like a transport error. Retrying the whole per-document
    flow (not just the HTTP request) catches those too.

    ``config.retry_total``/``config.retry_backoff`` are the same settings
    that size core.http's transport retries -- reused here rather than
    adding new env vars for what is, from an operator's perspective, the
    same "how hard should this try before giving up" knob. Read with
    ``getattr`` defaults so callers passing a minimal fake config (existing
    tests) keep their current no-retry behavior unchanged.
    """
    attempts = getattr(config, "retry_total", 0) + 1
    backoff = getattr(config, "retry_backoff", 0.0)
    last_exc: Exception | None = None
    html_retried = False
    previous_html_fingerprint = None
    for attempt in range(1, attempts + 1):
        try:
            return _download_one(session, document, destination, output_root, config, downloads, timeout)
        except Exception as exc:
            if isinstance(exc, DownloadFailure):
                failure = exc
            else:
                failure = DownloadFailure(
                    f"Request failed: url={document.url} phase=download: {exc}",
                    category="transport", retryable=True,
                )
            failure.attempts = attempt
            last_exc = failure
            retry = failure.retryable
            if failure.category == "html_response":
                if html_retried or (
                    previous_html_fingerprint is not None
                    and failure.fingerprint == previous_html_fingerprint
                ):
                    retry = False
                else:
                    html_retried = True
                    previous_html_fingerprint = failure.fingerprint
            if retry and attempt < attempts:
                print(f"retrying   {document.period} {_safe_name(document)} (attempt {attempt}/{attempts}): {exc}")
                if backoff:
                    time.sleep(backoff * attempt)
            else:
                break
    raise last_exc


def download_documents(
    session,
    documents: list[Document],
    output_root: Path,
    *,
    delay_seconds: float = 0.0,
    continue_on_error: bool = False,
) -> list[DownloadOutcome]:
    """Download every document, writing manifest.json as it goes.

    By default (`continue_on_error=False`) a single failed download aborts
    the run immediately, exactly as before -- existing callers (backfill_axis,
    tests) that treat any raised exception as "this run failed" keep working
    unchanged. Pass `continue_on_error=True` to keep going past individual
    failures instead: each one is recorded as a "failed" DownloadOutcome and
    the rest of the documents are still attempted, so a single flaky file
    doesn't hide whether the other 79 succeeded. Either way the full list of
    per-document outcomes is returned; when nothing fails this list is a
    superset of the old behavior (which returned nothing at all), so callers
    that ignore the return value see no change.
    """
    if not documents:
        raise RuntimeError("Refusing to download an empty discovery result")
    config = settings()
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(output_root)
    manifest = _read_manifest(manifest_path)
    downloads = manifest.setdefault("downloads", {})
    failures = manifest.setdefault("failures", {})
    timeout = getattr(session, "default_timeout", (30, 120))
    # Two distinct documents landing on the same destination filename means the
    # naming scheme can't tell them apart -- e.g. NJ Mutual Fund's viewfile.php
    # URLs put the real filename in a query string, so a URL-basename-only
    # scheme silently collapsed 5 different schemes onto one file, each
    # download overwriting the last. Fail loudly instead of overwriting.
    # This is a discovery-time correctness bug, not a per-file network flake,
    # so it always aborts immediately regardless of continue_on_error.
    seen_destinations: dict[Path, str] = {}
    for document in documents:
        destination = output_root / _safe_name(document)
        prior_url = seen_destinations.get(destination)
        if prior_url is not None and prior_url != document.url:
            raise RuntimeError(
                f"Filename collision: {document.url!r} and {prior_url!r} both resolve to "
                f"{destination.name!r} -- discovery is not producing distinct filenames"
            )
        seen_destinations[destination] = document.url

    outcomes: list[DownloadOutcome] = []
    for index, document in enumerate(documents):
        destination = output_root / _safe_name(document)
        try:
            outcome = _download_with_retries(session, document, destination, output_root, config, downloads, timeout)
        except Exception as exc:
            _record_failure(manifest, document, exc)
            if not continue_on_error:
                # Persist whatever documents already succeeded before this
                # one failed -- previously an abort here discarded that
                # progress entirely, forcing a full re-download next run.
                _write_manifest(manifest_path, manifest)
                raise
            print(f"failed     {document.period} {_safe_name(document)}: {exc}")
            outcome = DownloadOutcome(document=document, status="failed", error=str(exc))
        outcomes.append(outcome)
        if outcome.status in {"downloaded", "extracted", "skipped"}:
            failures.pop(document.url, None)
        if index + 1 < len(documents) and delay_seconds:
            time.sleep(delay_seconds)
    _write_manifest(manifest_path, manifest)
    return outcomes


def _unwrap_discovery(result) -> tuple[list[Document], dict]:
    if isinstance(result, DiscoveryResult):
        return result.documents, result.notes
    return result, {}


def run_cli(
    *,
    amc: str,
    discover,
    description: str = "",
    session=None,
    subdir: str | None = None,
    rediscoverable: bool = False,
) -> int:
    config = settings()
    # A caller may supply its own session -- e.g. a curl_cffi session that
    # impersonates a real browser's TLS fingerprint for sites (Edelweiss,
    # HDFC) whose bot wall also blocks the file host, not just the listing
    # page -- instead of the plain requests.Session created by default.
    session = session or create_session()
    print(f"{amc}: {description or 'monthly portfolio download'}")
    print(f"period={config.period} output={config.output_dir} download={config.download}")
    try:
        result = discover(config.period, session=session)
    except PeriodUnavailable as exc:
        # Exit code 2 is a distinct signal from a normal failure (exit 1 via
        # an uncaught exception): the adapter worked correctly and confirmed
        # this AMC simply doesn't publish the period anywhere reachable.
        print(f"unavailable: {exc}")
        return 2
    documents, adapter_notes = _unwrap_discovery(result)
    if not documents and not adapter_notes.get("source_unavailable"):
        raise RuntimeError(f"{amc} returned zero documents for {config.period}")
    full_discovered_count = len(documents)
    truncated_by_max_files = bool(config.max_files and full_discovered_count > config.max_files)
    if config.max_files:
        documents = documents[: config.max_files]
    print(f"discovered={len(documents)}")
    if config.download:
        destination = config.output_dir / re.sub(r"[^a-z0-9._-]+", "_", amc.lower()).strip("_")
        # Every other AMC writes directly to <amc>/<period>/. Bandhan is the
        # one exception (see verified/Bandhan_Mutual_Fund.py): it publishes
        # two structurally different kinds of workbook, so its files live one
        # level deeper under <amc>/<subdir>/<period>/ to keep them apart
        # without scattering Bandhan across multiple top-level data/raw/
        # folders. audit_icra_coverage.py's directory discovery knows to look
        # for this one extra level when an AMC folder uses it.
        if subdir:
            destination = destination / subdir
        destination = destination / config.period

        if documents and not config.validate and not config.validate_only:
            # AMC_VALIDATE=0 escape hatch: old discover-then-download-and-trust
            # behavior, untouched, for rolling back the whole pipeline at once.
            download_documents(session, documents, destination, delay_seconds=config.delay_seconds)
            return 0

        discovery_notes = dict(adapter_notes)
        if truncated_by_max_files:
            discovery_notes["full_discovered_count"] = full_discovered_count
            discovery_notes["max_files"] = config.max_files
        expectations = from_documents(
            documents,
            discovery_notes=discovery_notes,
            truncated_by_max_files=truncated_by_max_files,
            amc=amc,
            period=config.period,
        )
        write_expectations(destination, expectations)

        if config.validate_only:
            # Audit mode: check what's already on disk (from a previous real
            # run) against today's live expected set, without downloading
            # anything -- the whole cost is one discovery pass instead of a
            # full re-download of every file already sitting there.
            print(f"validate-only: checking {destination} against today's live discovery, nothing will be downloaded")
        else:
            # continue_on_error=True: one bad file must not hide whether the
            # other N-1 succeeded -- validate() below is what decides success,
            # not whether this call happened to raise.
            if documents:
                download_documents(session, documents, destination, delay_seconds=config.delay_seconds, continue_on_error=True)

        report = validate(expectations, destination)

        # Opt-in (rediscoverable=True): a file missing at the end of a run
        # could mean our download failed, or it could mean the AMC's site
        # changed what it lists between the moment discovery ran and the
        # moment we tried to fetch it -- those call for different responses
        # (retry us vs. nothing to retry), so this tries to tell them apart
        # with one more live check instead of reporting both as INCOMPLETE.
        # Deliberately opt-in per adapter: a Playwright-driven discover()
        # (Bandhan, Franklin Templeton) can take many minutes, and paying
        # that cost twice on every incomplete run isn't worth it there.
        # Doesn't apply in validate_only mode: "missing" there just means a
        # prior real run never got the file, which a second discovery call
        # right now can't shed any light on (there was no download attempt
        # in *this* run for a site change to have raced against).
        if rediscoverable and not config.validate_only and report.missing and not report.corrupt:
            try:
                fresh_result = discover(config.period, session=session)
            except PeriodUnavailable:
                fresh_documents = []
            except Exception as exc:
                fresh_documents = None
                print(f"re-discovery check skipped: {exc}")
            else:
                fresh_documents, _ = _unwrap_discovery(fresh_result)
            if fresh_documents is not None:
                fresh_keys = {item.key for item in from_documents(fresh_documents).items} if fresh_documents else set()
                missing_keys = {outcome.item.key for outcome in report.missing}
                if missing_keys.isdisjoint(fresh_keys):
                    report.site_changed = True
                    print(f"re-discovery confirmed {len(missing_keys)} missing file(s) are no longer listed on the site")
                else:
                    print("re-discovery still lists at least one missing file -- keeping status as reported")

        report_path = write_validation(destination, report)
        # A single greppable line so run_verified.py can find this AMC's
        # validation report without having to re-derive its destination
        # path (slug sanitization, Bandhan's extra subdir level, etc.) --
        # that derivation already lives here and shouldn't be duplicated.
        print(f"validation_report={report_path}")
        print(report.render())
        return _STATUS_EXIT_CODES[report.status]
    else:
        for document in documents:
            print(f"found      {document.url}")
    return 0

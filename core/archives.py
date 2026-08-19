"""Safe extraction of zip archives that hold monthly portfolio workbooks.

Deliberately does not use ``ZipFile.extractall`` -- that's the API with the
path-traversal history. Every member is checked (path traversal, symlinks,
encryption, decompression-bomb shape) before anything is written, members are
streamed out one at a time via ``ZipFile.open`` into a scratch directory, and
the result is only moved into the destination directory once the whole
archive has passed validation. On any failure nothing in the destination
directory is touched.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

WORKBOOK_SUFFIXES = {".xls", ".xlsx", ".xlsm", ".xlsb", ".csv"}
ARCHIVE_SUFFIXES = {".zip"}
_IGNORED_BASENAMES = {".ds_store"}
_IGNORED_PREFIXES = ("__macosx/",)

# Decompression-bomb guards. The four archives this was written against top
# out around 25MB / 150 members, so these limits are generous, not tuned.
MAX_MEMBERS = 5000
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

# A zip nested inside a zip is recursed into once; a zip inside that is not.
MAX_NESTED_DEPTH = 1


@dataclass
class ExtractResult:
    written: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def looks_like_archive(file_type: str, path: Path) -> bool:
    """True if `path` should be treated as an archive to extract.

    Deciding this by extension rather than magic bytes matters: an .xlsx
    file *is* a zip (it starts with "PK"), so a magic-bytes-only check would
    try to "extract" every xlsx download. The `[Content_Types].xml` check is
    a belt-and-braces guard against a mislabeled OOXML file slipping through
    on file_type alone.
    """
    if file_type.lower() != "zip":
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return "[Content_Types].xml" not in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _is_noise(name: str) -> bool:
    lower = name.lower()
    return lower in _IGNORED_BASENAMES or any(lower.startswith(prefix) for prefix in _IGNORED_PREFIXES)


def _check_member_safety(member: zipfile.ZipInfo) -> None:
    name = member.filename.replace("\\", "/")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Unsafe archive member path: {member.filename!r}")
    # High 16 bits of external_attr hold the Unix st_mode when the archive
    # was written on Unix (create_system == 3); S_ISLNK is 0o120000. Zips
    # written elsewhere leave this 0, so the check is a no-op for them.
    mode = member.external_attr >> 16
    if mode and (mode & 0o170000) == 0o120000:
        raise RuntimeError(f"Archive member is a symlink, refusing to extract: {member.filename!r}")
    if member.flag_bits & 0x1:
        raise RuntimeError(f"Archive member is encrypted, refusing to extract: {member.filename!r}")


def _check_bomb_limits(members: list[zipfile.ZipInfo]) -> None:
    if len(members) > MAX_MEMBERS:
        raise RuntimeError(f"Archive has too many members ({len(members)} > {MAX_MEMBERS})")
    total_uncompressed = sum(member.file_size for member in members)
    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise RuntimeError(f"Archive uncompressed size is too large ({total_uncompressed} bytes)")
    for member in members:
        if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
            raise RuntimeError(f"Archive member has a suspicious compression ratio: {member.filename!r}")


def _validate_workbook_magic(data: bytes, suffix: str) -> bool:
    # Mirrors core.cli._validate_magic's rules for the same file types.
    if suffix in {".xlsx", ".xlsm", ".xlsb"}:
        # Some UTI legacy archives label an OLE2 .xls workbook as .xlsx.
        # Excel and xlrd open it successfully based on content, so accept
        # that recognizable workbook container instead of discarding the
        # entire otherwise-valid monthly archive over the wrong extension.
        return data.startswith(b"PK") or data.startswith(b"\xd0\xcf\x11\xe0")
    if suffix == ".xls":
        # In addition to OLE2 and SpreadsheetML, very old Excel files can be
        # a bare BIFF stream.  These are the BOF signatures for BIFF2/3/4;
        # UTI's May 2019 dividend workbook is a valid BIFF2 example.
        return (
            data.startswith(b"PK")
            or data.startswith(b"\xd0\xcf\x11\xe0")
            or data.lstrip(b"\xef\xbb\xbf").startswith(b"<?xml")
            or data.startswith((b"\x09\x00\x04\x00", b"\x09\x02\x06\x00", b"\x09\x04\x06\x00"))
        )
    if suffix == ".csv":
        return True
    return False


def _extract_members(zip_path: Path, temp_root: Path, depth: int) -> tuple[list[Path], list[str]]:
    """Validate and stream-extract `zip_path`'s members flat into `temp_root`.

    Returns (extracted_file_paths, skipped_member_names) and never touches
    anything outside `temp_root` -- the caller commits to the real
    destination only after every recursive call here has succeeded.
    """
    if depth > MAX_NESTED_DEPTH:
        raise RuntimeError(f"{zip_path}: nested archive depth exceeds {MAX_NESTED_DEPTH}")

    written: list[Path] = []
    skipped: list[str] = []

    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"{zip_path}: not a valid ZIP archive") from exc

    with archive:
        members = archive.infolist()
        _check_bomb_limits(members)

        content_entries = [member for member in members if not member.is_dir() and not _is_noise(member.filename)]
        for member in content_entries:
            _check_member_safety(member)

        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"{zip_path}: corrupt archive member {bad_member!r}")

        for member in content_entries:
            name = Path(member.filename.replace("\\", "/")).name
            suffix = Path(name).suffix.lower()

            if suffix in ARCHIVE_SUFFIXES:
                fd, nested_name = tempfile.mkstemp(prefix=".nested-", suffix=".zip", dir=temp_root)
                nested_zip = Path(nested_name)
                try:
                    with archive.open(member) as source, open(fd, "wb") as target:
                        shutil.copyfileobj(source, target)
                    nested_written, nested_skipped = _extract_members(nested_zip, temp_root, depth + 1)
                finally:
                    nested_zip.unlink(missing_ok=True)
                written.extend(nested_written)
                skipped.extend(nested_skipped)
                continue

            if suffix not in WORKBOOK_SUFFIXES:
                skipped.append(member.filename)
                continue

            out_path = temp_root / name
            if out_path.exists():
                # Two members in this archive share a base filename -- keep
                # both, final collision handling happens against the real
                # destination directory when this gets committed.
                out_path = temp_root / f"{Path(name).stem}-{len(written)}{Path(name).suffix}"
            with archive.open(member) as source, out_path.open("wb") as target:
                shutil.copyfileobj(source, target)

            if not _validate_workbook_magic(out_path.read_bytes()[:8], suffix):
                raise RuntimeError(f"{zip_path}: {member.filename} does not look like a valid {suffix} workbook")
            written.append(out_path)

    return written, skipped


def _unique_destination(directory: Path, filename: str, digest: str) -> Path:
    destination = directory / filename
    if not destination.exists():
        return destination
    if hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
        return destination
    source = Path(filename)
    return directory / f"{source.stem}-{digest[:12]}{source.suffix}"


def extract_archive(zip_path: Path, into_dir: Path) -> ExtractResult:
    """Extract the workbook members of `zip_path` into `into_dir`.

    Raises on any unsafe member, a decompression-bomb-shaped archive, or an
    archive with zero workbook members -- in every failure case `into_dir`
    is left untouched and `zip_path` is not modified. On success, files are
    moved into `into_dir` (existing same-content files are left in place,
    same-name-different-content files get a short hash suffix) and the
    written paths are returned along with any non-workbook member names
    that were skipped rather than silently dropped.
    """
    into_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".extract-", dir=into_dir) as raw_temp:
        temp_root = Path(raw_temp)
        written, skipped = _extract_members(zip_path, temp_root, depth=0)
        if not written:
            raise RuntimeError(f"{zip_path}: archive contains no workbook members")

        result = ExtractResult(skipped=skipped)
        for extracted in written:
            digest = hashlib.sha256(extracted.read_bytes()).hexdigest()
            destination = _unique_destination(into_dir, extracted.name, digest)
            shutil.move(str(extracted), destination)
            result.written.append(destination)

    return result

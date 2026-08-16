"""One-off: extract the zip archives downloaded before extraction was wired
into the pipeline (core.archives.extract_archive), and rewrite their manifest
entries to point at the extracted workbooks instead of the zip.

Walks ``AMC_OUTPUT_DIR`` (default ``data/raw``) for ``*.zip`` files, finds
each one's governing ``manifest.json`` (the shared downloader in
``core.cli`` writes it into the period directory; the Aditya Birla script
writes it into the AMC's root directory), extracts the archive next to it,
and updates the matching manifest entry. Safe to re-run: an entry that
already has an "extracted" list is left alone, and by the time every zip has
been backfilled there is nothing left for a rerun to find.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.archives import extract_archive
from core.config import Settings, settings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(path: Path, manifest: dict) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(manifest, file, indent=2, ensure_ascii=False)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _find_manifest(zip_path: Path) -> Path | None:
    for candidate_dir in (zip_path.parent, zip_path.parent.parent):
        candidate = candidate_dir / "manifest.json"
        if candidate.is_file():
            return candidate
    return None


def _find_entry(manifest: dict, output_root: Path, zip_path: Path) -> tuple[str, dict] | None:
    relative = zip_path.relative_to(output_root).as_posix()
    for url, entry in manifest.get("downloads", {}).items():
        if entry.get("path") == relative:
            return url, entry
    return None


def backfill_zip(zip_path: Path, config: Settings) -> str:
    manifest_path = _find_manifest(zip_path)
    if manifest_path is None:
        return f"skip (no manifest.json found for {zip_path})"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_root = manifest_path.parent
    found = _find_entry(manifest, output_root, zip_path)
    if found is None:
        return f"skip (no manifest entry references {zip_path.relative_to(output_root)})"
    url, entry = found

    if "extracted" in entry:
        return "skip (already extracted)"

    digest = _sha256(zip_path)
    archive_bytes = zip_path.stat().st_size
    result = extract_archive(zip_path, zip_path.parent)

    new_entry = dict(entry)
    new_entry.pop("path", None)
    new_entry.pop("bytes", None)
    new_entry.pop("sha256", None)
    new_entry["archive"] = {"name": zip_path.name, "bytes": archive_bytes, "sha256": digest}
    new_entry["extracted"] = [
        {"path": path.relative_to(output_root).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in result.written
    ]
    if result.skipped:
        new_entry["skipped_members"] = result.skipped

    manifest["downloads"][url] = new_entry
    _write_manifest(manifest_path, manifest)

    if not config.keep_archives:
        zip_path.unlink()

    return f"extracted {len(result.written)} file(s), {len(result.skipped)} skipped member(s)"


def main() -> int:
    config = settings()
    zips = sorted(config.output_dir.rglob("*.zip"))
    if not zips:
        print(f"no zip archives found under {config.output_dir}")
        return 0

    print(f"backfill: {len(zips)} zip archive(s) under {config.output_dir}")
    failures = 0
    for zip_path in zips:
        relative = zip_path.relative_to(config.output_dir)
        try:
            print(f"{relative}: {backfill_zip(zip_path, config)}")
        except Exception as exc:
            failures += 1
            print(f"{relative}: FAILED: {exc}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

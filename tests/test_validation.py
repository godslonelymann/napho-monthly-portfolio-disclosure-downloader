from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core.discovery import Document
from core.expectations import from_documents
from core.validation import (
    STATUS_CORRUPT,
    STATUS_DOWNLOAD_FAILED,
    STATUS_INCOMPLETE,
    STATUS_PARTIAL_BY_CONFIG,
    STATUS_SITE_CHANGED,
    STATUS_SUCCESS,
    validate,
)


def _doc(**overrides) -> Document:
    fields = dict(
        amc="test_amc",
        period="2026-05",
        url="https://example.com/files/scheme-a.xlsx",
        source_page_url="https://example.com/disclosures",
        label="Scheme A",
        filename="scheme-a.xlsx",
        file_type="xlsx",
        scheme=None,
        primary=False,
        metadata={},
    )
    fields.update(overrides)
    return Document(**fields)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ValidationTestCase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.output_root = Path(self._tempdir.name)

    def _write_file(self, name: str, content: bytes) -> Path:
        path = self.output_root / name
        path.write_bytes(content)
        return path

    def _file_entry(self, *, path: str, content: bytes, scheme=None, source_page_url="https://example.com/disclosures") -> dict:
        return {
            "amc": "test_amc",
            "period": "2026-05",
            "scheme": scheme,
            "source_page_url": source_page_url,
            "path": path,
            "bytes": len(content),
            "sha256": _sha256_bytes(content),
            "content_type": "application/vnd.ms-excel",
            "downloaded_at": "2026-05-01T00:00:00+00:00",
            "metadata": {},
        }


class FullSuccessTests(ValidationTestCase):
    def test_every_expected_file_present_and_correct_is_success(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B", filename="fund_b.xlsx"),
        ]
        expectations = from_documents(documents)

        content_a = b"PK\x03\x04 fake xlsx a"
        content_b = b"PK\x03\x04 fake xlsx b"
        self._write_file("fund_a.xlsx", content_a)
        self._write_file("fund_b.xlsx", content_b)
        manifest = {
            "downloads": {
                "https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content_a, scheme="Fund A"),
                "https://example.com/b.xlsx": self._file_entry(path="fund_b.xlsx", content=content_b, scheme="Fund B"),
            }
        }

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertEqual(report.discovered, 2)
        self.assertEqual(report.downloaded, 2)
        self.assertEqual(len(report.missing), 0)
        self.assertEqual(len(report.corrupt), 0)
        self.assertEqual(len(report.unexpected), 0)

    def test_rerunning_the_same_month_against_unchanged_files_is_still_success(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        content = b"PK\x03\x04 same bytes both runs"
        self._write_file("fund_a.xlsx", content)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content, scheme="Fund A")}}

        first = validate(expectations, self.output_root, manifest=manifest)
        second = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(first.status, STATUS_SUCCESS)
        self.assertEqual(second.status, STATUS_SUCCESS)


class MissingFileTests(ValidationTestCase):
    def test_a_scheme_never_downloaded_is_missing_not_silently_success(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B", filename="fund_b.xlsx"),
        ]
        expectations = from_documents(documents)
        content_a = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content_a)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content_a, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertEqual(report.discovered, 2)
        self.assertEqual(report.downloaded, 1)
        self.assertEqual(len(report.missing), 1)
        self.assertEqual(report.missing[0].item.scheme, "Fund B")

    def test_fifty_discovered_forty_nine_downloaded_is_incomplete_not_success(self):
        documents = [
            _doc(url=f"https://example.com/{index}.xlsx", scheme=f"Fund {index}", filename=f"fund_{index}.xlsx")
            for index in range(50)
        ]
        expectations = from_documents(documents)
        downloads = {}
        for index in range(49):
            content = f"PK fund {index}".encode()
            self._write_file(f"fund_{index}.xlsx", content)
            downloads[f"https://example.com/{index}.xlsx"] = self._file_entry(
                path=f"fund_{index}.xlsx", content=content, scheme=f"Fund {index}"
            )

        report = validate(expectations, self.output_root, manifest={"downloads": downloads})

        self.assertEqual(report.discovered, 50)
        self.assertEqual(report.downloaded, 49)
        self.assertEqual(report.status, STATUS_INCOMPLETE)

    def test_manifest_entry_present_but_file_deleted_from_disk_is_missing(self):
        # e.g. someone cleaned the output directory but the manifest is stale.
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=b"gone")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(len(report.missing), 1)

    def test_nothing_downloaded_at_all_is_download_failed(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A")]
        expectations = from_documents(documents)

        report = validate(expectations, self.output_root, manifest={"downloads": {}})

        self.assertEqual(report.status, STATUS_DOWNLOAD_FAILED)
        self.assertEqual(report.downloaded, 0)


class CorruptionTests(ValidationTestCase):
    def test_zero_byte_download_is_corrupt_not_success(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        self._write_file("fund_a.xlsx", b"")
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=b"", scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_CORRUPT)
        self.assertEqual(len(report.corrupt), 1)
        self.assertIn("zero-byte", report.corrupt[0].reasons[0])

    def test_html_error_page_saved_with_xlsx_extension_is_corrupt(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        html_content = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"
        self._write_file("fund_a.xlsx", html_content)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=html_content, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_CORRUPT)
        self.assertTrue(any("HTML" in reason for reason in report.corrupt[0].reasons))

    def test_file_modified_after_download_fails_hash_check(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        original = b"PK\x03\x04 original content"
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=original, scheme="Fund A")}}
        # Disk has different bytes than what the manifest recorded.
        self._write_file("fund_a.xlsx", b"PK\x03\x04 tampered content")

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_CORRUPT)
        self.assertTrue(any("sha256" in reason for reason in report.corrupt[0].reasons))


class ArchiveTests(ValidationTestCase):
    def test_archive_with_all_members_present_is_success(self):
        documents = [_doc(url="https://example.com/bundle.zip", scheme=None, filename="bundle.zip", file_type="zip")]
        expectations = from_documents(documents)
        member_content = b"PK member sheet"
        self._write_file("scheme-a.xlsx", member_content)
        manifest = {
            "downloads": {
                "https://example.com/bundle.zip": {
                    "amc": "test_amc",
                    "period": "2026-05",
                    "scheme": None,
                    "source_page_url": "https://example.com/disclosures",
                    "archive": {"name": "bundle.zip", "bytes": 100, "sha256": "deadbeef"},
                    "extracted": [
                        {"path": "scheme-a.xlsx", "bytes": len(member_content), "sha256": _sha256_bytes(member_content)}
                    ],
                    "downloaded_at": "2026-05-01T00:00:00+00:00",
                    "metadata": {},
                }
            }
        }

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_archive_missing_an_extracted_member_is_corrupt(self):
        documents = [_doc(url="https://example.com/bundle.zip", scheme=None, filename="bundle.zip", file_type="zip")]
        expectations = from_documents(documents)
        manifest = {
            "downloads": {
                "https://example.com/bundle.zip": {
                    "amc": "test_amc",
                    "period": "2026-05",
                    "scheme": None,
                    "source_page_url": "https://example.com/disclosures",
                    "archive": {"name": "bundle.zip", "bytes": 100, "sha256": "deadbeef"},
                    "extracted": [{"path": "scheme-a.xlsx", "bytes": 10, "sha256": "abc123"}],
                    "downloaded_at": "2026-05-01T00:00:00+00:00",
                    "metadata": {},
                }
            }
        }
        # scheme-a.xlsx deliberately never written to disk.

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_CORRUPT)


class UnexpectedAndStaleTests(ValidationTestCase):
    def test_a_file_on_disk_outside_the_manifest_is_unexpected(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        content = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content)
        self._write_file("leftover_from_manual_copy.xlsx", b"PK stray")
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_SUCCESS)  # unexpected files don't block success on their own
        self.assertIn("leftover_from_manual_copy.xlsx", report.unexpected)

    def test_an_adapters_own_sidecar_dotfile_is_not_flagged_as_unexpected(self):
        # e.g. Bandhan's own .bandhan_discovery_report.json resume file --
        # bookkeeping the adapter writes itself, not a downloaded portfolio
        # file. A real downloaded file can never start with "." (the
        # filename sanitizer strips leading dots), so any dotfile here is
        # ours or an adapter's, not something to flag as unexplained.
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        content = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content)
        self._write_file(".bandhan_discovery_report.json", b'{"schemes": {}}')
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertEqual(report.unexpected, ())

    def test_a_scheme_that_disappeared_from_the_site_leaves_a_stale_manifest_entry(self):
        # Previous run downloaded Fund B; this run's discovery no longer
        # lists it (site removed the scheme between runs).
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        content_a = b"PK\x03\x04 fund a"
        content_b = b"PK\x03\x04 fund b (stale)"
        self._write_file("fund_a.xlsx", content_a)
        self._write_file("fund_b.xlsx", content_b)
        manifest = {
            "downloads": {
                "https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content_a, scheme="Fund A"),
                "https://example.com/b.xlsx": self._file_entry(path="fund_b.xlsx", content=content_b, scheme="Fund B"),
            }
        }

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertEqual(len(report.stale), 1)
        self.assertEqual(report.stale[0]["url"], "https://example.com/b.xlsx")
        # fund_b.xlsx is accounted for by the stale manifest entry, not "unexpected".
        self.assertNotIn("fund_b.xlsx", report.unexpected)


class DuplicateContentTests(ValidationTestCase):
    def test_two_different_schemes_downloading_byte_identical_content_is_flagged(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B", filename="fund_b.xlsx"),
        ]
        expectations = from_documents(documents)
        shared_content = b"PK\x03\x04 identical bytes"
        self._write_file("fund_a.xlsx", shared_content)
        self._write_file("fund_b.xlsx", shared_content)
        manifest = {
            "downloads": {
                "https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=shared_content, scheme="Fund A"),
                "https://example.com/b.xlsx": self._file_entry(path="fund_b.xlsx", content=shared_content, scheme="Fund B"),
            }
        }

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(len(report.duplicate_content), 1)
        self.assertEqual(set(report.duplicate_content[0]["items"]), {"scheme:2026-05:fund a", "scheme:2026-05:fund b"})


class MaxFilesTruncationTests(ValidationTestCase):
    def test_truncated_discovery_is_never_reported_as_success(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents, truncated_by_max_files=True)
        content = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        self.assertEqual(report.status, STATUS_PARTIAL_BY_CONFIG)


class SiteChangedTests(ValidationTestCase):
    """validate() never sets site_changed itself -- core.cli.run_cli does,
    after an optional re-discovery pass confirms the missing item(s) are no
    longer listed anywhere. These tests exercise the .status property's
    reaction to that flag directly, the same way run_cli sets it."""

    def test_site_changed_overrides_incomplete_when_every_missing_item_is_confirmed_gone(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B", filename="fund_b.xlsx"),
        ]
        expectations = from_documents(documents)
        content_a = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content_a)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content_a, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)
        self.assertEqual(report.status, STATUS_INCOMPLETE)  # before the override

        report.site_changed = True
        self.assertEqual(report.status, STATUS_SITE_CHANGED)

    def test_site_changed_does_not_override_a_corrupt_status(self):
        # A corrupt file was genuinely downloaded and is bad -- that's never
        # a "the site changed" story, regardless of what re-discovery found.
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        self._write_file("fund_a.xlsx", b"")
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=b"", scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)
        report.site_changed = True

        self.assertEqual(report.status, STATUS_CORRUPT)

    def test_site_changed_has_no_effect_on_an_already_successful_report(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        content = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)
        report.site_changed = True

        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_site_changed_appears_in_render_and_to_dict(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        report = validate(expectations, self.output_root, manifest={"downloads": {}})
        report.site_changed = True

        self.assertEqual(report.status, STATUS_SITE_CHANGED)
        self.assertIn("site changed mid-run", report.render())
        self.assertTrue(report.to_dict()["site_changed"])
        self.assertEqual(report.to_dict()["status"], STATUS_SITE_CHANGED)


class RenderTests(ValidationTestCase):
    def test_render_matches_the_expected_success_format(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        content = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)
        text = report.render()

        self.assertIn("AMC: test_amc", text)
        self.assertIn("Period: 2026-05", text)
        self.assertIn("Discovered: 1", text)
        self.assertIn("Downloaded: 1", text)
        self.assertIn("Status: SUCCESS", text)

    def test_render_lists_missing_scheme_names(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx"),
            _doc(url="https://example.com/b.xlsx", scheme="Bandhan XYZ Fund", filename="fund_b.xlsx"),
        ]
        expectations = from_documents(documents)
        content_a = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content_a)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content_a, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)
        text = report.render()

        self.assertIn("Missing:", text)
        self.assertIn("- Bandhan XYZ Fund", text)
        self.assertIn("Status: INCOMPLETE", text)


class RoundTripTests(ValidationTestCase):
    def test_to_dict_is_json_serializable(self):
        documents = [_doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx")]
        expectations = from_documents(documents)
        content = b"PK\x03\x04 fund a"
        self._write_file("fund_a.xlsx", content)
        manifest = {"downloads": {"https://example.com/a.xlsx": self._file_entry(path="fund_a.xlsx", content=content, scheme="Fund A")}}

        report = validate(expectations, self.output_root, manifest=manifest)

        json.dumps(report.to_dict())  # must not raise


if __name__ == "__main__":
    unittest.main()

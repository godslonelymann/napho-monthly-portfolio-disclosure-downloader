from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.cli import _validate_magic, download_documents
from core.discovery import DiscoveryResult, Document
from core.expectations import from_documents
from core.validation import STATUS_UPSTREAM_GAP, validate
from verified import JM_Financial_Mutual_Fund as jm
from verified import PGIM_India_Mutual_Fund as pgim


class _Response:
    def __init__(self, body=b"", status=200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self.url = "https://example.test/file.xlsx"

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        pass


class _Session:
    retry_total = 2
    default_timeout = (3, 7)

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, Exception):
            raise value
        return value


def _doc(url="https://example.test/file.xlsx"):
    return Document(
        amc="test_amc", period="2026-05", url=url,
        source_page_url="https://example.test/", filename="file.xlsx", file_type="xlsx",
    )


class PgimCandidateTests(unittest.TestCase):
    def test_candidates_are_stable_bounded_and_cover_targeted_transforms(self):
        url = "https://example.test/a-elss-feb-2024.xlsb"
        first = pgim._url_casing_variants(url)
        second = pgim._url_casing_variants(url)

        self.assertEqual(first, second)
        self.assertEqual(first[0], url)
        self.assertEqual(len(first), len(set(first)))
        self.assertLessEqual(len(first), 40)
        self.assertIn("https://example.test/a-ELSS-feb-2024.xlsx", first)

        ampersand = pgim._url_casing_variants(
            "https://example.test/banking-&-psu-debt-fund-sep-2021.xlsb"
        )
        self.assertTrue(any("banking-psu-debt-fund" in candidate for candidate in ampersand))
        self.assertTrue(any("sept-2021" in candidate for candidate in ampersand))

    def test_unresolved_resolution_keeps_original_url(self):
        class HeadSession:
            def head(self, url, **kwargs):
                return _Response(status=204)

        original = "https://example.test/feb-2023.xlsb"
        result = pgim._resolve_url(HeadSession(), original)
        self.assertEqual(result.url, original)
        self.assertEqual(result.status, "empty")


class CoreDiagnosticsTests(unittest.TestCase):
    def test_empty_responses_are_permanent_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = _doc()
            config = SimpleNamespace(extract_archives=False, keep_archives=False, retry_total=2, retry_backoff=0)
            session = _Session({document.url: _Response(status=204)})
            with patch("core.cli.settings", return_value=config):
                outcomes = download_documents(session, [document], root, continue_on_error=True)

            self.assertEqual(len(session.calls), 1)
            self.assertEqual(outcomes[0].status, "failed")
            manifest = json.loads((root / "manifest.json").read_text())
            failure = manifest["failures"][document.url]
            self.assertEqual(failure["category"], "empty_response")
            self.assertEqual(failure["status"], 204)
            self.assertIn("status=204", failure["error"])

    def test_missing_content_type_does_not_reject_a_valid_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.xlsx"
            path.write_bytes(b"PK\x03\x04 workbook")
            _validate_magic(path, _doc(), {"status": 200, "content_type": None, "url": _doc().url})

    def test_failure_ledger_reason_is_attached_to_validation_missing_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = _doc()
            expectations = from_documents([document])
            manifest = {
                "downloads": {},
                "failures": {document.url: {
                    "category": "http_error", "status": 500,
                    "error": "HTTP response: status=500", "attempts": 3,
                }},
            }
            report = validate(expectations, root, manifest=manifest)
            self.assertEqual(report.missing[0].reasons[0], "category=http_error")
            self.assertIn("status=500", " ".join(report.missing[0].reasons))

    def test_source_gap_is_a_distinct_validation_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expectations = from_documents(
                [],
                amc="pgim",
                period="2024-02",
                discovery_notes={"source_unavailable": [{"filename": "missing.xlsx", "reason": "HTTP 204"}]},
            )
            report = validate(expectations, root, manifest={"downloads": {}})
            self.assertEqual(report.status, STATUS_UPSTREAM_GAP)
            self.assertEqual(report.to_dict()["source_unavailable"][0]["filename"], "missing.xlsx")


class JmDuplicateTests(unittest.TestCase):
    @staticmethod
    def _record(download_id, title, filename, extension="xlsx"):
        return {
            "DownloadID": download_id,
            "SubCategoryName": "Monthly Portfolio of Schemes",
            "Title": title,
            "FileName": filename,
            "FileEXT": extension,
        }

    def test_html_duplicate_is_suppressed_only_when_valid_sibling_exists(self):
        records = [
            self._record(4655, "Monthly Portfolio - JM Equity Fund - March 31, 2018", "bad.xlsx"),
            self._record(4656, " Monthly Portfolio - JM Equity Fund - March 31, 2018 ", "good.xlsx"),
        ]

        class Session:
            def get(self, url, **kwargs):
                return _Response(body=b"<html>waf</html>" if url.endswith("bad.xlsx") else b"PK\x03\x04 workbook", headers={"Content-Type": "text/html" if url.endswith("bad.xlsx") else "application/octet-stream"})

        with patch.object(jm, "_fetch_records", return_value=records):
            result = jm.discover("2018-03", session=Session())

        self.assertIsInstance(result, DiscoveryResult)
        self.assertEqual([document.filename for document in result.documents], ["good.xlsx"])
        self.assertEqual(result.notes["rejected_duplicates"][0]["download_id"], 4655)

    def test_unique_html_record_is_retained(self):
        records = [self._record(9000, "Monthly Portfolio - JM Equity Fund - March 31, 2018", "only.xlsx")]

        class Session:
            def get(self, url, **kwargs):
                return _Response(body=b"<html>waf</html>", headers={"Content-Type": "text/html"})

        with patch.object(jm, "_fetch_records", return_value=records):
            result = jm.discover("2018-03", session=Session())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].filename, "only.xlsx")


if __name__ == "__main__":
    unittest.main()

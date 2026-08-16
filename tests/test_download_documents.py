from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.cli import DownloadOutcome, download_documents
from core.discovery import Document


class _FakeResponse:
    def __init__(self, *, body: bytes = b"", status: int = 200):
        self.body = body
        self.status = status
        self.closed = False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def iter_content(self, chunk_size: int):
        yield self.body

    def close(self):
        self.closed = True


class _FakeSession:
    """Serves canned responses/exceptions keyed by URL, mimicking requests.Session.get.

    A URL's value may be a single outcome (returned/raised every call) or a
    list of outcomes consumed one per call -- the list form is what the
    per-document retry tests use to simulate "fails twice, then succeeds".
    """

    default_timeout = (3, 7)
    retry_total = 2

    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.requested_urls: list[str] = []

    def get(self, url, **kwargs):
        self.requested_urls.append(url)
        outcome = self.responses[url]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _doc(name: str, *, body: bytes = b"PK\x03\x04 fake xlsx") -> tuple[Document, bytes]:
    document = Document(
        amc="test_amc",
        period="2026-05",
        url=f"https://example.test/{name}.xlsx",
        source_page_url="https://example.test/",
        filename=f"{name}.xlsx",
        file_type="xlsx",
        scheme=name,
    )
    return document, body


class DownloadDocumentsTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.output_root = Path(self._tempdir.name)
        self._config = SimpleNamespace(extract_archives=True, keep_archives=False, delay_seconds=0)
        self._patch = patch("core.cli.settings", return_value=self._config)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_all_success_returns_outcomes_and_preserves_old_no_exception_behavior(self):
        doc_a, body_a = _doc("fund_a")
        doc_b, body_b = _doc("fund_b")
        session = _FakeSession(
            {
                doc_a.url: _FakeResponse(body=body_a),
                doc_b.url: _FakeResponse(body=body_b),
            }
        )

        outcomes = download_documents(session, [doc_a, doc_b], self.output_root)

        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(isinstance(outcome, DownloadOutcome) for outcome in outcomes))
        self.assertTrue(all(outcome.status == "downloaded" for outcome in outcomes))
        self.assertTrue((self.output_root / "fund_a.xlsx").exists())
        self.assertTrue((self.output_root / "fund_b.xlsx").exists())
        manifest = json.loads((self.output_root / "manifest.json").read_text())
        self.assertEqual(len(manifest["downloads"]), 2)

    def test_default_behavior_still_raises_immediately_on_first_failure(self):
        doc_a, body_a = _doc("fund_a")
        doc_b, _ = _doc("fund_b")
        session = _FakeSession(
            {
                doc_a.url: _FakeResponse(body=body_a),
                doc_b.url: RuntimeError("simulated network failure"),
            }
        )

        with self.assertRaises(RuntimeError):
            download_documents(session, [doc_a, doc_b], self.output_root)

        # doc_a succeeded before doc_b's failure aborted the run -- that
        # progress must survive the abort instead of being discarded.
        self.assertTrue((self.output_root / "fund_a.xlsx").exists())
        manifest = json.loads((self.output_root / "manifest.json").read_text())
        self.assertEqual(len(manifest["downloads"]), 1)
        self.assertIn(doc_a.url, manifest["downloads"])

    def test_a_document_never_attempted_after_the_raise_has_no_manifest_entry(self):
        doc_a, _ = _doc("fund_a")
        doc_b, body_b = _doc("fund_b")
        session = _FakeSession(
            {
                doc_a.url: RuntimeError("simulated network failure"),
                doc_b.url: _FakeResponse(body=body_b),
            }
        )

        with self.assertRaises(RuntimeError):
            download_documents(session, [doc_a, doc_b], self.output_root)

        self.assertFalse((self.output_root / "fund_b.xlsx").exists())

    def test_continue_on_error_downloads_the_rest_after_one_failure(self):
        doc_a, body_a = _doc("fund_a")
        doc_b, _ = _doc("fund_b")
        doc_c, body_c = _doc("fund_c")
        session = _FakeSession(
            {
                doc_a.url: _FakeResponse(body=body_a),
                doc_b.url: RuntimeError("simulated network failure"),
                doc_c.url: _FakeResponse(body=body_c),
            }
        )

        outcomes = download_documents(session, [doc_a, doc_b, doc_c], self.output_root, continue_on_error=True)

        self.assertEqual([outcome.status for outcome in outcomes], ["downloaded", "failed", "downloaded"])
        self.assertTrue((self.output_root / "fund_a.xlsx").exists())
        self.assertFalse((self.output_root / "fund_b.xlsx").exists())
        self.assertTrue((self.output_root / "fund_c.xlsx").exists())

    def test_continue_on_error_records_the_failure_reason(self):
        doc_a, _ = _doc("fund_a")
        session = _FakeSession({doc_a.url: RuntimeError("simulated network failure")})

        outcomes = download_documents(session, [doc_a], self.output_root, continue_on_error=True)

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "failed")
        self.assertIn("simulated network failure", outcomes[0].error)

    def test_continue_on_error_never_raises_and_writes_manifest_for_the_successes(self):
        doc_a, body_a = _doc("fund_a")
        doc_b, _ = _doc("fund_b")
        session = _FakeSession(
            {
                doc_a.url: _FakeResponse(body=body_a),
                doc_b.url: RuntimeError("simulated network failure"),
            }
        )

        # Must not raise -- that's the entire point of continue_on_error.
        outcomes = download_documents(session, [doc_a, doc_b], self.output_root, continue_on_error=True)

        self.assertEqual(len(outcomes), 2)
        manifest = json.loads((self.output_root / "manifest.json").read_text())
        self.assertEqual(len(manifest["downloads"]), 1)
        self.assertIn(doc_a.url, manifest["downloads"])

    def test_a_zero_byte_response_is_reported_as_a_failed_outcome_not_a_silent_success(self):
        doc_a, _ = _doc("fund_a", body=b"")
        session = _FakeSession({doc_a.url: _FakeResponse(body=b"")})

        outcomes = download_documents(session, [doc_a], self.output_root, continue_on_error=True)

        self.assertEqual(outcomes[0].status, "failed")
        self.assertIn("did not return a ZIP/XLSX payload", outcomes[0].error)

    def test_html_error_page_saved_as_xlsx_is_reported_as_a_failed_outcome(self):
        doc_a, _ = _doc("fund_a")
        session = _FakeSession({doc_a.url: _FakeResponse(body=b"<html>404 not found</html>")})

        outcomes = download_documents(session, [doc_a], self.output_root, continue_on_error=True)

        self.assertEqual(outcomes[0].status, "failed")

    def test_filename_collision_aborts_before_any_network_call_regardless_of_continue_on_error(self):
        doc_a, body_a = _doc("fund_a")
        doc_b = Document(
            amc="test_amc",
            period="2026-05",
            url="https://example.test/fund_b.xlsx",
            source_page_url="https://example.test/",
            filename="fund_a.xlsx",  # deliberately collides with doc_a's destination
            file_type="xlsx",
        )
        session = _FakeSession({doc_a.url: _FakeResponse(body=body_a)})

        with self.assertRaisesRegex(RuntimeError, "Filename collision"):
            download_documents(session, [doc_a, doc_b], self.output_root, continue_on_error=True)

        self.assertEqual(session.requested_urls, [])


class PerDocumentRetryTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.output_root = Path(self._tempdir.name)
        config = SimpleNamespace(extract_archives=True, keep_archives=False, delay_seconds=0, retry_total=2, retry_backoff=0)
        self._patch = patch("core.cli.settings", return_value=config)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_a_download_that_fails_twice_then_succeeds_is_reported_downloaded(self):
        doc_a, body_a = _doc("fund_a")
        session = _FakeSession({doc_a.url: [RuntimeError("flaky 1"), RuntimeError("flaky 2"), _FakeResponse(body=body_a)]})

        outcomes = download_documents(session, [doc_a], self.output_root)

        self.assertEqual(outcomes[0].status, "downloaded")
        self.assertEqual(len(session.requested_urls), 3)  # 2 failed attempts + the successful one
        self.assertTrue((self.output_root / "fund_a.xlsx").exists())

    def test_a_persistent_failure_still_gives_up_after_the_configured_attempts(self):
        doc_a, _ = _doc("fund_a")
        session = _FakeSession({doc_a.url: RuntimeError("persistent failure")})

        outcomes = download_documents(session, [doc_a], self.output_root, continue_on_error=True)

        self.assertEqual(outcomes[0].status, "failed")
        self.assertEqual(len(session.requested_urls), 3)  # retry_total=2 -> 3 attempts total

    def test_retry_total_zero_means_no_retries_matching_pre_retry_behavior(self):
        doc_a, _ = _doc("fund_a")
        session = _FakeSession({doc_a.url: RuntimeError("persistent failure")})
        config = SimpleNamespace(extract_archives=True, keep_archives=False, delay_seconds=0, retry_total=0, retry_backoff=0)

        with patch("core.cli.settings", return_value=config):
            outcomes = download_documents(session, [doc_a], self.output_root, continue_on_error=True)

        self.assertEqual(len(session.requested_urls), 1)
        self.assertEqual(outcomes[0].status, "failed")

    def test_default_continue_on_error_false_still_raises_after_exhausting_retries(self):
        doc_a, _ = _doc("fund_a")
        session = _FakeSession({doc_a.url: RuntimeError("persistent failure")})

        with self.assertRaises(RuntimeError):
            download_documents(session, [doc_a], self.output_root)

        self.assertEqual(len(session.requested_urls), 3)

    def test_a_config_without_retry_fields_defaults_to_no_retries(self):
        # A caller passing a minimal config without retry_total/retry_backoff
        # at all (existing tests, e.g. test_http_timeouts.py) must keep
        # behaving exactly as it did before this feature existed.
        doc_a, _ = _doc("fund_a")
        session = _FakeSession({doc_a.url: RuntimeError("persistent failure")})
        minimal_config = SimpleNamespace(extract_archives=False, keep_archives=False, delay_seconds=0)

        with patch("core.cli.settings", return_value=minimal_config):
            with self.assertRaises(RuntimeError):
                download_documents(session, [doc_a], self.output_root)

        self.assertEqual(len(session.requested_urls), 1)

    def test_retrying_a_second_document_does_not_affect_a_document_that_succeeded_first_try(self):
        doc_a, body_a = _doc("fund_a")
        doc_b, body_b = _doc("fund_b")
        session = _FakeSession(
            {
                doc_a.url: _FakeResponse(body=body_a),
                doc_b.url: [RuntimeError("flaky"), _FakeResponse(body=body_b)],
            }
        )

        outcomes = download_documents(session, [doc_a, doc_b], self.output_root)

        self.assertEqual([outcome.status for outcome in outcomes], ["downloaded", "downloaded"])
        self.assertEqual(session.requested_urls.count(doc_a.url), 1)
        self.assertEqual(session.requested_urls.count(doc_b.url), 2)


if __name__ == "__main__":
    unittest.main()

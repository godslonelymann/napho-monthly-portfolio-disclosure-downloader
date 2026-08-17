from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.cli import DownloadOutcome, download_documents
from core.discovery import Document


class _FakeResponse:
    def __init__(self, *, body: bytes = b"", status: int = 200, headers: dict | None = None):
        self.body = body
        self.status = status
        self.status_code = status
        self.headers = headers or {}
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
        self.sent_headers: list[dict] = []

    def get(self, url, **kwargs):
        self.requested_urls.append(url)
        self.sent_headers.append(dict(kwargs.get("headers") or {}))
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


class ManifestDeduplicationTests(unittest.TestCase):
    """A re-run must not re-fetch a file the host says is unchanged."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.output_root = Path(self._tempdir.name)
        config = SimpleNamespace(extract_archives=True, keep_archives=False, delay_seconds=0)
        self._patch = patch("core.cli.settings", return_value=config)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _first_run(self, document, body):
        session = _FakeSession({
            document.url: _FakeResponse(
                body=body,
                headers={"ETag": '"abc123"', "Last-Modified": "Wed, 18 Jun 2025 14:29:21 GMT"},
            )
        })
        download_documents(session, [document], self.output_root)
        return json.loads((self.output_root / "manifest.json").read_text())

    def test_the_first_download_records_the_hosts_validators(self):
        document, body = _doc("fund_a")

        manifest = self._first_run(document, body)

        entry = manifest["downloads"][document.url]
        self.assertEqual(entry["etag"], '"abc123"')
        self.assertEqual(entry["last_modified"], "Wed, 18 Jun 2025 14:29:21 GMT")
        self.assertEqual(entry["sha256"], hashlib.sha256(body).hexdigest())

    def test_a_second_run_asks_the_host_whether_anything_changed(self):
        document, body = _doc("fund_a")
        self._first_run(document, body)

        session = _FakeSession({document.url: _FakeResponse(status=304)})
        outcomes = download_documents(session, [document], self.output_root)

        self.assertEqual(session.sent_headers[0]["If-None-Match"], '"abc123"')
        self.assertEqual(session.sent_headers[0]["If-Modified-Since"], "Wed, 18 Jun 2025 14:29:21 GMT")
        self.assertEqual(outcomes[0].status, "skipped")

    def test_a_304_leaves_the_file_and_its_manifest_entry_untouched(self):
        document, body = _doc("fund_a")
        manifest_before = self._first_run(document, body)

        session = _FakeSession({document.url: _FakeResponse(status=304)})
        download_documents(session, [document], self.output_root)

        self.assertEqual((self.output_root / "fund_a.xlsx").read_bytes(), body)
        manifest_after = json.loads((self.output_root / "manifest.json").read_text())
        self.assertEqual(manifest_after["downloads"], manifest_before["downloads"])

    def test_a_changed_file_is_downloaded_again_when_the_host_answers_200(self):
        document, body = _doc("fund_a")
        self._first_run(document, body)
        new_body = b"PK\x03\x04 newer xlsx"

        session = _FakeSession({document.url: _FakeResponse(body=new_body, headers={"ETag": '"def456"'})})
        outcomes = download_documents(session, [document], self.output_root)

        self.assertEqual(outcomes[0].status, "downloaded")
        self.assertEqual((self.output_root / "fund_a.xlsx").read_bytes(), new_body)

    def test_a_file_deleted_from_disk_is_re_downloaded_rather_than_asked_about(self):
        # Without this the host could answer 304 for a file that is no
        # longer there, and the run would report it as still present.
        document, body = _doc("fund_a")
        self._first_run(document, body)
        (self.output_root / "fund_a.xlsx").unlink()

        session = _FakeSession({document.url: _FakeResponse(body=body, headers={"ETag": '"abc123"'})})
        outcomes = download_documents(session, [document], self.output_root)

        self.assertNotIn("If-None-Match", session.sent_headers[0])
        self.assertEqual(outcomes[0].status, "downloaded")

    def test_a_file_modified_on_disk_since_the_manifest_was_written_is_re_downloaded(self):
        document, body = _doc("fund_a")
        self._first_run(document, body)
        (self.output_root / "fund_a.xlsx").write_bytes(b"PK\x03\x04 tampered")

        session = _FakeSession({document.url: _FakeResponse(body=body, headers={"ETag": '"abc123"'})})
        outcomes = download_documents(session, [document], self.output_root)

        self.assertNotIn("If-None-Match", session.sent_headers[0])
        self.assertEqual((self.output_root / "fund_a.xlsx").read_bytes(), body)
        self.assertEqual(outcomes[0].status, "downloaded")


class InterruptedDownloadTests(unittest.TestCase):
    """A killed run leaves a .part file behind; the next run must not trust it."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.output_root = Path(self._tempdir.name)
        config = SimpleNamespace(extract_archives=True, keep_archives=False, delay_seconds=0)
        self._patch = patch("core.cli.settings", return_value=config)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_a_leftover_part_file_is_never_treated_as_the_download(self):
        document, body = _doc("fund_a")
        stale = self.output_root / ".portfolio-interrupted.part"
        stale.write_bytes(b"PK\x03\x04 half a workbook")
        session = _FakeSession({document.url: _FakeResponse(body=body)})

        download_documents(session, [document], self.output_root)

        manifest = json.loads((self.output_root / "manifest.json").read_text())
        self.assertEqual(manifest["downloads"][document.url]["path"], "fund_a.xlsx")
        self.assertEqual((self.output_root / "fund_a.xlsx").read_bytes(), body)

    def test_a_failed_download_leaves_no_part_file_behind(self):
        document, _ = _doc("fund_a")
        session = _FakeSession({document.url: _FakeResponse(body=b"<html>error</html>")})

        download_documents(session, [document], self.output_root, continue_on_error=True)

        self.assertEqual(list(self.output_root.glob("*.part")), [])

    def test_a_failed_download_does_not_leave_a_partial_destination_file(self):
        document, _ = _doc("fund_a")
        session = _FakeSession({document.url: _FakeResponse(body=b"<html>error</html>")})

        download_documents(session, [document], self.output_root, continue_on_error=True)

        self.assertFalse((self.output_root / "fund_a.xlsx").exists())

    def test_an_interrupted_run_keeps_the_files_it_did_finish(self):
        doc_a, body_a = _doc("fund_a")
        doc_b, _ = _doc("fund_b")
        session = _FakeSession({doc_a.url: _FakeResponse(body=body_a), doc_b.url: RuntimeError("connection reset")})

        with self.assertRaises(RuntimeError):
            download_documents(session, [doc_a, doc_b], self.output_root)

        manifest = json.loads((self.output_root / "manifest.json").read_text())
        self.assertIn(doc_a.url, manifest["downloads"])
        self.assertEqual(list(self.output_root.glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()

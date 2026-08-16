from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.cli import run_cli
from core.discovery import Document, DiscoveryResult, PeriodUnavailable


class _FakeResponse:
    def __init__(self, *, body: bytes = b"PK\x03\x04 fake xlsx"):
        self.body = body

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size: int):
        yield self.body

    def close(self):
        pass


class _FakeSession:
    default_timeout = (3, 7)
    retry_total = 2

    def __init__(self, responses: dict[str, object]):
        self.responses = responses

    def get(self, url, **kwargs):
        outcome = self.responses[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _doc(name: str, scheme: str | None = None) -> Document:
    return Document(
        amc="test_amc",
        period="2026-05",
        url=f"https://example.test/{name}.xlsx",
        source_page_url="https://example.test/",
        filename=f"{name}.xlsx",
        file_type="xlsx",
        scheme=scheme or name,
    )


class RunCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.output_dir = Path(self._tempdir.name)

    def _config(self, **overrides):
        base = dict(
            period="2026-05",
            output_dir=self.output_dir,
            download=True,
            max_files=0,
            delay_seconds=0,
            connect_timeout=30,
            read_timeout=120,
            discovery_timeout=30,
            retry_total=2,
            retry_backoff=0,  # tests don't need real backoff delay between per-document retries
            process_timeout=600,
            headless=True,
            user_agent="test-agent",
            extract_archives=False,
            keep_archives=False,
            validate=True,
            validate_only=False,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _destination(self, amc: str) -> Path:
        return self.output_dir / amc / "2026-05"


class SuccessPathTests(RunCliTestCase):
    def test_full_success_returns_zero_and_writes_expected_and_validation_reports(self):
        docs = [_doc("fund_a"), _doc("fund_b")]
        session = _FakeSession({doc.url: _FakeResponse() for doc in docs})

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=lambda period, session: docs, session=session)

        self.assertEqual(code, 0)
        destination = self._destination("test_amc")
        self.assertTrue((destination / ".expected.json").exists())
        self.assertTrue((destination / ".validation.json").exists())
        validation = json.loads((destination / ".validation.json").read_text())
        self.assertEqual(validation["status"], "SUCCESS")
        self.assertEqual(validation["discovered"], 2)
        self.assertEqual(validation["downloaded"], 2)


class IncompleteAndFailureTests(RunCliTestCase):
    def test_one_failed_download_is_reported_incomplete_not_raised(self):
        good, bad = _doc("fund_a"), _doc("fund_b")
        session = _FakeSession({good.url: _FakeResponse(), bad.url: RuntimeError("simulated failure")})

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=lambda period, session: [good, bad], session=session)

        self.assertEqual(code, 5)  # INCOMPLETE
        validation = json.loads((self._destination("test_amc") / ".validation.json").read_text())
        self.assertEqual(validation["status"], "INCOMPLETE")
        self.assertEqual(validation["missing"], 1)

    def test_every_download_failing_is_download_failed_not_a_crash(self):
        doc = _doc("fund_a")
        session = _FakeSession({doc.url: RuntimeError("simulated failure")})

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=lambda period, session: [doc], session=session)

        self.assertEqual(code, 6)  # DOWNLOAD_FAILED


class PeriodUnavailableTests(RunCliTestCase):
    def test_period_unavailable_still_returns_two_and_skips_validation_entirely(self):
        def discover(period, session):
            raise PeriodUnavailable("nothing published this month")

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=_FakeSession({}))

        self.assertEqual(code, 2)
        self.assertFalse(self._destination("test_amc").exists())


class MaxFilesTruncationTests(RunCliTestCase):
    def test_truncated_discovery_is_partial_by_config_even_if_every_attempted_file_succeeds(self):
        docs = [_doc("fund_a"), _doc("fund_b"), _doc("fund_c")]
        session = _FakeSession({doc.url: _FakeResponse() for doc in docs})

        with patch("core.cli.settings", return_value=self._config(max_files=2)):
            code = run_cli(amc="test_amc", discover=lambda period, session: docs, session=session)

        self.assertEqual(code, 8)  # PARTIAL_BY_CONFIG
        validation = json.loads((self._destination("test_amc") / ".validation.json").read_text())
        self.assertEqual(validation["discovered"], 2)
        self.assertEqual(validation["downloaded"], 2)
        expected = json.loads((self._destination("test_amc") / ".expected.json").read_text())
        self.assertEqual(expected["discovery_notes"]["full_discovered_count"], 3)


class ValidateOptOutTests(RunCliTestCase):
    def test_amc_validate_false_restores_old_raise_on_first_failure_behavior(self):
        good, bad = _doc("fund_a"), _doc("fund_b")
        session = _FakeSession({good.url: _FakeResponse(), bad.url: RuntimeError("simulated failure")})

        with patch("core.cli.settings", return_value=self._config(validate=False)):
            with self.assertRaises(RuntimeError):
                run_cli(amc="test_amc", discover=lambda period, session: [good, bad], session=session)

        # No new report artifacts under the old code path.
        self.assertFalse((self._destination("test_amc") / ".expected.json").exists())
        self.assertFalse((self._destination("test_amc") / ".validation.json").exists())


class _NoDownloadsAllowedSession:
    """A session that fails the test the moment anything tries to download --
    validate_only mode must never call .get() at all."""

    default_timeout = (3, 7)
    retry_total = 0

    def get(self, url, **kwargs):
        raise AssertionError(f"a download was attempted for {url}, but validate_only mode must never download anything")


class ValidateOnlyTests(RunCliTestCase):
    def _write_existing_download(self, destination: Path, document: Document, content: bytes) -> None:
        import hashlib
        import json as json_module

        destination.mkdir(parents=True, exist_ok=True)
        (destination / document.filename).write_bytes(content)
        manifest_path = destination / "manifest.json"
        manifest = json_module.loads(manifest_path.read_text()) if manifest_path.exists() else {"downloads": {}}
        manifest["downloads"][document.url] = {
            "amc": document.amc,
            "period": document.period,
            "scheme": document.scheme,
            "source_page_url": document.source_page_url,
            "path": document.filename,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_type": None,
            "downloaded_at": "2026-05-01T00:00:00+00:00",
            "metadata": {},
        }
        manifest_path.write_text(json_module.dumps(manifest))

    def test_a_month_that_was_fully_downloaded_before_reports_success_without_downloading_anything(self):
        docs = [_doc("fund_a"), _doc("fund_b")]
        destination = self._destination("test_amc")
        self._write_existing_download(destination, docs[0], b"PK\x03\x04 fund a")
        self._write_existing_download(destination, docs[1], b"PK\x03\x04 fund b")

        with patch("core.cli.settings", return_value=self._config(validate_only=True)):
            code = run_cli(
                amc="test_amc",
                discover=lambda period, session: docs,
                session=_NoDownloadsAllowedSession(),
            )

        self.assertEqual(code, 0)
        validation = json.loads((destination / ".validation.json").read_text())
        self.assertEqual(validation["status"], "SUCCESS")
        self.assertEqual(validation["downloaded"], 2)

    def test_a_never_downloaded_amc_reports_download_failed_not_success(self):
        docs = [_doc("fund_a")]

        with patch("core.cli.settings", return_value=self._config(validate_only=True)):
            code = run_cli(
                amc="test_amc",
                discover=lambda period, session: docs,
                session=_NoDownloadsAllowedSession(),
            )

        self.assertEqual(code, 6)  # DOWNLOAD_FAILED
        validation = json.loads((self._destination("test_amc") / ".validation.json").read_text())
        self.assertEqual(validation["missing"], 1)

    def test_a_partially_downloaded_amc_reports_incomplete_with_the_real_gap(self):
        docs = [_doc("fund_a"), _doc("fund_b"), _doc("fund_c")]
        destination = self._destination("test_amc")
        self._write_existing_download(destination, docs[0], b"PK\x03\x04 fund a")
        self._write_existing_download(destination, docs[1], b"PK\x03\x04 fund b")
        # fund_c was never downloaded in the original run.

        with patch("core.cli.settings", return_value=self._config(validate_only=True)):
            code = run_cli(
                amc="test_amc",
                discover=lambda period, session: docs,
                session=_NoDownloadsAllowedSession(),
            )

        self.assertEqual(code, 5)  # INCOMPLETE
        validation = json.loads((destination / ".validation.json").read_text())
        self.assertEqual(validation["discovered"], 3)
        self.assertEqual(validation["downloaded"], 2)
        self.assertEqual(validation["missing"], 1)

    def test_validate_only_overrides_the_amc_validate_false_escape_hatch(self):
        # AMC_VALIDATE=0 alone means "trust it, don't check" -- but
        # AMC_VALIDATE_ONLY=1 is an explicit request to check, so it must
        # win even if AMC_VALIDATE also happens to be off.
        docs = [_doc("fund_a")]

        with patch("core.cli.settings", return_value=self._config(validate=False, validate_only=True)):
            code = run_cli(
                amc="test_amc",
                discover=lambda period, session: docs,
                session=_NoDownloadsAllowedSession(),
            )

        self.assertEqual(code, 6)  # DOWNLOAD_FAILED -- validation actually ran
        self.assertTrue((self._destination("test_amc") / ".validation.json").exists())

    def test_rediscoverable_is_a_no_op_in_validate_only_mode(self):
        docs = [_doc("fund_a")]
        calls = []

        def discover(period, session):
            calls.append(1)
            return docs

        with patch("core.cli.settings", return_value=self._config(validate_only=True)):
            run_cli(
                amc="test_amc",
                discover=discover,
                session=_NoDownloadsAllowedSession(),
                rediscoverable=True,
            )

        self.assertEqual(len(calls), 1)  # never re-discovers even though everything is "missing"


class DiscoveryResultTests(RunCliTestCase):
    def test_adapter_notes_from_a_discovery_result_land_in_expected_json(self):
        docs = [_doc("fund_a")]
        session = _FakeSession({docs[0].url: _FakeResponse()})
        notes = {"total_schemes_offered": 3, "not_published": ["Fund X", "Fund Y"]}

        def discover(period, session):
            return DiscoveryResult(documents=docs, notes=notes)

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=session)

        self.assertEqual(code, 0)
        expected = json.loads((self._destination("test_amc") / ".expected.json").read_text())
        self.assertEqual(expected["discovery_notes"]["total_schemes_offered"], 3)
        self.assertEqual(expected["discovery_notes"]["not_published"], ["Fund X", "Fund Y"])

    def test_a_plain_document_list_still_works_exactly_as_before(self):
        # Every adapter except Bandhan returns list[Document] directly --
        # confirm the DiscoveryResult branch didn't change that path.
        docs = [_doc("fund_a")]
        session = _FakeSession({docs[0].url: _FakeResponse()})

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=lambda period, session: docs, session=session)

        self.assertEqual(code, 0)
        expected = json.loads((self._destination("test_amc") / ".expected.json").read_text())
        self.assertEqual(expected["discovery_notes"], {})

    def test_max_files_truncation_note_is_merged_with_adapter_notes_not_overwritten(self):
        docs = [_doc("fund_a"), _doc("fund_b")]
        session = _FakeSession({doc.url: _FakeResponse() for doc in docs})
        notes = {"total_schemes_offered": 2}

        def discover(period, session):
            return DiscoveryResult(documents=docs, notes=notes)

        with patch("core.cli.settings", return_value=self._config(max_files=1)):
            code = run_cli(amc="test_amc", discover=discover, session=session)

        self.assertEqual(code, 8)  # PARTIAL_BY_CONFIG
        expected = json.loads((self._destination("test_amc") / ".expected.json").read_text())
        self.assertEqual(expected["discovery_notes"]["total_schemes_offered"], 2)
        self.assertEqual(expected["discovery_notes"]["full_discovered_count"], 2)


class RediscoverableTests(RunCliTestCase):
    """rediscoverable=True: after a run ends with missing files, run_cli
    re-runs discover() once to check whether they're still listed at all.
    """

    def test_a_missing_file_confirmed_gone_by_fresh_discovery_becomes_site_changed(self):
        good, gone = _doc("fund_a"), _doc("fund_b")
        session = _FakeSession({good.url: _FakeResponse(), gone.url: RuntimeError("simulated failure")})
        calls = []

        def discover(period, session):
            calls.append(1)
            # First call (the real one): both documents. Second call (the
            # re-discovery check): the site no longer lists fund_b at all.
            return [good, gone] if len(calls) == 1 else [good]

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=session, rediscoverable=True)

        self.assertEqual(code, 9)  # SITE_CHANGED
        self.assertEqual(len(calls), 2)
        validation = json.loads((self._destination("test_amc") / ".validation.json").read_text())
        self.assertEqual(validation["status"], "SITE_CHANGED")
        self.assertTrue(validation["site_changed"])

    def test_a_missing_file_still_listed_by_fresh_discovery_stays_incomplete(self):
        good, still_missing = _doc("fund_a"), _doc("fund_b")
        session = _FakeSession({good.url: _FakeResponse(), still_missing.url: RuntimeError("simulated failure")})

        def discover(period, session):
            # Every call (including the re-discovery check) still lists both.
            return [good, still_missing]

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=session, rediscoverable=True)

        self.assertEqual(code, 5)  # still INCOMPLETE -- the file really is missing
        validation = json.loads((self._destination("test_amc") / ".validation.json").read_text())
        self.assertEqual(validation["status"], "INCOMPLETE")
        self.assertFalse(validation["site_changed"])

    def test_rediscoverable_false_by_default_never_calls_discover_a_second_time(self):
        good, missing = _doc("fund_a"), _doc("fund_b")
        session = _FakeSession({good.url: _FakeResponse(), missing.url: RuntimeError("simulated failure")})
        calls = []

        def discover(period, session):
            calls.append(1)
            return [good, missing]

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=session)  # rediscoverable defaults to False

        self.assertEqual(code, 5)  # INCOMPLETE, no override
        self.assertEqual(len(calls), 1)  # discover() was never called a second time

    def test_a_re_discovery_failure_does_not_crash_the_run_or_change_the_status(self):
        good, missing = _doc("fund_a"), _doc("fund_b")
        session = _FakeSession({good.url: _FakeResponse(), missing.url: RuntimeError("simulated failure")})
        calls = []

        def discover(period, session):
            calls.append(1)
            if len(calls) == 1:
                return [good, missing]
            raise RuntimeError("site is down for the re-discovery check too")

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=session, rediscoverable=True)

        self.assertEqual(code, 5)  # still INCOMPLETE -- re-discovery itself failing isn't evidence of anything
        self.assertEqual(len(calls), 2)

    def test_a_period_unavailable_on_rediscovery_confirms_the_whole_period_vanished(self):
        good, gone = _doc("fund_a"), _doc("fund_b")
        session = _FakeSession({good.url: _FakeResponse(), gone.url: RuntimeError("simulated failure")})
        calls = []

        def discover(period, session):
            calls.append(1)
            if len(calls) == 1:
                return [good, gone]
            raise PeriodUnavailable("the whole month disappeared between discovery and download")

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=session, rediscoverable=True)

        self.assertEqual(code, 9)  # SITE_CHANGED

    def test_rediscovery_is_skipped_entirely_when_nothing_is_missing(self):
        docs = [_doc("fund_a"), _doc("fund_b")]
        session = _FakeSession({doc.url: _FakeResponse() for doc in docs})
        calls = []

        def discover(period, session):
            calls.append(1)
            return docs

        with patch("core.cli.settings", return_value=self._config()):
            code = run_cli(amc="test_amc", discover=discover, session=session, rediscoverable=True)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)  # nothing missing -> no reason to re-check


class DownloadDisabledTests(RunCliTestCase):
    def test_download_false_just_prints_found_urls_and_skips_validation(self):
        docs = [_doc("fund_a")]

        with patch("core.cli.settings", return_value=self._config(download=False)):
            code = run_cli(amc="test_amc", discover=lambda period, session: docs, session=_FakeSession({}))

        self.assertEqual(code, 0)
        self.assertFalse(self._destination("test_amc").exists())


if __name__ == "__main__":
    unittest.main()

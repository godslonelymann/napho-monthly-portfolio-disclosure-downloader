from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from core.discovery import Document
from core.http import create_session, fetch_response
from core.cli import download_documents


class _TimeoutSession:
    default_timeout = (3, 7)
    retry_total = 2

    def get(self, *args, **kwargs):
        raise requests.Timeout("simulated timeout")


class HttpTimeoutTests(unittest.TestCase):
    def test_discovery_timeout_message_has_url_phase_and_attempt_budget(self):
        with self.assertRaisesRegex(RuntimeError, r"url=https://example.test/catalog phase=discovery attempts<=3"):
            fetch_response(_TimeoutSession(), "https://example.test/catalog", phase="discovery")

    def test_download_timeout_message_identifies_the_download_url_and_phase(self):
        document = Document(
            amc="example",
            period="2026-05",
            url="https://example.test/book.xlsx",
            source_page_url="https://example.test/",
            filename="book.xlsx",
            file_type="xlsx",
        )
        config = SimpleNamespace(
            extract_archives=False,
            keep_archives=False,
            delay_seconds=0,
        )
        with patch("core.cli.settings", return_value=config):
            with self.assertRaisesRegex(RuntimeError, r"url=https://example.test/book.xlsx phase=download attempts<=3"):
                with tempfile.TemporaryDirectory() as directory:
                    download_documents(_TimeoutSession(), [document], Path(directory))

    def test_created_session_exposes_bounded_retry_and_discovery_settings(self):
        config = SimpleNamespace(
            retry_total=2,
            retry_backoff=0.5,
            connect_timeout=10,
            read_timeout=120,
            discovery_timeout=30,
            user_agent="test-agent",
        )
        with patch("core.http.settings", return_value=config):
            session = create_session()

        self.assertEqual(session.retry_total, 2)
        self.assertEqual(session.default_timeout, (10, 120))
        self.assertEqual(session.discovery_timeout, 30)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from core.discovery import PeriodUnavailable
from verified import Aditya_Birla_Sun_Life_Mutual_Fund as aditya_birla
from verified import Invesco_Mutual_Fund as invesco
from verified import Motilal_Oswal_Mutual_Fund as motilal
from verified import Old_Bridge_Mutual_Fund as old_bridge
from verified import The_Wealth_Company_Mutual_Fund as wealth
from verified import JM_Financial_Mutual_Fund as jm_financial

from Crypto.Cipher import AES


class DiscoveryRegressionTests(unittest.TestCase):
    @staticmethod
    def _fixture(name):
        path = Path(__file__).parent / "fixtures" / "jm_financial" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def test_jm_uses_monthly_records_from_current_catalog_without_select_options(self):
        records = self._fixture("page_1.json")
        with patch.object(jm_financial, "_fetch_records", return_value=records):
            documents = jm_financial.discover("2026-07", session=object())

        self.assertEqual(len(documents), 1)
        self.assertIn("July 31, 2026", documents[0].label)
        self.assertTrue(documents[0].url.startswith("https://www.jmfinancialmf.com/CMS/"))
        self.assertEqual(documents[0].metadata["subcategory"], "Monthly Portfolio of Schemes")

    def test_jm_historical_catalog_is_not_limited_to_the_current_page(self):
        records = self._fixture("page_1.json") + self._fixture("page_2.json")
        with patch.object(jm_financial, "_fetch_records", return_value=records):
            documents = jm_financial.discover("2026-05", session=object())

        self.assertEqual(len(documents), 2)
        self.assertTrue(all(document.period == "2026-05" for document in documents))

    def test_jm_returns_period_unavailable_for_a_valid_catalog_without_period(self):
        records = self._fixture("page_1.json")
        with patch.object(jm_financial, "_fetch_records", return_value=records):
            with self.assertRaises(PeriodUnavailable):
                jm_financial.discover("2026-05", session=object())

    def test_jm_rejects_an_unrecognizable_api_envelope_as_adapter_failure(self):
        with self.assertRaisesRegex(RuntimeError, "unrecognizable response envelope"):
            jm_financial._records_from_payload({"unexpected": []})

    def test_jm_api_request_uses_the_unfiltered_catalog_not_subcategory_select(self):
        records = self._fixture("page_1.json")
        plaintext = json.dumps(records, ensure_ascii=False).encode("latin1")
        padding = AES.block_size - len(plaintext) % AES.block_size
        encrypted = AES.new(jm_financial._AES_KEY, AES.MODE_CBC, jm_financial._AES_IV).encrypt(
            plaintext + bytes([padding]) * padding
        )
        captured = {}

        def fake_post_json(session, url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs["json"]
            return {"data": base64.b64encode(encrypted).decode("ascii")}

        config = SimpleNamespace(connect_timeout=3, discovery_timeout=5)
        with patch.object(jm_financial, "post_json", side_effect=fake_post_json), patch.object(
            jm_financial, "settings", return_value=config
        ):
            fetched = jm_financial._fetch_records(object())

        self.assertEqual(len(fetched), len(records))
        self.assertEqual(captured["payload"]["IICategoryID"], "2")
        self.assertEqual(captured["payload"]["IISubCategoryID"], "0")

    def test_wealth_company_scopes_dates_to_each_record(self):
        records = [
            {
                "uploadDate": "2026-06-30",
                "name": "Monthly - Wealth Fund - June 30, 2026",
                "attachment": {"url": "/uploads/june.xlsx"},
            },
            {
                "uploadDate": "2026-05-31",
                "name": "Monthly - Wealth Fund - May 31, 2026",
                "attachment": {"url": "/uploads/may.xlsx"},
            },
        ]
        streamed = ('"downloads":' + json.dumps(records, separators=(",", ":"))).replace('"', '\\"')

        with patch.object(wealth, "fetch_text", side_effect=[streamed, "<html></html>"]):
            documents = wealth.discover("2026-06")

        self.assertEqual([document.filename for document in documents], ["june.xlsx"])
        self.assertEqual(documents[0].metadata["uploadDate"], "2026-06-30")

    def test_invesco_requests_fixed_income_classification(self):
        requested = []

        def fake_fetch_json(session, url, **kwargs):
            classification = parse_qs(urlsplit(url).query)["classification"][0]
            requested.append(classification)
            if classification == "fixed-income":
                return [{"Name": "Debt Fund", "JunUrl": "https://example.com/docs/debt_jun_2026.xlsx"}]
            return []

        with patch.object(invesco, "fetch_json", side_effect=fake_fetch_json):
            documents = invesco.discover("2026-06")

        self.assertIn("fixed-income", requested)
        self.assertNotIn("debt", requested)
        self.assertEqual(len(documents), 1)

    def test_motilal_uses_title_not_malformed_filename_or_publish_month(self):
        payload = {
            "results": [
                {
                    "path": "/content/dam/2026/jul/Scheme Portfolio Details June 20261.xlsx",
                    "title": "Scheme Portfolio Details June 2026",
                    "category": "month end portfolio",
                },
                {
                    "path": "/content/dam/2026/jun/Scheme Portfolio Details 31-05-2026.xlsx",
                    "title": "Scheme Portfolio Details May 2026",
                    "category": "month end portfolio",
                },
            ]
        }
        with patch.object(motilal, "fetch_json", return_value=payload):
            documents = motilal.discover("2026-06")

        self.assertEqual(len(documents), 1)
        self.assertIn("June 20261.xlsx", documents[0].url)

    def test_adapter_specific_empty_result_diagnostic_is_reachable(self):
        with patch.object(motilal, "fetch_json", return_value={"results": []}):
            with self.assertRaisesRegex(RuntimeError, "Motilal Oswal search-documents"):
                motilal.discover("2026-06")

    def test_aditya_birla_returns_unavailable_for_empty_requested_period(self):
        records = [{"ResourceLink": "Monthly Portfolio May 31, 2026", "pdfUrl": "/may.zip"}]
        with (
            patch.object(aditya_birla, "discover_endpoint", return_value="https://example.test/api"),
            patch.object(aditya_birla, "fetch_disclosures", return_value=records),
        ):
            with self.assertRaises(PeriodUnavailable):
                aditya_birla.discover("2026-06", session=object())

    def test_aditya_birla_returns_the_matching_period_as_a_single_zip_document(self):
        records = [
            {"ResourceLink": "Monthly Portfolio June 30, 2026", "pdfUrl": "/june.zip"},
            {"ResourceLink": "Monthly Portfolio May 31, 2026", "pdfUrl": "/may.zip"},
        ]
        with (
            patch.object(aditya_birla, "discover_endpoint", return_value="https://example.test/api"),
            patch.object(aditya_birla, "fetch_disclosures", return_value=records),
        ):
            documents = aditya_birla.discover("2026-05", session=object())

        self.assertEqual(len(documents), 1)
        self.assertTrue(documents[0].url.endswith("/may.zip"))
        self.assertEqual(documents[0].file_type, "zip")
        self.assertEqual(documents[0].scheme, "consolidated")

    def test_aditya_birla_keeps_the_first_matching_record_when_the_list_has_duplicates(self):
        # AccordionList is newest-first; the first record for a period wins.
        records = [
            {"ResourceLink": "Monthly Portfolio May 31, 2026", "pdfUrl": "/may-latest.zip"},
            {"ResourceLink": "Monthly Portfolio May 31, 2026", "pdfUrl": "/may-older.zip"},
        ]
        with (
            patch.object(aditya_birla, "discover_endpoint", return_value="https://example.test/api"),
            patch.object(aditya_birla, "fetch_disclosures", return_value=records),
        ):
            documents = aditya_birla.discover("2026-05", session=object())

        self.assertEqual(len(documents), 1)
        self.assertTrue(documents[0].url.endswith("/may-latest.zip"))

    def test_old_bridge_marks_absent_period_unavailable_when_archive_is_valid(self):
        html = '<a href="/uploads/monthly-portfolio-may-2026.xlsx">Monthly Portfolio May 2026</a>'
        with patch.object(old_bridge, "fetch_text", return_value=html):
            with self.assertRaises(PeriodUnavailable):
                old_bridge.discover("2026-06")

    def test_old_bridge_still_flags_unrecognizable_page_structure(self):
        with patch.object(old_bridge, "fetch_text", return_value="<html><body>No files</body></html>"):
            with self.assertRaisesRegex(RuntimeError, "no recognizable"):
                old_bridge.discover("2026-06")


if __name__ == "__main__":
    unittest.main()

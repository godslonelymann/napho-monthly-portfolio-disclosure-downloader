from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from core.discovery import PeriodUnavailable
from verified import HSBC_Mutual_Fund as hsbc


def _fixture() -> str:
    path = Path(__file__).parent / "fixtures" / "hsbc" / "information_library_sample.html"
    return path.read_text(encoding="utf-8")


class HsbcAsOfPeriodRegressionTests(unittest.TestCase):
    """HSBC files each link under a folder named for its *publish* date, which
    is not always the month the data is actually as of, and several filenames
    also embed a scheme's own maturity date (e.g. "...gilt-june-2027...").
    Naively treating "the requested period appears anywhere in the URL" as a
    match let both kinds of unrelated dates be mistaken for the as-of date.
    These pin the as-of resolution (filename -> link text -> folder, most
    recent date not in the future) against real HSBC URLs/labels captured
    from the live site.
    """

    def setUp(self) -> None:
        patcher = patch.object(hsbc, "fetch_text", return_value=_fixture())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_current_month_folder_matches_its_own_period(self):
        documents = hsbc.discover("2026-07", session=object())
        self.assertEqual(len(documents), 3)
        self.assertTrue(all(doc.period == "2026-07" for doc in documents))

    def test_publish_date_folder_does_not_leak_into_its_own_month(self):
        # document-08012025/ is filed under January but holds December 2024
        # data -- 2025-01 must not pick up these three files.
        with self.assertRaises(PeriodUnavailable):
            hsbc.discover("2025-01", session=object())

    def test_publish_date_folder_resolves_to_the_month_its_data_is_actually_for(self):
        documents = hsbc.discover("2024-12", session=object())
        self.assertEqual(len(documents), 3)
        for doc in documents:
            self.assertIn("dec-2024", doc.url)

    def test_publish_date_folder_does_not_leak_into_its_own_month_2024_06(self):
        # document-06062024/ is filed under June but holds May 2024 data.
        with self.assertRaises(PeriodUnavailable):
            hsbc.discover("2024-06", session=object())

    def test_publish_date_folder_resolves_to_the_month_its_data_is_actually_for_2024_05(self):
        documents = hsbc.discover("2024-05", session=object())
        self.assertEqual(len(documents), 3)
        for doc in documents:
            self.assertIn("may-2024", doc.url)

    def test_scheme_maturity_date_in_filename_is_not_mistaken_for_the_as_of_date(self):
        # hsbc-crisil-ibx-gilt-june-2027-fund.xlsx names no as-of date in the
        # filename at all -- "june-2027" is the scheme's maturity, not data
        # as of June 2027 (which is in the future relative to every date
        # this fixture describes, so it must never match any real period).
        with self.assertRaises(PeriodUnavailable):
            hsbc.discover("2027-06", session=object())

    def test_scheme_maturity_date_falls_back_to_the_folder_when_filename_has_no_as_of_date(self):
        # Same file as above: with no as-of date in the filename or a usable
        # one in the link text, the folder's 31 Jan 2024 is the last resort.
        documents = hsbc.discover("2024-01", session=object())
        self.assertEqual(len(documents), 1)
        self.assertIn("gilt-june-2027", documents[0].url)

    def test_filename_without_a_date_falls_back_to_link_text(self):
        # documents-31122023/hsbc-aggressive-hybrid-fund.xlsx has no date in
        # the filename; the as-of date only appears in the link text.
        documents = hsbc.discover("2023-12", session=object())
        self.assertEqual(len(documents), 3)

    def test_filename_without_a_date_falls_back_to_folder_when_link_text_also_bare(self):
        # document-30042023/hsbc-aggressive-hybrid-fund.xlsx: no date in the
        # filename, and the link text's own date agrees with the folder.
        documents = hsbc.discover("2023-04", session=object())
        self.assertEqual(len(documents), 3)

    def test_folder_naming_march_actually_holds_august_data_so_march_is_unavailable(self):
        # document-07032023/ is dated 7 March 2023 but every file inside it
        # is "as on 31 August 2023" -- HSBC never published a March 2023
        # snapshot at this URL, so discovery must say so rather than hand
        # back August's files under a March label.
        with self.assertRaises(PeriodUnavailable):
            hsbc.discover("2023-03", session=object())

    def test_folder_resolves_to_the_month_its_data_is_actually_for(self):
        documents = hsbc.discover("2023-08", session=object())
        self.assertEqual(len(documents), 3)

    def test_legacy_combined_workbook_resolves_from_link_text_only(self):
        # Pre-2019 era: a single combined workbook with no date anywhere in
        # its filename or path, only in the link text.
        documents = hsbc.discover("2018-10", session=object())
        self.assertEqual(len(documents), 1)
        self.assertTrue(documents[0].url.endswith("monthly-portfolio-all-schemes.xlsx"))

    def test_as_of_cutoff_uses_the_real_current_month_not_the_requested_period(self):
        # The future-date guard must reject dates later than *today*, not
        # merely later than whatever period was requested -- otherwise
        # requesting a far-future period would let scheme-maturity dates
        # like "gilt-june-2027" match again.
        far_future = f"{date.today().year + 5:04d}-01"
        with self.assertRaises(PeriodUnavailable):
            hsbc.discover(far_future, session=object())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from core.discovery import Document
from core.expectations import (
    ExpectationSet,
    destination_filename,
    detect_duplicates,
    from_documents,
    identity_key,
    normalized_url,
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


class NormalizedUrlTests(unittest.TestCase):
    def test_strips_cache_busting_query_params(self):
        self.assertEqual(
            normalized_url("https://example.com/a.xlsx?sfvrsn=3"),
            normalized_url("https://example.com/a.xlsx?sfvrsn=9"),
        )

    def test_keeps_meaningful_query_params_distinct(self):
        # NJ Mutual Fund's real identity lives in ?file=..., not the path.
        self.assertNotEqual(
            normalized_url("https://example.com/viewfile.php?file=Scheme-A.xlsx"),
            normalized_url("https://example.com/viewfile.php?file=Scheme-B.xlsx"),
        )

    def test_is_case_and_order_insensitive_where_it_should_be(self):
        self.assertEqual(
            normalized_url("HTTPS://Example.com/a.xlsx?b=2&a=1"),
            normalized_url("https://example.com/a.xlsx?a=1&b=2"),
        )

    def test_ignores_fragment(self):
        self.assertEqual(
            normalized_url("https://example.com/a.xlsx#section"),
            normalized_url("https://example.com/a.xlsx"),
        )


class DestinationFilenameTests(unittest.TestCase):
    def test_prefers_adapter_supplied_filename_over_url_basename(self):
        # Mirrors NJ Mutual Fund: generic path, real name in the query string.
        document = _doc(
            url="https://example.com/viewfile.php?file=Scheme-A.xlsx",
            filename="Scheme-A.xlsx",
        )
        self.assertEqual(destination_filename(document), "Scheme-A.xlsx")

    def test_falls_back_to_url_basename_without_a_filename(self):
        document = _doc(url="https://example.com/files/report.pdf", filename="", file_type="pdf")
        self.assertEqual(destination_filename(document), "report.pdf")

    def test_sanitizes_unsafe_characters(self):
        document = _doc(filename="Scheme: A / B?.xlsx")
        self.assertNotIn("/", destination_filename(document))
        self.assertNotIn(":", destination_filename(document))


class IdentityKeyTests(unittest.TestCase):
    def test_scheme_wins_over_url_when_present(self):
        one = _doc(url="https://example.com/a.xlsx", scheme="Fund A")
        two = _doc(url="https://example.com/a-v2.xlsx", scheme="Fund A")
        self.assertEqual(identity_key(one), identity_key(two))

    def test_falls_back_to_normalized_url_without_a_scheme(self):
        one = _doc(url="https://example.com/a.xlsx?sfvrsn=1", scheme=None)
        two = _doc(url="https://example.com/a.xlsx?sfvrsn=2", scheme=None)
        self.assertEqual(identity_key(one), identity_key(two))

    def test_same_scheme_different_period_does_not_collide(self):
        one = _doc(period="2026-05", scheme="Fund A")
        two = _doc(period="2026-06", scheme="Fund A")
        self.assertNotEqual(identity_key(one), identity_key(two))


class DetectDuplicatesTests(unittest.TestCase):
    def test_same_link_discovered_twice_is_a_repeated_url_not_a_collision(self):
        documents = [_doc(), _doc()]
        report = detect_duplicates(documents)
        self.assertEqual(len(report.repeated_url), 1)
        self.assertFalse(report.filename_collision)
        self.assertFalse(report.has_blocking_issues)

    def test_same_scheme_two_urls_is_a_duplicate_listing(self):
        # "same file exposed twice on website" via two different URLs.
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A"),
            _doc(url="https://example.com/a-mirror.xlsx", scheme="Fund A"),
        ]
        report = detect_duplicates(documents)
        self.assertEqual(len(report.duplicate_listing), 1)
        self.assertEqual(len(report.duplicate_listing[0]["urls"]), 2)

    def test_two_distinct_files_would_collide_on_disk(self):
        # Two different schemes whose adapter-supplied filenames happen to
        # sanitize to the same destination name.
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="portfolio.xlsx"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B", filename="portfolio.xlsx"),
        ]
        report = detect_duplicates(documents)
        self.assertEqual(len(report.filename_collision), 1)
        self.assertTrue(report.has_blocking_issues)

    def test_same_url_different_filename_is_flagged(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", filename="one.xlsx"),
            _doc(url="https://example.com/a.xlsx", filename="two.xlsx"),
        ]
        report = detect_duplicates(documents)
        self.assertEqual(len(report.url_filename_conflict), 1)

    def test_distinct_documents_produce_no_duplicates_at_all(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", filename="fund_a.xlsx"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B", filename="fund_b.xlsx"),
        ]
        report = detect_duplicates(documents)
        self.assertFalse(report.repeated_url)
        self.assertFalse(report.duplicate_listing)
        self.assertFalse(report.filename_collision)
        self.assertFalse(report.url_filename_conflict)


class FromDocumentsTests(unittest.TestCase):
    def test_builds_one_item_per_distinct_identity(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B"),
        ]
        expectations = from_documents(documents)
        self.assertEqual(expectations.count, 2)
        self.assertEqual(expectations.amc, "test_amc")
        self.assertEqual(expectations.period, "2026-05")

    def test_repeated_links_collapse_to_a_single_item(self):
        expectations = from_documents([_doc(), _doc()])
        self.assertEqual(expectations.count, 1)

    def test_zero_documents_is_refused_rather_than_silently_empty(self):
        with self.assertRaises(ValueError):
            from_documents([])

    def test_mixed_amcs_are_refused(self):
        with self.assertRaises(ValueError):
            from_documents([_doc(amc="one"), _doc(amc="two", url="https://example.com/x.xlsx")])

    def test_primary_document_wins_a_duplicate_listing(self):
        primary = _doc(url="https://example.com/a-primary.xlsx", scheme="Fund A", primary=True)
        secondary = _doc(url="https://example.com/a-mirror.xlsx", scheme="Fund A", primary=False)
        expectations = from_documents([secondary, primary])
        self.assertEqual(expectations.count, 1)
        self.assertEqual(expectations.items[0].url, primary.url)

    def test_archive_kind_is_guessed_from_zip_file_type(self):
        documents = [_doc(file_type="zip", url="https://example.com/bundle.zip", filename="bundle.zip")]
        expectations = from_documents(documents)
        self.assertEqual(expectations.items[0].kind, "archive")

    def test_source_pages_are_collected_and_deduped(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A", source_page_url="https://example.com/page1"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B", source_page_url="https://example.com/page1"),
            _doc(url="https://example.com/c.xlsx", scheme="Fund C", source_page_url="https://example.com/page2"),
        ]
        expectations = from_documents(documents)
        self.assertEqual(expectations.source_pages, ("https://example.com/page1", "https://example.com/page2"))

    def test_truncated_by_max_files_flag_is_carried_through(self):
        expectations = from_documents([_doc()], truncated_by_max_files=True)
        self.assertTrue(expectations.truncated_by_max_files)

    def test_discovery_notes_are_carried_through(self):
        notes = {"schemes": {"Fund X": {"status": "not_published"}}}
        expectations = from_documents([_doc()], discovery_notes=notes)
        self.assertEqual(expectations.discovery_notes, notes)


class RoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_round_trips(self):
        documents = [
            _doc(url="https://example.com/a.xlsx", scheme="Fund A"),
            _doc(url="https://example.com/b.xlsx", scheme="Fund B"),
        ]
        original = from_documents(documents, discovery_notes={"note": "ok"})
        restored = ExpectationSet.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())


if __name__ == "__main__":
    unittest.main()

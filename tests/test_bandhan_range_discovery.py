"""Regression tests for Bandhan's listing-API discovery.

The adapter drives a browser only to make the site's own encrypted API call
and to read its own decrypted answer; everything that decides what gets
downloaded from that answer is plain data handling, and that is what these
cover -- URL extraction, pagination, period coverage across 2017-2026,
duplicate handling, and the "explicit answer for every period" contract the
range runner depends on.
"""

from __future__ import annotations

import json
import unittest

import backfill_range
from verified import Bandhan_Mutual_Fund as bandhan


def _row(*, scheme: str, name: str, urls: list[str], month: str = "September") -> dict:
    return {
        "title": f"Monthly and Half-Yearly &#8211; {name}",
        "acf_fields": {
            "month": month,
            "document_name": name,
            "funds_mapping": {"post_title": scheme},
            "disclosure_files": [{"url": url} for url in urls],
        },
    }


def _payload(rows: list[dict], **overrides) -> dict:
    payload = {
        "status": 200,
        "data": rows,
        "financial_years": ["2026", "2025", "2024", "2023", "2022", "2021", "2020"],
        "months": ["September", "October", "November", "December"],
        "scheme_titles": [],
        "total_posts": len(rows),
        "posts_per_page": bandhan.PER_PAGE,
        "max_pages": 1,
        "current_page": 1,
    }
    payload.update(overrides)
    return payload


class DirectUrlTests(unittest.TestCase):
    def test_a_storage_url_is_used_as_is(self):
        url = "https://storage.googleapis.com/nonprod-static-assets/2026/08/fund-31-july-2026.xlsx"
        self.assertEqual(bandhan.direct_url(url), url)

    def test_the_filepath_parameter_of_the_download_shim_wins_over_the_shim_url(self):
        target = "https://storage.googleapis.com/nonprod-static-assets/2026/08/fund.xlsx"
        shim = (
            "https://pnservices.bandhanmutual.com/investor/v1/dashboard/download-doc"
            f"?filepath={target}&fname=fund.xlsx"
        )
        self.assertEqual(bandhan.direct_url(shim), target)

    def test_a_percent_encoded_filepath_is_decoded(self):
        shim = (
            "https://pnservices.bandhanmutual.com/investor/v1/dashboard/download-doc"
            "?filepath=https%3A%2F%2Fstorage.googleapis.com%2Fbucket%2Ffund%20a.xlsx&fname=x.xlsx"
        )
        self.assertEqual(bandhan.direct_url(shim), "https://storage.googleapis.com/bucket/fund a.xlsx")

    def test_a_shim_whose_filepath_is_not_a_usable_url_keeps_the_shim(self):
        # A relative or empty filepath is not something requests can fetch on
        # its own; the shim URL at least still resolves.
        shim = "https://bandhanmutual.com/investor/v1/dashboard/download-doc?filepath=&fname=x.xlsx"
        self.assertEqual(bandhan.direct_url(shim), shim)


class RecordExtractionTests(unittest.TestCase):
    def test_scheme_label_url_and_filename_are_taken_from_the_listing_row(self):
        payload = _payload([
            _row(
                scheme="Bandhan Credit Risk Fund",
                name="IDFC Credit Risk Fund - 30 Sept 2021",
                urls=["https://cms.example/IDFC-Credit-Risk-Fund-30-Sept-2021.xlsx"],
            )
        ])

        records = bandhan.records_from_payload(payload, "2021-09")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheme"], "Bandhan Credit Risk Fund")
        self.assertEqual(records[0]["url"], "https://cms.example/IDFC-Credit-Risk-Fund-30-Sept-2021.xlsx")
        self.assertEqual(records[0]["filename"], "bandhan_credit_risk_fund_2021-09.xlsx")

    def test_a_growth_plan_suffix_is_dropped_from_the_scheme_identity(self):
        payload = _payload([
            _row(
                scheme="Bandhan Fixed Term Plan - Series 179 - Growth",
                name="IDFC Fixed Term Plan Series 179 (3652 days) - 30 Sept 2021",
                urls=["https://cms.example/ftp-179-30-sept-2021.xlsx"],
            )
        ])

        records = bandhan.records_from_payload(payload, "2021-09")

        self.assertEqual(records[0]["scheme"], "Bandhan Fixed Term Plan - Series 179")

    def test_the_same_url_listed_twice_produces_one_record(self):
        url = "https://cms.example/IDFC-Credit-Risk-Fund-30-Sept-2021.xlsx"
        payload = _payload([
            _row(scheme="Bandhan Credit Risk Fund", name="IDFC Credit Risk Fund - 30 Sept 2021", urls=[url, url]),
            _row(scheme="Bandhan Credit Risk Fund", name="IDFC Credit Risk Fund - 30 Sept 2021", urls=[url]),
        ])

        records = bandhan.records_from_payload(payload, "2021-09")

        self.assertEqual([record["url"] for record in records], [url])

    def test_two_documents_for_one_scheme_get_distinct_filenames(self):
        # A monthly and a half-yearly workbook for the same scheme in the
        # same month would otherwise both be written to one path, and
        # core.cli treats that collision as a hard discovery error.
        payload = _payload([
            _row(
                scheme="Bandhan Credit Risk Fund",
                name="IDFC Credit Risk Fund - 30 Sept 2021",
                urls=["https://cms.example/monthly-30-sept-2021.xlsx"],
            ),
            _row(
                scheme="Bandhan Credit Risk Fund",
                name="IDFC Credit Risk Fund Half Yearly - 30 Sept 2021",
                urls=["https://cms.example/half-yearly-30-sept-2021.xlsx"],
            ),
        ])

        records = bandhan.records_from_payload(payload, "2021-09")

        filenames = {record["filename"] for record in records}
        self.assertEqual(len(filenames), 2)
        self.assertTrue(all(name.endswith("_2021-09.xlsx") for name in filenames))

    def test_two_documents_whose_labels_slugify_identically_still_get_distinct_filenames(self):
        payload = _payload([
            _row(scheme="Bandhan X Fund", name="Portfolio!! 30 Sept 2021", urls=["https://cms.example/a-30-sept-2021.xlsx"]),
            _row(scheme="Bandhan X Fund", name="Portfolio?? 30 Sept 2021", urls=["https://cms.example/b-30-sept-2021.xlsx"]),
        ])

        records = bandhan.records_from_payload(payload, "2021-09")

        self.assertEqual(len({record["filename"] for record in records}), 2)

    def test_a_row_dated_for_another_period_is_reported_not_saved_under_this_one(self):
        # Real behaviour of the site: its only December-2020 entry is a
        # workbook dated 31 Dec 2022. Saving that as 2020-12 would file one
        # month's holdings as another's.
        payload = _payload([
            _row(
                scheme="Bandhan Medium Duration Fund",
                name="IDFC Bond Fund - Medium Term Plan - 31 Dec 2022",
                urls=["https://cms.example/IDFC-Bond-Fund-Medium-Term-Plan-31-Dec-2022.xlsx"],
                month="December",
            )
        ])
        misfiled: list[dict] = []

        records = bandhan.records_from_payload(payload, "2020-12", misfiled)

        self.assertEqual(records, [])
        self.assertEqual(len(misfiled), 1)
        self.assertEqual(misfiled[0]["document_period"], "2022-12")
        self.assertEqual(misfiled[0]["listed_under"], "2020-12")

    def test_a_row_whose_month_field_disagrees_with_the_requested_month_is_skipped(self):
        payload = _payload([
            _row(
                scheme="Bandhan Liquid Fund",
                name="IDFC Liquid Fund - 31 Oct 2021",
                urls=["https://cms.example/liquid-31-oct-2021.xlsx"],
                month="October",
            )
        ])

        self.assertEqual(bandhan.records_from_payload(payload, "2021-09"), [])

    def test_the_upload_directory_in_a_storage_url_is_not_read_as_the_as_of_date(self):
        # The URL path carries the month the file was *uploaded*
        # (.../2024/09/...), which for back-filled documents is years after
        # the month the data is about.
        payload = _payload([
            _row(
                scheme="Bandhan Liquid Fund",
                name="IDFC Liquid Fund - 30 Sept 2021",
                urls=["https://cms.example/wp-content/uploads/2024/09/IDFC-Liquid-Fund-30-Sept-2021.xlsx"],
            )
        ])

        self.assertEqual(len(bandhan.records_from_payload(payload, "2021-09")), 1)

    def test_a_row_without_a_downloadable_file_is_ignored(self):
        payload = _payload([
            _row(scheme="Bandhan Liquid Fund", name="IDFC Liquid Fund - 30 Sept 2021", urls=[]),
            {"acf_fields": {"month": "September", "document_name": "x - 30 Sept 2021", "disclosure_files": [{"url": ""}]}},
        ])

        self.assertEqual(bandhan.records_from_payload(payload, "2021-09"), [])


class PaginationTests(unittest.TestCase):
    def _paged_fetch(self, pages: dict[int, dict]):
        requested: list[int] = []

        def fetch(period, page_number):
            requested.append(page_number)
            return pages[page_number]

        return fetch, requested

    def test_every_page_of_a_multi_page_listing_is_walked(self):
        pages = {
            1: _payload([_row(scheme="Fund A", name="Fund A - 30 Sept 2021", urls=["https://cms.example/a.xlsx"])], max_pages=3, current_page=1),
            2: _payload([_row(scheme="Fund B", name="Fund B - 30 Sept 2021", urls=["https://cms.example/b.xlsx"])], max_pages=3, current_page=2),
            3: _payload([_row(scheme="Fund C", name="Fund C - 30 Sept 2021", urls=["https://cms.example/c.xlsx"])], max_pages=3, current_page=3),
        }
        fetch, requested = self._paged_fetch(pages)

        _, records = bandhan.collect_records(fetch, "2021-09")

        self.assertEqual(requested, [1, 2, 3])
        self.assertEqual(
            sorted(record["url"] for record in records),
            ["https://cms.example/a.xlsx", "https://cms.example/b.xlsx", "https://cms.example/c.xlsx"],
        )

    def test_a_row_repeated_across_two_pages_is_only_downloaded_once(self):
        row = _row(scheme="Fund A", name="Fund A - 30 Sept 2021", urls=["https://cms.example/a.xlsx"])
        pages = {
            1: _payload([row], max_pages=2, current_page=1),
            2: _payload([row], max_pages=2, current_page=2),
        }
        fetch, _ = self._paged_fetch(pages)

        _, records = bandhan.collect_records(fetch, "2021-09")

        self.assertEqual(len(records), 1)

    def test_a_single_page_listing_makes_exactly_one_request(self):
        pages = {1: _payload([_row(scheme="Fund A", name="Fund A - 30 Sept 2021", urls=["https://cms.example/a.xlsx"])])}
        fetch, requested = self._paged_fetch(pages)

        bandhan.collect_records(fetch, "2021-09")

        self.assertEqual(requested, [1])

    def test_pagination_stops_at_the_guard_even_if_the_site_claims_absurdly_many_pages(self):
        def fetch(period, page_number):
            return _payload(
                [_row(scheme=f"Fund {page_number}", name=f"Fund {page_number} - 30 Sept 2021", urls=[f"https://cms.example/{page_number}.xlsx"])],
                max_pages=10_000,
                current_page=page_number,
            )

        _, records = bandhan.collect_records(fetch, "2021-09")

        self.assertEqual(len(records), bandhan._MAX_PAGES_GUARD)

    def test_a_no_posts_found_first_page_yields_no_records(self):
        def fetch(period, page_number):
            return {"status": "no_posts_found"}

        first, records = bandhan.collect_records(fetch, "2017-05")

        self.assertEqual(records, [])
        self.assertEqual(first["status"], "no_posts_found")


class ResponseSelectionTests(unittest.TestCase):
    def test_the_pages_own_unmodified_response_is_not_mistaken_for_ours(self):
        summaries = [{"posts_per_page": 10, "current_page": 1}]

        self.assertIsNone(bandhan.capture_index(summaries, 1))

    def test_our_own_response_is_recognised_by_page_size_and_page_number(self):
        summaries = [{"posts_per_page": 10, "current_page": 1}, {"posts_per_page": bandhan.PER_PAGE, "current_page": 2}]

        self.assertEqual(bandhan.capture_index(summaries, 2), 1)

    def test_a_response_for_a_different_page_number_is_not_accepted(self):
        summaries = [{"posts_per_page": bandhan.PER_PAGE, "current_page": 1}]

        self.assertIsNone(bandhan.capture_index(summaries, 2))

    def test_nothing_captured_yet_reads_as_not_landed(self):
        self.assertIsNone(bandhan.capture_index([], 1))

    def test_no_posts_found_is_selected_so_the_caller_can_classify_it(self):
        summaries = [{"status": "no_posts_found"}]

        self.assertEqual(bandhan.capture_index(summaries, 1), 0)


class QueryTests(unittest.TestCase):
    def test_every_period_in_the_2017_2026_range_produces_a_well_formed_query(self):
        for year in range(2017, 2027):
            for month in range(1, 13):
                period = f"{year}-{month:02d}"
                with self.subTest(period=period):
                    script = bandhan._init_script(period, 1)
                    payload = json.loads(script.split("const QUERY = ", 1)[1].split(";\n", 1)[0])
                    self.assertEqual(payload["type"], bandhan.API_REQUEST_TYPE)
                    self.assertEqual(payload["data"]["financial_year"], str(year))
                    self.assertEqual(payload["data"]["posts_per_page"], bandhan.PER_PAGE)
                    self.assertNotIn("acf_value1", payload["data"])

    def test_the_metadata_probe_asks_for_no_particular_period(self):
        script = bandhan._init_script(None, 1)
        payload = json.loads(script.split("const QUERY = ", 1)[1].split(";\n", 1)[0])

        self.assertIsNone(payload["data"]["financial_year"])
        self.assertIsNone(payload["data"]["month"])

    def test_no_api_key_rsa_key_or_signature_is_embedded_anywhere_in_the_adapter(self):
        # The site's request signing stays entirely with the site's own JS.
        source = open(bandhan.__file__, encoding="utf-8").read().lower()
        for forbidden in ("x-api-key", "begin rsa", "begin public key", "fingerprintjs", "aes-", "signature="):
            self.assertNotIn(forbidden, source)


class AbsenceReasonTests(unittest.TestCase):
    def test_a_year_the_site_does_not_offer_is_named_as_such(self):
        reason = bandhan._absence_reason({}, _payload([]), "2017-05")

        self.assertIn("does not list 2017", reason)
        self.assertIn("2020", reason)

    def test_a_month_missing_from_an_available_year_is_named_as_such(self):
        payload = _payload([], months=["December"])

        reason = bandhan._absence_reason(payload, None, "2020-06")

        self.assertIn("no June in 2020", reason)
        self.assertIn("December", reason)

    def test_an_available_year_and_month_with_nothing_published_falls_back_to_the_generic_reason(self):
        payload = _payload([], months=["September"])

        self.assertEqual(
            bandhan._absence_reason(payload, None, "2021-09"),
            "Bandhan lists no monthly portfolio documents for 2021-09",
        )

    def test_every_absence_reason_is_classified_as_no_data_by_the_range_runner(self):
        # The range runner must record a real "checked, nothing there"
        # answer for these, never UNKNOWN_ERROR -- otherwise it retries
        # 36 months of a year the site simply doesn't publish, every run.
        payloads = [
            ({}, _payload([]), "2017-05"),
            (_payload([], months=["December"]), None, "2020-06"),
            (_payload([], months=["September"]), None, "2021-09"),
        ]
        for payload, fallback, period in payloads:
            with self.subTest(period=period):
                reason = bandhan._absence_reason(payload, fallback, period)
                self.assertTrue(
                    backfill_range.looks_like_no_data(reason),
                    f"range runner would not classify this as NO_DATA: {reason}",
                )


class SchemeReportTests(unittest.TestCase):
    def test_every_scheme_the_site_offers_gets_an_explicit_verdict(self):
        payload = _payload([], scheme_titles=["Bandhan Liquid Fund", "Bandhan Gilt Fund", "Bandhan Value Fund - Growth"])
        records = [{"scheme": "Bandhan Liquid Fund", "label": "x", "url": "https://cms.example/a.xlsx", "filename": "a.xlsx"}]

        report = bandhan.build_scheme_report(payload, records)

        self.assertEqual(report["Bandhan Liquid Fund"]["status"], "found")
        self.assertEqual(report["Bandhan Gilt Fund"]["status"], "not_published")
        self.assertEqual(report["Bandhan Value Fund"]["status"], "not_published")
        self.assertEqual(len(report), 3)

    def test_a_document_for_a_scheme_the_dropdown_no_longer_lists_is_still_recorded(self):
        payload = _payload([], scheme_titles=["Bandhan Liquid Fund"])
        records = [{"scheme": "IDFC Renamed Fund", "label": "x", "url": "https://cms.example/a.xlsx", "filename": "a.xlsx"}]

        report = bandhan.build_scheme_report(payload, records)

        self.assertEqual(report["IDFC Renamed Fund"]["status"], "found")

    def test_the_notes_summary_counts_what_the_site_confirmed_absent(self):
        payload = _payload([], scheme_titles=["Bandhan Liquid Fund", "Bandhan Gilt Fund"])
        records = [{"scheme": "Bandhan Liquid Fund", "label": "x", "url": "https://cms.example/a.xlsx", "filename": "a.xlsx"}]

        summary = bandhan._discovery_notes_summary(bandhan.build_scheme_report(payload, records))

        self.assertEqual(summary["total_schemes_offered"], 2)
        self.assertEqual(summary["status_counts"], {"found": 1, "not_published": 1})
        self.assertEqual(summary["not_published"], ["Bandhan Gilt Fund"])


def _summary_row(*, name: str, urls: list[str], month: str = "January") -> dict:
    return {
        "title": name,
        "acf_fields": {
            "month": month,
            "document_name": name,
            "disclosure_files": [{"url": url} for url in urls],
        },
    }


class SummaryKindTests(unittest.TestCase):
    # Real basenames observed live from downloads/portfolio-summary/monthly
    # -- both 2025-01 rows below share the exact same document_name
    # ("Bandhan Debt Fund Portfolio as on 31-jan-2025"), so the kind must
    # come from the URL, not the label, or the two workbooks collide.
    def test_an_equity_hybrid_workbook_is_recognised_from_its_url_despite_a_debt_labelled_title(self):
        url = (
            "https://storage.googleapis.com/nonprod-static-assets/2026/05/"
            "a2b3415e-bandhan-equity-hybrid-fund-portfolios-as-on-31-jan-2025.xlsx"
        )
        self.assertEqual(bandhan._summary_kind(url, "Bandhan Debt Fund Portfolio as on 31-jan-2025"), "equity_hybrid")

    def test_a_debt_workbook_is_recognised_from_its_url(self):
        url = (
            "https://storage.googleapis.com/nonprod-static-assets/2026/05/"
            "be06bade-bandhan-debt-fund-portfolios-as-on-31-jan-2025.xlsx"
        )
        self.assertEqual(bandhan._summary_kind(url, "Bandhan Debt Fund Portfolio as on 31-jan-2025"), "debt")

    def test_debt_is_recognised_even_without_word_boundaries_in_the_slug(self):
        url = (
            "https://storage.googleapis.com/nonprod-static-assets/2026/05/"
            "4268d487-bandhan-debtfundportfolioason-28-11-2025.xlsx"
        )
        self.assertEqual(bandhan._summary_kind(url, "Bandhan Debt Fund Portfolio as on 28-11-2025"), "debt")

    def test_a_copy_suffixed_basename_is_still_recognised_as_debt(self):
        url = (
            "https://storage.googleapis.com/nonprod-static-assets/2026/05/"
            "6cbdb928-bandhan-debt-fund-portfolio-as-on-30-04-2026-1.xlsx"
        )
        self.assertEqual(bandhan._summary_kind(url, "Bandhan Debt Fund Portfolio as on 30-04-2026"), "debt")

    def test_arbitrage_is_recognised(self):
        url = "https://storage.googleapis.com/nonprod-static-assets/2026/05/x-bandhan-arbitrage-fund-portfolios-31-jan-2025.xlsx"
        self.assertEqual(bandhan._summary_kind(url, "x"), "arbitrage")

    def test_an_unrecognised_url_falls_back_to_the_label(self):
        self.assertEqual(bandhan._summary_kind("https://cms.example/", "Some Debt Workbook"), "debt")


class SummaryRecordExtractionTests(unittest.TestCase):
    def test_two_same_titled_rows_get_distinct_filenames_by_url_derived_kind(self):
        # Real 2025-01 shape: identical document_name, different files.
        payload = _payload(
            [
                _summary_row(
                    name="Bandhan Debt Fund Portfolio as on 31-jan-2025",
                    urls=["https://cms.example/a2b3415e-bandhan-equity-hybrid-fund-portfolios-as-on-31-jan-2025.xlsx"],
                ),
                _summary_row(
                    name="Bandhan Debt Fund Portfolio as on 31-jan-2025",
                    urls=["https://cms.example/be06bade-bandhan-debt-fund-portfolios-as-on-31-jan-2025.xlsx"],
                ),
            ]
        )

        records = bandhan.summary_records_from_payload(payload, "2025-01")

        kinds = {record["kind"] for record in records}
        filenames = {record["filename"] for record in records}
        self.assertEqual(kinds, {"equity_hybrid", "debt"})
        self.assertEqual(len(filenames), 2)
        self.assertIn("bandhan_summary_equity_hybrid_2025-01.xlsx", filenames)
        self.assertIn("bandhan_summary_debt_2025-01.xlsx", filenames)

    def test_the_same_url_listed_twice_produces_one_record(self):
        url = "https://cms.example/be06bade-bandhan-debt-fund-portfolios-as-on-31-jan-2025.xlsx"
        payload = _payload(
            [
                _summary_row(name="Bandhan Debt Fund Portfolio as on 31-jan-2025", urls=[url, url]),
            ]
        )

        records = bandhan.summary_records_from_payload(payload, "2025-01")

        self.assertEqual([record["url"] for record in records], [url])

    def test_a_row_dated_for_another_period_is_reported_not_saved_under_this_one(self):
        payload = _payload(
            [
                _summary_row(
                    name="Debt Fund Portfolios 31 December 2022",
                    urls=["https://cms.example/debt-fund-portfolios-31-dec-2022.xlsx"],
                    month="December",
                )
            ]
        )
        misfiled: list[dict] = []

        records = bandhan.summary_records_from_payload(payload, "2020-12", misfiled)

        self.assertEqual(records, [])
        self.assertEqual(len(misfiled), 1)
        self.assertEqual(misfiled[0]["document_period"], "2022-12")

    def test_a_row_without_a_downloadable_file_is_ignored(self):
        payload = _payload(
            [
                _summary_row(name="Debt Fund Portfolios 31 Jan 2025", urls=[]),
            ]
        )

        self.assertEqual(bandhan.summary_records_from_payload(payload, "2025-01"), [])


class SummaryCollectionTests(unittest.TestCase):
    def test_every_page_of_a_multi_page_summary_listing_is_walked(self):
        pages = {
            1: _payload(
                [_summary_row(name="Debt Fund Portfolios 31 Jan 2025", urls=["https://cms.example/a.xlsx"])],
                max_pages=2,
                current_page=1,
            ),
            2: _payload(
                [_summary_row(name="Equity Hybrid Fund Portfolios 31 Jan 2025", urls=["https://cms.example/b.xlsx"])],
                max_pages=2,
                current_page=2,
            ),
        }
        requested: list[int] = []

        def fetch(period, page_number):
            requested.append(page_number)
            return pages[page_number]

        _, records = bandhan.collect_summary_records(fetch, "2025-01")

        self.assertEqual(requested, [1, 2])
        self.assertEqual(sorted(record["url"] for record in records), ["https://cms.example/a.xlsx", "https://cms.example/b.xlsx"])

    def test_a_no_posts_found_first_page_yields_no_records(self):
        def fetch(period, page_number):
            return {"status": "no_posts_found"}

        first, records = bandhan.collect_summary_records(fetch, "2019-05")

        self.assertEqual(records, [])
        self.assertEqual(first["status"], "no_posts_found")


class SummaryQueryTests(unittest.TestCase):
    def test_the_summary_query_keeps_the_month_filter_unlike_the_per_scheme_query(self):
        script = bandhan._summary_init_script("2025-01", 1)
        payload = json.loads(script.split("const QUERY = ", 1)[1].split(";\n", 1)[0])

        self.assertEqual(payload["type"], bandhan.SUMMARY_API_REQUEST_TYPE)
        self.assertEqual(payload["data"]["financial_year"], "2025")
        self.assertEqual(payload["data"]["month"], "January")
        self.assertEqual(payload["data"]["posts_per_page"], bandhan.SUMMARY_PER_PAGE)

    def test_the_summary_query_never_deletes_the_scheme_filter_keys(self):
        # There is no scheme dropdown on the summary page, so unlike
        # _INIT_SCRIPT_TEMPLATE, the summary hook must not strip
        # acf_key1/acf_value1 -- it never sets them in the first place.
        self.assertNotIn("acf_key1", bandhan._SUMMARY_INIT_SCRIPT_TEMPLATE)
        self.assertNotIn("delete data.acf_key1", bandhan._SUMMARY_INIT_SCRIPT_TEMPLATE)


class ExitCodeMergeTests(unittest.TestCase):
    # __main__ itself isn't unit-testable without subprocessing, but the
    # merge rule it implements is simple enough to pin down directly:
    # PeriodUnavailable (2) from *both* legs is the only case that should
    # read as an overall miss; any other non-zero code is a hard failure
    # that must win; otherwise overall success.
    def _merge(self, scheme_code: int, summary_code: int) -> int:
        codes = (scheme_code, summary_code)
        hard_failures = [code for code in codes if code not in (0, 2)]
        if hard_failures:
            return hard_failures[0]
        if all(code == 2 for code in codes):
            return 2
        return 0

    def test_both_legs_unavailable_is_an_overall_miss(self):
        self.assertEqual(self._merge(2, 2), 2)

    def test_one_leg_succeeding_is_an_overall_success(self):
        self.assertEqual(self._merge(0, 2), 0)
        self.assertEqual(self._merge(2, 0), 0)

    def test_a_hard_failure_in_either_leg_wins(self):
        self.assertEqual(self._merge(7, 0), 7)
        self.assertEqual(self._merge(0, 9), 9)
        self.assertEqual(self._merge(7, 2), 7)


if __name__ == "__main__":
    unittest.main()

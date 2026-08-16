from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_icra_coverage import (  # noqa: E402
    Unit,
    build_candidates,
    build_units,
    build_universe,
    canonical,
    despace,
    drop_brackets,
    filename_candidates,
    is_specific,
    map_amc_dirs,
    match_unit,
    plan_reductions,
    GAP_NONE,
    GAP_NOT_DOWNLOADED,
    GAP_NOT_IN_ICRA,
    STATUS_TO_GAP,
    scan_sheet,
    strip_tenor,
    token_set,
)


class TestCanonical(unittest.TestCase):
    def test_case_punctuation_and_whitespace(self):
        self.assertEqual(canonical("  Axis   Nifty 50 ETF  "), "axis nifty 50 etf")
        self.assertEqual(canonical("Axis Children's Fund"), "axis childrens fund")
        self.assertEqual(canonical("Banking & PSU"), "banking and psu")

    def test_letter_digit_boundaries_are_split(self):
        self.assertEqual(canonical("IBX50:50 Gilt"), canonical("IBX 50:50 Gilt"))
        self.assertEqual(canonical("Nifty500 Index"), canonical("Nifty 500 Index"))

    def test_month_names_are_folded_to_three_letters(self):
        self.assertEqual(canonical("SDL Sep 2027"), canonical("SDL September 2027"))
        self.assertEqual(canonical("Jun 2028 Index"), canonical("June 2028 Index"))

    def test_fund_of_fund_and_fmp_abbreviations(self):
        self.assertEqual(canonical("Gold ETF Fund of Funds"), canonical("Gold ETF FoF"))
        self.assertEqual(
            canonical("HDFC FMP 1861D"), canonical("HDFC Fixed Maturity Plan 1861D")
        )

    def test_plus_sign_matches_the_word(self):
        self.assertEqual(canonical("Gilt + SDL Index"), canonical("Gilt Plus SDL Index"))

    def test_roman_numerals_only_after_a_trigger_word(self):
        self.assertEqual(canonical("Interval Fund Series XLIII"),
                         "interval fund series 43")
        # A bare series letter must survive untouched.
        self.assertEqual(canonical("Debt Fund - C"), "debt fund c")

    def test_marketing_tail_is_cut(self):
        self.assertEqual(
            canonical("Samco Large Cap Fund AS ON May 31, 2026"),
            "samco large cap fund",
        )
        self.assertEqual(
            canonical("MONTHLY PORTFOLIO STATEMENT OF UNION LARGECAP FUND"),
            "union largecap fund",
        )


class TestReductions(unittest.TestCase):
    def test_plan_reductions_are_progressive(self):
        chain = plan_reductions("hdfc liquid fund direct growth")
        self.assertIn("hdfc liquid fund direct", chain)
        self.assertIn("hdfc liquid fund", chain)

    def test_plan_is_kept_on_the_unreduced_key(self):
        # "Medium Term Plan" is the scheme's real name, so the full key must
        # still be produced ahead of any reduction.
        cands = build_candidates("Aditya Birla Sun Life Medium Term Plan", "t")
        self.assertEqual(cands[0].key, "aditya birla sun life medium term plan")

    def test_strip_tenor(self):
        self.assertEqual(
            strip_tenor("sbi fixed maturity plan series 1 3668 days"),
            "sbi fixed maturity plan series 1",
        )

    def test_drop_brackets(self):
        self.assertEqual(
            drop_brackets("HDFC FMP 1861D March 2022 (A Close Ended Income Scheme)"),
            "HDFC FMP 1861D March 2022",
        )

    def test_token_set_is_order_independent_but_exact(self):
        self.assertEqual(token_set("b a c"), token_set("c b a"))
        self.assertNotEqual(token_set("plan 1861 d"), token_set("plan 1876 d"))

    def test_is_specific_rejects_eroded_keys(self):
        self.assertFalse(is_specific("income"))
        self.assertFalse(is_specific("fund"))
        self.assertTrue(is_specific("hdfc income fund"))

    def test_despace(self):
        self.assertEqual(despace("large midcap"), despace("largemidcap"))


class TestFilenameCandidates(unittest.TestCase):
    def test_numbers_in_the_scheme_name_survive(self):
        keys = {c.key for c in filename_candidates(
            Path("Monthly-Portfolio-May-2026-Angel-One-Nifty-50-Index-Fund.xlsx")
        )}
        self.assertIn("angel one nifty 50 index fund", keys)

    def test_month_token_does_not_eat_market(self):
        keys = {c.key for c in filename_candidates(
            Path("Monthly-Portfolio-May-2026-Angel-One-Nifty-Total-Market-Fund.xlsx")
        )}
        self.assertIn("angel one nifty total market fund", keys)


class TestAmcMapping(unittest.TestCase):
    def test_slug_and_manual_mappings(self):
        mapped = map_amc_dirs(
            ["axis", "wealth_company", "ilfs_idf"],
            ["Axis Mutual Fund", "The Wealth Company Mutual Fund", "IL & FS Mutual Fund"],
        )
        self.assertEqual(mapped["axis"], "Axis Mutual Fund")
        self.assertEqual(mapped["wealth_company"], "The Wealth Company Mutual Fund")
        self.assertEqual(mapped["ilfs_idf"], "IL & FS Mutual Fund")


def _universe(*schemes):
    portfolio = [
        {"amfi_code": code, "fund_name": name, "rows": 1} for code, name in schemes
    ]
    master = [
        {"amfi_code": code, "scheme_name": f"{name} - Direct - Growth",
         "mf_name": "Demo Mutual Fund"}
        for code, name in schemes
    ]
    return build_universe(portfolio, master)


def _unit(*texts, order_base=100):
    cands = []
    for i, t in enumerate(texts):
        cands.extend(build_candidates(t, f"sheet:S:row{i}", order=order_base + i))
    return Unit(label="S", candidates=cands, codes=[], detected_name=texts[0])


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.u = _universe(
            ("100001", "Demo Large Cap Fund"),
            ("100002", "Demo Small Cap Fund"),
            ("100003", "Demo Income Fund"),
        )

    def test_exact_name_match(self):
        res = match_unit(_unit("Demo Large Cap Fund"), self.u, "Demo Mutual Fund", {})
        self.assertEqual(res.status, "matched")
        self.assertEqual(res.method, "icra_name_exact")
        self.assertEqual(self.u.schemes[res.scheme_id].amfi_code, "100001")

    def test_amfi_code_wins(self):
        unit = _unit("Demo Small Cap Fund")
        unit = Unit(unit.label, unit.candidates, ["100003"], unit.detected_name)
        res = match_unit(unit, self.u, "Demo Mutual Fund", {})
        self.assertEqual(res.method, "amfi_code")
        self.assertEqual(self.u.schemes[res.scheme_id].fund_name, "Demo Income Fund")

    def test_scheme_master_alias(self):
        res = match_unit(
            _unit("Demo Small Cap Fund - Direct - Growth"), self.u, "Demo Mutual Fund", {}
        )
        self.assertEqual(res.status, "matched")
        self.assertEqual(self.u.schemes[res.scheme_id].amfi_code, "100002")

    def test_stray_generic_word_does_not_match(self):
        # The bug this guards: "Income" eroded down from "Demo Income Fund".
        res = match_unit(_unit("Income"), self.u, "Demo Mutual Fund", {})
        self.assertEqual(res.status, "unmatched")

    def test_earlier_title_beats_a_stray_later_mention(self):
        # A footer naming another scheme must not make the unit ambiguous.
        res = match_unit(
            _unit("Demo Small Cap Fund", "Demo Large Cap Fund"),
            self.u, "Demo Mutual Fund", {},
        )
        self.assertEqual(res.status, "matched")
        self.assertEqual(self.u.schemes[res.scheme_id].fund_name, "Demo Small Cap Fund")

    def test_near_miss_is_ambiguous_never_matched(self):
        res = match_unit(_unit("Demo Smal Cap Fund"), self.u, "Demo Mutual Fund", {})
        self.assertEqual(res.status, "ambiguous")
        self.assertEqual(res.method, "fuzzy_near_miss")
        self.assertIsNone(res.scheme_id)

    def test_amfi_navall_fallback(self):
        # NAVAll.txt maps one base name to several codes (one per plan/option);
        # the fallback accepts it only when they all lead to the same scheme.
        amfi = {canonical("Demo Growth Story Fund"): {"100002"}}
        res = match_unit(
            _unit("Demo Growth Story Fund"), self.u, "Demo Mutual Fund", amfi
        )
        self.assertEqual(res.method, "amfi_navall_code")
        self.assertEqual(self.u.schemes[res.scheme_id].amfi_code, "100002")

    def test_amfi_navall_rejects_codes_spanning_two_schemes(self):
        amfi = {canonical("Demo Growth Story Fund"): {"100001", "100002"}}
        res = match_unit(
            _unit("Demo Growth Story Fund"), self.u, "Demo Mutual Fund", amfi
        )
        self.assertNotEqual(res.method, "amfi_navall_code")
        self.assertIsNone(res.scheme_id)

    def test_gap_direction_labels_are_distinct(self):
        self.assertNotEqual(GAP_NOT_IN_ICRA, GAP_NOT_DOWNLOADED)
        self.assertEqual(STATUS_TO_GAP["unmatched"], GAP_NOT_IN_ICRA)
        self.assertEqual(STATUS_TO_GAP["matched"], GAP_NONE)


class TestSheetScanning(unittest.TestCase):
    def test_holdings_rows_are_not_titles(self):
        rows = [
            ("Demo Large Cap Fund", None),
            ("Name of the Instrument", "ISIN", "% to Net Assets"),
            ("Some Company Fund Ltd", "INE040A01034", "1.2"),
        ]
        scan = scan_sheet("S", iter(rows))
        titles = [t for _, t in scan.titles]
        self.assertIn("Demo Large Cap Fund", titles)
        self.assertNotIn("Some Company Fund Ltd", titles)
        self.assertTrue(scan.has_isin)
        self.assertTrue(scan.is_data_sheet)

    def test_non_portfolio_sheet_is_skipped(self):
        scan_data = scan_sheet("Main", iter([
            ("Demo Large Cap Fund",),
            ("Name of the Instrument", "ISIN", "% to Net Assets"),
            ("X", "INE040A01034", "1.0"),
        ]))
        scan_notes = scan_sheet("Disclaimer", iter([("Read all documents",)]))
        units, skipped = build_units(Path("f.xlsx"), [scan_data, scan_notes], "")
        self.assertEqual([u.label for u in units], ["Main"])
        self.assertIn("Disclaimer", skipped)

    def test_stacked_scheme_blocks_become_separate_units(self):
        rows = [("SCHEME: Demo Large Cap Fund",), ("Name of the Instrument", "ISIN", "% to NAV"),
                ("X", "INE040A01034", "1.0"), ("SCHEME: Demo Small Cap Fund",),
                ("Y", "INE040A01035", "2.0")]
        scan = scan_sheet("exposure", iter(rows))
        units, _ = build_units(Path("f.xlsx"), [scan], "")
        self.assertEqual(len(units), 2)


if __name__ == "__main__":
    unittest.main()

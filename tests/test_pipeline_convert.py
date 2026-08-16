from __future__ import annotations

import unittest

from pipeline.convert import (
    FALLBACK_CLASSIFICATION,
    AmcMapping,
    Lookups,
    NonIsinRule,
    SchemeMapping,
    convert,
)
from pipeline.schema import FINAL_FIELDS, IntermediateRow

KNOWN_ISIN = "INE000000001"
KNOWN_CLASSIFICATION = {
    "Instrument_Name": "Equity",
    "Nature_Name": "EQ",
    "Basic_Industry": "Widgets",
    "Industry": "Widgets",
    "Sector_Name": "Industrials",
    "Macro_Economic_Sector": "Industrials",
}

ALIAS_SOURCE_ISIN = "INE_OLD_0001"
ALIAS_TARGET_ISIN = "INE_NEW_0001"


def _row(**overrides) -> IntermediateRow:
    base = dict(
        amc="test_amc",
        source_file="file.xlsx",
        sheet="Test Fund",
        scheme_name_raw="Test Fund",
        section_header=None,
        security_name="Some Security",
        isin=None,
        industry_raw=None,
        quantity=100,
        market_value_raw=50.0,
        pct_raw=0.05,
    )
    base.update(overrides)
    return IntermediateRow(**base)


def _lookups() -> Lookups:
    return Lookups(
        amfi_names={"999999": "Test Fund"},
        isin_classification={
            KNOWN_ISIN: KNOWN_CLASSIFICATION,
            ALIAS_TARGET_ISIN: KNOWN_CLASSIFICATION,
        },
        isin_aliases={ALIAS_SOURCE_ISIN: ALIAS_TARGET_ISIN},
    )


def _mapping() -> AmcMapping:
    return AmcMapping(
        schemes={"Test Fund": SchemeMapping(amfi_code="999999", fund_name="Test Fund")},
        non_isin_rules=[
            NonIsinRule(match_type="exact", pattern="TREPS", classification=KNOWN_CLASSIFICATION)
        ],
    )


class ConvertColumnTests(unittest.TestCase):
    def test_output_columns_match_final_fields_and_exclude_port_date(self):
        rows = [_row(isin=KNOWN_ISIN)]
        out, _ = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(list(out[0]), FINAL_FIELDS)
        self.assertNotIn("Port_Date", out[0])
        self.assertEqual(len(FINAL_FIELDS), 12)


class ConvertFallbackTests(unittest.TestCase):
    def test_unmapped_scheme_is_dropped_not_tagged(self):
        rows = [_row(scheme_name_raw="Unknown Fund")]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(out, [])
        self.assertEqual(len(report.unmapped_schemes), 1)
        self.assertFalse(report.ok())
        self.assertFalse(report.has_tagged())

    def test_known_isin_is_classified_normally_and_not_tagged(self):
        rows = [_row(isin=KNOWN_ISIN)]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], KNOWN_ISIN)
        self.assertEqual(out[0]["Instrument_Name"], "Equity")
        self.assertFalse(report.has_tagged())

    def test_aliased_isin_resolves_to_icra_isin_and_is_not_tagged(self):
        rows = [_row(isin=ALIAS_SOURCE_ISIN)]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], ALIAS_TARGET_ISIN)
        self.assertEqual(out[0]["Instrument_Name"], "Equity")
        self.assertFalse(report.has_tagged())

    def test_unresolvable_isin_is_kept_and_tagged_undisclosed(self):
        rows = [_row(isin="INE_NEVER_SEEN_0001")]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], "INE_NEVER_SEEN_0001")
        self.assertEqual(out[0]["Instrument_Name"], FALLBACK_CLASSIFICATION["Instrument_Name"])
        self.assertEqual(out[0]["Macro_Economic_Sector"], "Miscellaneous")
        self.assertTrue(report.ok())  # scheme mapping was fine — this isn't a hard drop
        self.assertEqual(len(report.tagged_isin), 1)
        self.assertEqual(report.tagged_isin[0].isin, "INE_NEVER_SEEN_0001")

    def test_unresolvable_non_isin_row_is_kept_and_tagged_undisclosed(self):
        rows = [_row(isin=None, security_name="Some Exotic Instrument")]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], "")
        self.assertEqual(out[0]["Instrument_Name"], FALLBACK_CLASSIFICATION["Instrument_Name"])
        self.assertTrue(report.ok())
        self.assertEqual(len(report.tagged_non_isin), 1)

    def test_known_non_isin_rule_still_matches_normally(self):
        rows = [_row(isin=None, security_name="TREPS")]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["Instrument_Name"], "Equity")
        self.assertFalse(report.has_tagged())


if __name__ == "__main__":
    unittest.main()

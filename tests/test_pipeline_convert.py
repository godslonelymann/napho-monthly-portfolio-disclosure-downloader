from __future__ import annotations

import unittest

from pipeline.convert import (
    FALLBACK_CLASSIFICATION,
    AmcMapping,
    Lookups,
    SchemeMapping,
    convert,
)
from pipeline.isin_type import IsinTypeTable
from pipeline.names import NameTables, match_key
from pipeline.non_isin import NonIsinRule, NonIsinRules
from pipeline.schema import FINAL_FIELDS, IntermediateRow

KNOWN_ISIN = "INE000000001"
ALIAS_SOURCE_ISIN = "INE_OLD_0001"
ALIAS_TARGET_ISIN = "INE_NEW_0001"

# convert() looks a scheme up by pipeline.schemes._match_norm of the row's
# scheme_name_raw, so the mapping is keyed the way that leaves it.
SCHEME_KEY = "TEST FUND"


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
        pct_raw=100.0,
        port_date="2026-05-31",
    )
    base.update(overrides)
    return IntermediateRow(**base)


def _lookups(*, names: dict[str, str] | None = None) -> Lookups:
    return Lookups(
        amfi_names={"999999": "Test Fund"},
        isin_aliases={ALIAS_SOURCE_ISIN: ALIAS_TARGET_ISIN},
        isin_types=IsinTypeTable(
            seed={
                KNOWN_ISIN: ("Equity", "EQ"),
                ALIAS_TARGET_ISIN: ("Equity", "EQ"),
            },
            key_table={},
        ),
        names=NameTables(
            isin_to_name={KNOWN_ISIN: "Some Security", ALIAS_TARGET_ISIN: "Some Security"},
            match_key_to_isin=names or {},
        ),
    )


def _mapping() -> AmcMapping:
    return AmcMapping(
        schemes={SCHEME_KEY: SchemeMapping(amfi_code="999999", fund_name="Test Fund")},
        non_isin=NonIsinRules(
            overrides=[
                NonIsinRule(
                    match_type="exact", pattern="Exotic Thing",
                    instrument="Margin Deposit", nature="Debt",
                )
            ]
        ),
    )


class ConvertColumnTests(unittest.TestCase):
    def test_output_columns_match_final_fields(self):
        out, _ = convert([_row(isin=KNOWN_ISIN)], lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(list(out[0]), FINAL_FIELDS)
        self.assertIn("Port_Date", out[0])
        self.assertEqual(out[0]["Port_Date"], "2026-05-31")


class ConvertFallbackTests(unittest.TestCase):
    def test_unmapped_scheme_is_dropped_not_tagged(self):
        out, report = convert(
            [_row(scheme_name_raw="Unknown Fund")], lookups=_lookups(), amc_mapping=_mapping()
        )

        self.assertEqual(out, [])
        self.assertEqual(len(report.unmapped_schemes), 1)
        self.assertFalse(report.ok())
        self.assertFalse(report.has_tagged())

    def test_known_isin_is_classified_normally_and_not_tagged(self):
        out, report = convert([_row(isin=KNOWN_ISIN)], lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], KNOWN_ISIN)
        self.assertEqual(out[0]["Instrument_Name"], "Equity")
        self.assertFalse(report.has_tagged())

    def test_aliased_isin_resolves_to_icra_isin_and_is_not_tagged(self):
        out, report = convert(
            [_row(isin=ALIAS_SOURCE_ISIN)], lookups=_lookups(), amc_mapping=_mapping()
        )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], ALIAS_TARGET_ISIN)
        self.assertEqual(out[0]["Instrument_Name"], "Equity")
        self.assertFalse(report.has_tagged())

    def test_unresolvable_isin_is_kept_and_tagged(self):
        out, report = convert(
            [_row(isin="INE_NEVER_SEEN_0001")], lookups=_lookups(), amc_mapping=_mapping()
        )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], "INE_NEVER_SEEN_0001")
        self.assertEqual(out[0]["Instrument_Name"], FALLBACK_CLASSIFICATION["Instrument_Name"])
        self.assertTrue(report.ok())  # scheme mapping was fine — this isn't a hard drop
        self.assertEqual([r.isin for r in report.tagged_isin], ["INE_NEVER_SEEN_0001"])

    def test_known_non_isin_rule_still_matches_normally(self):
        out, report = convert(
            [_row(isin=None, security_name="Exotic Thing")],
            lookups=_lookups(),
            amc_mapping=_mapping(),
        )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], "")
        self.assertEqual(out[0]["Instrument_Name"], "Margin Deposit")
        self.assertFalse(report.has_tagged())

    def test_isin_bearing_name_resolves_when_the_isin_cell_is_blank(self):
        """An AMC leaving the ISIN cell empty does not make the row junk."""
        lookups = _lookups(names={match_key("Some Security"): KNOWN_ISIN})
        out, report = convert(
            [_row(isin=None, security_name="Some Security")],
            lookups=lookups,
            amc_mapping=_mapping(),
        )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ISIN"], KNOWN_ISIN)
        self.assertEqual(out[0]["Instrument_Name"], "Equity")
        self.assertEqual(report.not_a_holding, [])


class HoldingsFilterTests(unittest.TestCase):
    """_holdings_only: a row is dropped for restating a total, not for
    being unrecognised. ICRA keeps 243 unidentifiable rows of its own."""

    def test_category_header_restating_the_rows_below_it_is_dropped(self):
        rows = [
            _row(security_name="Equity & Equity Related Instruments", market_value_raw=90.0, pct_raw=90.0),
            _row(isin=KNOWN_ISIN, market_value_raw=60.0, pct_raw=60.0),
            _row(isin=ALIAS_TARGET_ISIN, market_value_raw=30.0, pct_raw=30.0),
            _row(security_name="Exotic Thing", market_value_raw=10.0, pct_raw=10.0),
        ]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 3)
        self.assertEqual(
            [r.security_name for r in report.not_a_holding],
            ["Equity & Equity Related Instruments"],
        )

    def test_unidentifiable_row_that_restates_nothing_is_kept(self):
        """Edelweiss prints a per-scheme "Accrued Interest" line: no ISIN,
        no known instrument, no matching security — and a real holding."""
        rows = [
            _row(isin=KNOWN_ISIN, market_value_raw=60.0, pct_raw=60.0),
            _row(isin=ALIAS_TARGET_ISIN, market_value_raw=30.0, pct_raw=30.0),
            _row(security_name="Accrued Interest", market_value_raw=7.0, pct_raw=7.0),
        ]
        out, report = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        self.assertEqual(len(out), 3)
        self.assertEqual(report.not_a_holding, [])
        kept = [o for o in out if o["Mkt_Value"] == 0.07]
        self.assertEqual(kept[0]["Instrument_Name"], FALLBACK_CLASSIFICATION["Instrument_Name"])


class DerivativeRowTests(unittest.TestCase):
    def test_futures_row_drops_the_underlyings_isin(self):
        """AMCs print the underlying's ISIN on a futures line. ICRA records
        Futures with a blank ISIN, without exception (0 of 7737)."""
        rows = [
            _row(isin=KNOWN_ISIN, market_value_raw=100.0, pct_raw=100.0),
            _row(
                isin=KNOWN_ISIN,
                security_name="Some Security-JUN2026",
                section_header="Futures",
                market_value_raw=-20.0,
                pct_raw=-20.0,
            ),
        ]
        out, _ = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        futures = [o for o in out if o["Instrument_Name"] == "Futures"]
        self.assertEqual(len(futures), 1)
        self.assertEqual(futures[0]["ISIN"], "")
        self.assertEqual(futures[0]["Nature_Name"], "EQ")

    def test_corpus_per_is_derived_when_the_disclosure_omits_it(self):
        """SEBI's derivative disclosure reports size and value but no share
        of the portfolio, so it comes from the scheme's implied net assets:
        here 100.0 of value is 100%, so -20.0 of value is -20%."""
        rows = [
            _row(isin=KNOWN_ISIN, market_value_raw=100.0, pct_raw=100.0),
            _row(
                isin=None,
                security_name="Some Security-JUN2026",
                section_header="Futures",
                market_value_raw=-20.0,
                pct_raw=None,
            ),
        ]
        out, _ = convert(rows, lookups=_lookups(), amc_mapping=_mapping())

        futures = [o for o in out if o["Instrument_Name"] == "Futures"]
        self.assertEqual(len(futures), 1)
        self.assertAlmostEqual(futures[0]["Corpus_Per"], -20.0, places=6)


if __name__ == "__main__":
    unittest.main()

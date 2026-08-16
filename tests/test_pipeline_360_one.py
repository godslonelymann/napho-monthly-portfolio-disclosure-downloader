from __future__ import annotations

import unittest
from pathlib import Path

from pipeline.amcs.three_sixty_one import parse_period

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "360_one" / "2026-05"


@unittest.skipUnless(RAW_DIR.exists(), f"{RAW_DIR} not present — raw download missing")
class ThreeSixtyOneParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = parse_period(RAW_DIR)

    def test_row_and_scheme_count(self):
        self.assertEqual(len(self.rows), 595)
        self.assertEqual(len({r.sheet for r in self.rows}), 12)

    def test_reit_row_keeps_its_own_isin_and_section(self):
        row = next(r for r in self.rows if r.security_name == "Embassy Office Parks REIT")
        self.assertEqual(row.isin, "INE041025011")
        self.assertEqual(row.section_header, "REIT/InvIT Instruments")
        self.assertEqual(row.sheet, "Dynamic Bond")
        self.assertEqual(row.quantity, 594983)

    def test_treps_row_has_no_isin(self):
        treps_rows = [r for r in self.rows if r.security_name == "TREPS" and r.sheet == "Flexicap Fund"]
        self.assertEqual(len(treps_rows), 1)
        self.assertIsNone(treps_rows[0].isin)

    def test_totals_and_footnote_rows_are_never_parsed_as_holdings(self):
        names = {r.security_name.strip().lower() for r in self.rows}
        self.assertNotIn("sub total", names)
        self.assertNotIn("total", names)
        self.assertNotIn("grand total", names)
        self.assertFalse(any(n.startswith("notes:") for n in names))

    def test_silver_etf_holding_is_not_misclassified_by_its_gold_section_label(self):
        # 360 ONE's own file mislabels the Silver ETF sheet's holding section as "Gold".
        silver_row = next(r for r in self.rows if r.sheet == "SILVERETF" and r.security_name == "SILVER")
        self.assertEqual(silver_row.section_header, "Gold")
        # The parser must still hand the raw security name through unchanged —
        # it's convert.py's ISIN-first classification that gets this right,
        # not anything section-header-based in the parser.
        self.assertEqual(silver_row.security_name, "SILVER")


if __name__ == "__main__":
    unittest.main()

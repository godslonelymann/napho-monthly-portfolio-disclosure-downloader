from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from pipeline.schema import IntermediateRow, write_unresolved


class WriteUnresolvedTests(unittest.TestCase):
    def test_writes_reason_and_row_fields_without_extra_columns_error(self):
        row = IntermediateRow(
            amc="test_amc",
            source_file="file.xlsx",
            sheet="Test Fund",
            scheme_name_raw="Test Fund",
            section_header="Equity",
            security_name="Some Security",
            isin="INE_UNSEEN_0001",
            industry_raw="Widgets",
            quantity=100,
            market_value_raw=50.0,
            pct_raw=0.05,
            port_date="2026-05-31",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unresolved.csv"
            n = write_unresolved([("isin", row)], path)
            self.assertEqual(n, 1)

            with path.open(newline="") as f:
                records = list(csv.DictReader(f))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["reason"], "isin")
            self.assertEqual(records[0]["isin"], "INE_UNSEEN_0001")
            self.assertEqual(records[0]["security_name"], "Some Security")
            # source_file/industry_raw/section_header aren't in UNRESOLVED_FIELDS —
            # confirm the extra IntermediateRow fields didn't blow up the writer.
            self.assertNotIn("source_file", records[0])


if __name__ == "__main__":
    unittest.main()

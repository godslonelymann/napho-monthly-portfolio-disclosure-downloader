from __future__ import annotations

import unittest

from verified import Bandhan_Mutual_Fund as bandhan


class DiscoveryNotesSummaryTests(unittest.TestCase):
    def test_counts_every_status_and_names_the_schemes_confirmed_absent(self):
        schemes_report = {
            "Bandhan Large Cap Fund": {"status": "found", "documents": [{"url": "https://x/a.xlsx"}]},
            "Bandhan Mid Cap Fund": {"status": "found", "documents": [{"url": "https://x/b.xlsx"}]},
            "Bandhan Fixed Term Plan - Series 179": {"status": "not_published"},
            "Bandhan Matured Close Ended Fund": {"status": "unavailable_on_site", "reason": "site fired no listing request"},
        }

        summary = bandhan._discovery_notes_summary(schemes_report)

        self.assertEqual(summary["total_schemes_offered"], 4)
        self.assertEqual(summary["status_counts"], {"found": 2, "not_published": 1, "unavailable_on_site": 1})
        self.assertEqual(
            summary["not_published"],
            ["Bandhan Fixed Term Plan - Series 179", "Bandhan Matured Close Ended Fund"],
        )

    def test_all_schemes_found_has_an_empty_not_published_list(self):
        schemes_report = {"Fund A": {"status": "found"}, "Fund B": {"status": "found"}}

        summary = bandhan._discovery_notes_summary(schemes_report)

        self.assertEqual(summary["not_published"], [])
        self.assertEqual(summary["status_counts"], {"found": 2})

    def test_empty_report_is_handled_without_error(self):
        summary = bandhan._discovery_notes_summary({})

        self.assertEqual(summary["total_schemes_offered"], 0)
        self.assertEqual(summary["status_counts"], {})
        self.assertEqual(summary["not_published"], [])

    def test_notes_are_json_serializable(self):
        import json

        schemes_report = {"Fund A": {"status": "found"}, "Fund B": {"status": "error", "reason": "boom"}}
        json.dumps(bandhan._discovery_notes_summary(schemes_report))  # must not raise


if __name__ == "__main__":
    unittest.main()

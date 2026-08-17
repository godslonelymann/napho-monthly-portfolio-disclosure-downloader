"""Regression tests for the 2017-2026 range engine in backfill_range.py.

These cover the range layer only -- the scheduling, classification, resume
and rollup logic that sits *around* the 52 verified adapters. Nothing here
touches the network: every test drives the engine with a fake
``subprocess.run`` so the AMC scripts themselves are never invoked.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backfill_range as br


REQUIRED_STATUSES = {
    br.SUCCESS, br.ALREADY_EXISTS, br.YEAR_NOT_AVAILABLE, br.MONTH_NOT_AVAILABLE,
    br.NO_DATA, br.NOT_YET_PUBLISHED, br.DISCOVERY_FAILED, br.DOWNLOAD_FAILED,
    br.INVALID_FILE, br.HTTP_ERROR, br.SITE_CHANGED, br.UNKNOWN_ERROR,
}

TODAY = date(2026, 8, 17)


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _run_one(returncode, stdout="", *, period="2019-06", discover_only=False, tmp=Path("/nonexistent")):
    # retries=0: these tests exercise single-attempt exit-code/message
    # classification, not the retry loop itself (see RetryTests below) --
    # leaving the default retries on here would make every retryable-status
    # case here really sleep and re-invoke subprocess.run several times.
    with patch.object(br.subprocess, "run", return_value=_completed(returncode, stdout)):
        return br.run_one(
            "Demo Mutual Fund", "demo", Path("Demo_Mutual_Fund.py"), period,
            output_root=tmp, discover_only=discover_only,
            timeout=10, today=TODAY, lag_months=2, retries=0,
        )


class MonthRangeTests(unittest.TestCase):
    def test_full_range_is_every_month_2017_to_2026(self):
        periods = br.month_range(2017, 2026)
        self.assertEqual(len(periods), 120)
        self.assertEqual(periods[0], (2017, 1))
        self.assertEqual(periods[-1], (2026, 12))

    def test_no_month_is_skipped_or_repeated(self):
        periods = br.month_range(2017, 2026)
        self.assertEqual(len(set(periods)), len(periods))
        self.assertEqual(sorted(periods), periods)

    def test_period_string_is_zero_padded(self):
        self.assertEqual(br._period(2017, 1), "2017-01")
        self.assertEqual(br._period(2026, 12), "2026-12")


class RecentPeriodTests(unittest.TestCase):
    def test_future_months_are_recent(self):
        self.assertTrue(br._is_recent("2026-09", today=TODAY, lag_months=2))
        self.assertTrue(br._is_recent("2027-01", today=TODAY, lag_months=2))

    def test_current_and_lagging_months_are_recent(self):
        for period in ("2026-08", "2026-07", "2026-06"):
            self.assertTrue(br._is_recent(period, today=TODAY, lag_months=2), period)

    def test_older_months_are_not_recent(self):
        for period in ("2026-05", "2019-06", "2017-01"):
            self.assertFalse(br._is_recent(period, today=TODAY, lag_months=2), period)

    def test_lag_window_crosses_the_year_boundary(self):
        january = date(2026, 1, 10)
        self.assertTrue(br._is_recent("2025-12", today=january, lag_months=2))
        self.assertTrue(br._is_recent("2025-11", today=january, lag_months=2))
        self.assertFalse(br._is_recent("2025-10", today=january, lag_months=2))


class ExitCodeClassificationTests(unittest.TestCase):
    """core.cli.run_cli's exit codes must each land on one explicit status."""

    def test_clean_exit_is_success(self):
        self.assertEqual(_run_one(0)["status"], br.SUCCESS)

    def test_period_unavailable_exit_is_a_no_data_family_status(self):
        row = _run_one(2, "unavailable: nothing published for 2019-06\n")
        self.assertEqual(row["status"], br.NO_DATA)

    def test_period_unavailable_for_a_recent_month_is_not_yet_published(self):
        row = _run_one(2, "unavailable: nothing published for 2026-07\n", period="2026-07")
        self.assertEqual(row["status"], br.NOT_YET_PUBLISHED)

    def test_download_and_validation_exit_codes(self):
        self.assertEqual(_run_one(5, "Status: INCOMPLETE\n")["status"], br.DOWNLOAD_FAILED)
        self.assertEqual(_run_one(6, "Status: DOWNLOAD_FAILED\n")["status"], br.DOWNLOAD_FAILED)
        self.assertEqual(_run_one(7, "Status: CORRUPT\n")["status"], br.INVALID_FILE)
        self.assertEqual(_run_one(8, "Status: PARTIAL_BY_CONFIG\n")["status"], br.SUCCESS)
        self.assertEqual(_run_one(9, "Status: SITE_CHANGED\n")["status"], br.SITE_CHANGED)

    def test_every_exit_code_produces_a_status_in_the_required_vocabulary(self):
        for code in (0, 1, 2, 3, 5, 6, 7, 8, 9, 70):
            row = _run_one(code, "something happened\n")
            self.assertIn(row["status"], REQUIRED_STATUSES, f"exit {code}")
            self.assertTrue(row["description"], f"exit {code} has no description")

    def test_timeout_is_recorded_not_raised(self):
        with patch.object(br.subprocess, "run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            row = br.run_one(
                "Demo Mutual Fund", "demo", Path("Demo.py"), "2019-06",
                output_root=Path("/nonexistent"), discover_only=False,
                timeout=10, today=TODAY, lag_months=2, retries=0,
            )
        self.assertEqual(row["status"], br.HTTP_ERROR)


class RetryTests(unittest.TestCase):
    """run_one() must retry retryable failures and leave everything else alone."""

    def _row(self, status, description="d"):
        return {"amc": "Demo", "year": "2019", "month": "06", "status": status,
                "description": description, "source_page": "", "download_url": "",
                "file_path": "", "error": ""}

    def _run_one_with(self, statuses, *, retries=3, retry_delay=3.0):
        """Drive run_one() through a scripted sequence of _attempt_one results,
        with time.sleep patched out so retry_delay never actually waits."""
        attempts = iter(statuses)
        calls = []

        def fake_attempt(*args, **kwargs):
            calls.append(1)
            return self._row(next(attempts))

        with patch.object(br, "_attempt_one", side_effect=fake_attempt), \
             patch.object(br.time, "sleep") as sleep_mock:
            row = br.run_one(
                "Demo Mutual Fund", "demo", Path("Demo.py"), "2019-06",
                output_root=Path("/nonexistent"), discover_only=False,
                timeout=10, today=TODAY, lag_months=2,
                retries=retries, retry_delay=retry_delay,
            )
        return row, len(calls), sleep_mock

    def test_a_retryable_failure_is_retried_up_to_the_limit(self):
        row, call_count, sleep_mock = self._run_one_with(
            [br.HTTP_ERROR, br.HTTP_ERROR, br.HTTP_ERROR, br.HTTP_ERROR], retries=3,
        )
        self.assertEqual(call_count, 4, "1 initial + 3 retries")
        self.assertEqual(row["status"], br.HTTP_ERROR)
        self.assertEqual(sleep_mock.call_count, 3)

    def test_a_failure_that_succeeds_on_a_later_attempt_stops_retrying(self):
        row, call_count, sleep_mock = self._run_one_with(
            [br.UNKNOWN_ERROR, br.DOWNLOAD_FAILED, br.SUCCESS], retries=3,
        )
        self.assertEqual(call_count, 3, "stopped as soon as a retry succeeded")
        self.assertEqual(row["status"], br.SUCCESS)
        self.assertIn("attempt 3/4", row["description"])

    def test_every_retryable_status_is_retried(self):
        for status in (br.HTTP_ERROR, br.SITE_CHANGED, br.DOWNLOAD_FAILED,
                       br.INVALID_FILE, br.DISCOVERY_FAILED, br.UNKNOWN_ERROR):
            with self.subTest(status=status):
                _, call_count, _ = self._run_one_with([status, br.SUCCESS], retries=3)
                self.assertEqual(call_count, 2, status)

    def test_a_confirmed_not_available_answer_is_never_retried(self):
        for status in (br.NO_DATA, br.MONTH_NOT_AVAILABLE, br.YEAR_NOT_AVAILABLE, br.NOT_YET_PUBLISHED):
            with self.subTest(status=status):
                row, call_count, sleep_mock = self._run_one_with([status], retries=3)
                self.assertEqual(call_count, 1, status)
                self.assertEqual(sleep_mock.call_count, 0)
                self.assertEqual(row["status"], status)

    def test_success_is_never_retried(self):
        _, call_count, sleep_mock = self._run_one_with([br.SUCCESS], retries=3)
        self.assertEqual(call_count, 1)
        self.assertEqual(sleep_mock.call_count, 0)

    def test_retries_zero_means_a_single_attempt(self):
        row, call_count, sleep_mock = self._run_one_with([br.HTTP_ERROR], retries=0)
        self.assertEqual(call_count, 1)
        self.assertEqual(sleep_mock.call_count, 0)
        self.assertEqual(row["status"], br.HTTP_ERROR)


class MessageClassificationTests(unittest.TestCase):
    def test_hdfc_bot_wall_403_is_an_http_error_not_unknown(self):
        # The exact message HDFC's adapter raises. It carries no "status" or
        # "error" word next to the code, which is what used to make it fall
        # through every pattern and land in UNKNOWN_ERROR.
        message = "RuntimeError: HDFC disclosure page returned 403 even with a browser-impersonated request"
        self.assertEqual(br.classify_failure(1, "", message)[0], br.HTTP_ERROR)

    def test_bandhan_boot_timeout_is_an_http_error_not_unknown(self):
        message = "RuntimeError: Bandhan disclosure page dropdowns never finished loading"
        self.assertEqual(br.classify_failure(1, "", message)[0], br.HTTP_ERROR)

    def test_structural_drift_is_site_changed(self):
        message = "RuntimeError: Kotak Portfolios header no longer exposes option 51"
        self.assertEqual(br.classify_failure(1, "", message)[0], br.SITE_CHANGED)

    def test_html_error_page_is_invalid_file(self):
        message = "RuntimeError: https://x/y.xlsx did not return a ZIP/XLSX payload"
        self.assertEqual(br.classify_failure(1, "", message)[0], br.INVALID_FILE)

    def test_parse_failure_is_discovery_failed(self):
        message = "RuntimeError: demo returned zero documents for 2019-06"
        self.assertEqual(br.classify_failure(1, "", message)[0], br.DISCOVERY_FAILED)

    def test_unrecognised_message_still_gets_a_status_and_keeps_its_text(self):
        status, description = br.classify_failure(1, "", "something nobody predicted")
        self.assertEqual(status, br.UNKNOWN_ERROR)
        self.assertIn("something nobody predicted", description)


class NoDataHeuristicTests(unittest.TestCase):
    """The "adapter says nothing is published" shape must not swallow failures."""

    def test_adapter_no_data_message_is_recognised(self):
        self.assertTrue(br.looks_like_no_data("HDFC listing has no current monthly workbook for 2019-04"))
        self.assertTrue(br.looks_like_no_data("Invesco does not list a monthly portfolio for 2018-03"))

    def test_http_failure_that_also_names_the_period_is_not_no_data(self):
        # Both halves of the heuristic match here by accident: the message
        # names the period and contains "no". Reading it as NO_DATA would
        # record a month the AMC does publish as one it doesn't, and the
        # resume shortcut would then never retry it.
        message = "Timeout: url=https://x/api?p=2019-06 phase=discovery attempts<=3: no response for 2019-06"
        self.assertFalse(br.looks_like_no_data(message))
        self.assertEqual(br.classify_failure(1, "", message)[0], br.HTTP_ERROR)

    def test_bot_wall_that_also_names_the_period_is_not_no_data(self):
        message = "HDFC disclosure page returned 403, no workbook for 2019-06"
        self.assertFalse(br.looks_like_no_data(message))

    def test_corrupt_payload_that_also_names_the_period_is_not_no_data(self):
        message = "https://x/2019-06.xlsx did not return a ZIP/XLSX payload, no file for 2019-06"
        self.assertFalse(br.looks_like_no_data(message))

    def test_structural_drift_that_also_names_the_period_is_not_no_data(self):
        message = "Kotak header no longer exposes a month option for 2019-06"
        self.assertFalse(br.looks_like_no_data(message))

    def test_run_one_routes_an_adapter_no_data_message_to_no_data(self):
        row = _run_one(1, "RuntimeError: demo has no monthly portfolio for 2019-06\n")
        self.assertEqual(row["status"], br.NO_DATA)

    def test_run_one_does_not_route_a_403_to_no_data(self):
        row = _run_one(1, "RuntimeError: demo page returned 403, no workbook for 2019-06\n")
        self.assertEqual(row["status"], br.HTTP_ERROR)


class DownloadFlagTests(unittest.TestCase):
    def _env_for(self, *, discover_only: bool) -> dict:
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs.get("env") or {})
            return _completed(0)

        with patch.object(br.subprocess, "run", side_effect=fake_run):
            br.run_one(
                "Demo Mutual Fund", "demo", Path("Demo.py"), "2019-06",
                output_root=Path("/nonexistent"), discover_only=discover_only,
                timeout=10, today=TODAY, lag_months=2,
            )
        return captured

    def test_download_mode_forces_amc_download_on(self):
        # Without this the adapter inherits AMC_DOWNLOAD from .env; an
        # inherited "false" made every cell exit 0 having downloaded
        # nothing, and be recorded as SUCCESS anyway.
        env = self._env_for(discover_only=False)
        self.assertEqual(env["AMC_DOWNLOAD"], "true")
        self.assertEqual(env["AMC_PERIOD"], "2019-06")

    def test_discover_only_forces_amc_download_off(self):
        self.assertEqual(self._env_for(discover_only=True)["AMC_DOWNLOAD"], "false")


class PerAmcSerialisationTests(unittest.TestCase):
    """Periods of one AMC must never be in flight at the same time."""

    def _cells(self, amc: str, periods: list[str]):
        return [(amc, amc.lower(), p, (amc, p[:4], p[5:7])) for p in periods]

    def test_one_amc_runs_its_periods_one_at_a_time_and_in_order(self):
        import queue

        concurrent = []
        active = {"n": 0}
        lock = threading.Lock()
        order = []

        def fake_run_one(amc_display, amc_slug, script, period, **kwargs):
            with lock:
                active["n"] += 1
                concurrent.append(active["n"])
                order.append(period)
            time.sleep(0.01)
            with lock:
                active["n"] -= 1
            return {"amc": amc_display, "year": period[:4], "month": period[5:7],
                    "status": br.SUCCESS, "description": "", "source_page": "",
                    "download_url": "", "file_path": "", "error": ""}

        results: queue.Queue = queue.Queue()
        periods = ["2019-01", "2019-02", "2019-03", "2019-04"]
        with patch.object(br, "run_one", side_effect=fake_run_one):
            br._run_amc_periods(
                self._cells("Demo", periods), Path("Demo.py"), results,
                output_root=Path("/nonexistent"), discover_only=True,
                timeout=10, today=TODAY, lag_months=2, amc_delay=0.0,
            )

        self.assertEqual(max(concurrent), 1, "two periods of one AMC ran at once")
        self.assertEqual(order, periods)
        self.assertEqual(results.qsize(), len(periods))

    def test_a_failing_cell_does_not_end_the_rest_of_the_amcs_range(self):
        import queue

        seen = []

        def fake_run_one(amc_display, amc_slug, script, period, **kwargs):
            seen.append(period)
            if period == "2019-02":
                raise RuntimeError("boom")
            return {"amc": amc_display, "year": period[:4], "month": period[5:7],
                    "status": br.SUCCESS, "description": "", "source_page": "",
                    "download_url": "", "file_path": "", "error": ""}

        results: queue.Queue = queue.Queue()
        periods = ["2019-01", "2019-02", "2019-03"]
        with patch.object(br, "run_one", side_effect=fake_run_one):
            br._run_amc_periods(
                self._cells("Demo", periods), Path("Demo.py"), results,
                output_root=Path("/nonexistent"), discover_only=True,
                timeout=10, today=TODAY, lag_months=2, amc_delay=0.0,
            )

        self.assertEqual(seen, periods, "the range stopped at the failing cell")
        rows = [results.get()[1] for _ in range(3)]
        self.assertEqual([row["status"] for row in rows], [br.SUCCESS, br.UNKNOWN_ERROR, br.SUCCESS])
        self.assertIn("boom", rows[1]["error"])

    def test_every_cell_reports_exactly_one_row(self):
        import queue

        def fake_run_one(*args, **kwargs):
            raise KeyboardInterrupt("even this")

        results: queue.Queue = queue.Queue()
        cells = self._cells("Demo", ["2019-01", "2019-02"])
        with patch.object(br, "run_one", side_effect=fake_run_one):
            br._run_amc_periods(
                cells, Path("Demo.py"), results,
                output_root=Path("/nonexistent"), discover_only=True,
                timeout=10, today=TODAY, lag_months=2, amc_delay=0.0,
            )
        self.assertEqual(results.qsize(), len(cells))


class RollupTests(unittest.TestCase):
    def _row(self, amc, year, month, status, **extra):
        row = {"amc": amc, "year": str(year), "month": f"{month:02d}" if month != "--" else "--",
               "status": status, "description": "", "source_page": "",
               "download_url": "", "file_path": "", "error": ""}
        row.update(extra)
        return row

    def _rows(self, *rows):
        return {(r["amc"], r["year"], r["month"]): r for r in rows}

    def test_no_data_in_a_reachable_year_becomes_month_not_available(self):
        rows = self._rows(
            self._row("Demo", 2020, 1, br.SUCCESS),
            self._row("Demo", 2020, 2, br.NO_DATA),
        )
        out = br._rollup_year_status(rows)
        self.assertEqual(out[("Demo", "2020", "02")]["status"], br.MONTH_NOT_AVAILABLE)

    def test_earliest_all_unavailable_years_collapse_to_year_not_available(self):
        rows = self._rows(
            *[self._row("Demo", 2017, m, br.NO_DATA) for m in range(1, 13)],
            *[self._row("Demo", 2018, m, br.SUCCESS) for m in range(1, 13)],
        )
        out = br._rollup_year_status(rows)
        self.assertEqual(out[("Demo", "2017", "--")]["status"], br.YEAR_NOT_AVAILABLE)
        self.assertNotIn(("Demo", "2017", "01"), out)

    def test_a_gap_year_after_a_reachable_year_is_left_as_no_data(self):
        rows = self._rows(
            *[self._row("Demo", 2018, m, br.SUCCESS) for m in range(1, 13)],
            *[self._row("Demo", 2019, m, br.NO_DATA) for m in range(1, 13)],
        )
        out = br._rollup_year_status(rows)
        self.assertNotIn(("Demo", "2019", "--"), out)
        self.assertEqual(out[("Demo", "2019", "01")]["status"], br.NO_DATA)

    def test_a_stale_collapsed_year_row_is_dropped_once_the_year_is_reachable(self):
        # A prior run collapsed 2019 to a single "--" row. This run re-ran
        # the year and found real files. Leaving the old row behind would
        # keep both it and the 12 fresh rows in the manifest.
        rows = self._rows(
            self._row("Demo", 2019, "--", br.YEAR_NOT_AVAILABLE),
            *[self._row("Demo", 2019, m, br.SUCCESS) for m in range(1, 13)],
        )
        out = br._rollup_year_status(rows)
        self.assertNotIn(("Demo", "2019", "--"), out)
        self.assertEqual(len([k for k in out if k[0] == "Demo"]), 12)

    def test_a_collapsed_year_row_for_an_amc_not_rerun_is_preserved(self):
        rows = self._rows(
            self._row("Other", 2017, "--", br.YEAR_NOT_AVAILABLE),
            self._row("Demo", 2019, 1, br.SUCCESS),
        )
        out = br._rollup_year_status(rows)
        self.assertEqual(out[("Other", "2017", "--")]["status"], br.YEAR_NOT_AVAILABLE)

    def test_rollup_is_idempotent(self):
        rows = self._rows(
            *[self._row("Demo", 2017, m, br.NO_DATA) for m in range(1, 13)],
            *[self._row("Demo", 2018, m, br.SUCCESS) for m in range(1, 13)],
        )
        once = br._rollup_year_status(rows)
        twice = br._rollup_year_status(once)
        self.assertEqual(set(once), set(twice))
        self.assertEqual(
            {k: v["status"] for k, v in once.items()},
            {k: v["status"] for k, v in twice.items()},
        )

    def test_every_rolled_up_status_is_in_the_required_vocabulary(self):
        rows = self._rows(
            *[self._row("Demo", 2017, m, br.NO_DATA) for m in range(1, 13)],
            self._row("Demo", 2018, 1, br.SUCCESS),
            self._row("Demo", 2018, 2, br.NO_DATA),
            self._row("Demo", 2019, 1, br.HTTP_ERROR),
            self._row("Demo", 2026, 9, br.NOT_YET_PUBLISHED),
        )
        for row in br._rollup_year_status(rows).values():
            self.assertIn(row["status"], REQUIRED_STATUSES)


class ResumeShortcutTests(unittest.TestCase):
    def _row(self, status):
        return {"amc": "Demo", "year": "2019", "month": "06", "status": status,
                "description": "", "source_page": "", "download_url": "",
                "file_path": "", "error": ""}

    def test_not_yet_published_is_retried_rather_than_reused(self):
        # Otherwise a month recorded NOT_YET_PUBLISHED while it was still in
        # the future would keep that answer forever and the range could
        # never fill itself in on a later run.
        shortcut = br._apply_resume_shortcut(
            "2019-06", self._row(br.NOT_YET_PUBLISHED), Path("/nonexistent"), "demo",
        )
        self.assertIsNone(shortcut)

    def test_confirmed_unavailable_answers_are_reused(self):
        for status in (br.NO_DATA, br.MONTH_NOT_AVAILABLE, br.YEAR_NOT_AVAILABLE):
            shortcut = br._apply_resume_shortcut(
                "2019-06", self._row(status), Path("/nonexistent"), "demo",
            )
            self.assertIsNotNone(shortcut, status)
            self.assertEqual(shortcut["status"], status)

    def test_failures_are_always_retried(self):
        for status in (br.HTTP_ERROR, br.SITE_CHANGED, br.DOWNLOAD_FAILED,
                       br.INVALID_FILE, br.DISCOVERY_FAILED, br.UNKNOWN_ERROR):
            self.assertIsNone(
                br._apply_resume_shortcut("2019-06", self._row(status), Path("/nonexistent"), "demo"),
                status,
            )

    def test_success_without_files_on_disk_is_re_run(self):
        self.assertIsNone(
            br._apply_resume_shortcut("2019-06", self._row(br.SUCCESS), Path("/nonexistent"), "demo")
        )

    def test_success_with_files_still_on_disk_becomes_already_exists(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            period_dir = root / "demo" / "2019-06"
            period_dir.mkdir(parents=True)
            (period_dir / "portfolio.xlsx").write_bytes(b"PK\x03\x04")
            shortcut = br._apply_resume_shortcut("2019-06", self._row(br.SUCCESS), root, "demo")
        self.assertIsNotNone(shortcut)
        self.assertEqual(shortcut["status"], br.ALREADY_EXISTS)


if __name__ == "__main__":
    unittest.main()


class _FrozenDate:
    """Stand-in for ``datetime.date`` so main()'s ``date.today()`` is fixed.

    ``date`` itself is immutable, so its ``today`` cannot be patched.
    """

    def __init__(self, value: date) -> None:
        self._value = value

    def today(self) -> date:
        return self._value


class FullRangeEndToEndTests(unittest.TestCase):
    """Drive main() over a small range with fake AMC scripts and no network."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.verified = self.root / "verified"
        self.verified.mkdir()
        for name, slug in (("Alpha_Mutual_Fund", "alpha"), ("Beta_Mutual_Fund", "beta")):
            (self.verified / f"{name}.py").write_text(f'AMC = "{slug}"\n', encoding="utf-8")
        self.manifest = self.root / "manifest.csv"
        self.output_root = self.root / "raw"
        self.addCleanup(self._tmp.cleanup)

    def _main(self, argv, run_side_effect, today=TODAY):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((kwargs.get("env") or {}).get("AMC_PERIOD"))
            return run_side_effect(cmd, **kwargs)

        class _Settings:
            process_timeout = 10

        _Settings.output_dir = self.output_root
        with (
            patch.object(br, "VERIFIED_DIR", self.verified),
            patch.object(br, "ROOT", self.root),
            patch.object(br, "settings", lambda: _Settings()),
            patch.object(br.subprocess, "run", side_effect=fake_run),
            patch.object(br, "date", _FrozenDate(today)),
        ):
            code = br.main(argv + ["--manifest", "manifest.csv", "--amc-delay", "0"])
        return code, calls

    def _manifest_rows(self):
        import csv

        with self.manifest.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_every_cell_in_the_range_ends_with_an_explicit_status(self):
        code, _ = self._main(
            ["--start-year", "2025", "--end-year", "2026", "--discover-only"],
            lambda cmd, **kw: _completed(0),
        )
        self.assertEqual(code, 0)
        rows = self._manifest_rows()
        # 2 AMCs x 24 months, minus nothing: every cell is present exactly once.
        self.assertEqual(len(rows), 2 * 24)
        self.assertEqual(len({(r["amc"], r["year"], r["month"]) for r in rows}), 2 * 24)
        for row in rows:
            self.assertIn(row["status"], REQUIRED_STATUSES)
            self.assertTrue(row["description"], row)

    def test_future_months_are_never_attempted(self):
        _, calls = self._main(
            ["--start-year", "2026", "--end-year", "2026", "--discover-only"],
            lambda cmd, **kw: _completed(0),
        )
        # today is 2026-08-17: Jan..Aug are attempted, Sep..Dec never are.
        self.assertEqual(sorted(set(calls)), [f"2026-{m:02d}" for m in range(1, 9)])
        statuses = {(r["year"], r["month"]): r["status"] for r in self._manifest_rows()}
        for month in range(9, 13):
            self.assertEqual(statuses[("2026", f"{month:02d}")], br.NOT_YET_PUBLISHED)

    def test_one_amc_failing_every_month_does_not_stop_the_other(self):
        def side_effect(cmd, **kwargs):
            script = str(cmd[-1])
            if "Alpha" in script:
                raise OSError("this AMC cannot even be launched")
            return _completed(0)

        code, _ = self._main(
            ["--start-year", "2026", "--end-year", "2026", "--discover-only"],
            side_effect,
        )
        self.assertEqual(code, 0)
        rows = self._manifest_rows()
        alpha = [r for r in rows if r["amc"] == "Alpha Mutual Fund"]
        beta = [r for r in rows if r["amc"] == "Beta Mutual Fund"]
        self.assertEqual(len(alpha), 12)
        self.assertEqual(len(beta), 12)
        # Beta's whole year still completed normally.
        self.assertEqual({r["status"] for r in beta if r["month"] <= "08"}, {br.SUCCESS})
        # Alpha's failures are recorded, with their message kept.
        failed = [r for r in alpha if r["month"] <= "08"]
        self.assertEqual(len(failed), 8)
        for row in failed:
            self.assertIn(row["status"], REQUIRED_STATUSES)
            self.assertNotEqual(row["status"], br.SUCCESS)

    def test_an_unknown_amc_stem_is_rejected_before_any_work_starts(self):
        with self.assertRaises(SystemExit):
            self._main(
                ["--amc", "Nope_Mutual_Fund", "--discover-only"],
                lambda cmd, **kw: _completed(0),
            )
        self.assertFalse(self.manifest.exists())

    def test_a_resumed_run_does_not_re_invoke_settled_cells(self):
        argv = ["--start-year", "2025", "--end-year", "2025", "--discover-only"]
        self._main(argv, lambda cmd, **kw: _completed(2, "unavailable: no data for 2025-01\n"))
        first = {(r["amc"], r["year"], r["month"]): r["status"] for r in self._manifest_rows()}
        self.assertTrue(all(status == br.YEAR_NOT_AVAILABLE for status in first.values()))

        # Second run: the settled answers are reused, so nothing is launched.
        _, calls = self._main(argv, lambda cmd, **kw: _completed(0))
        self.assertEqual(calls, [])
        rows = self._manifest_rows()
        self.assertEqual({r["status"] for r in rows}, {br.YEAR_NOT_AVAILABLE})
        # ...and the collapsed year is still one row per AMC, not 12 plus one.
        self.assertEqual(len(rows), 2)

    def test_periods_of_one_amc_never_overlap_across_the_whole_run(self):
        lock = threading.Lock()
        active: dict[str, int] = {}
        overlaps = []

        def side_effect(cmd, **kwargs):
            script = str(cmd[-1])
            with lock:
                active[script] = active.get(script, 0) + 1
                if active[script] > 1:
                    overlaps.append(script)
            time.sleep(0.005)
            with lock:
                active[script] -= 1
            return _completed(0)

        self._main(
            ["--start-year", "2025", "--end-year", "2026", "--discover-only", "--workers", "4"],
            side_effect,
        )
        self.assertEqual(overlaps, [], "the same AMC was driven concurrently")


class AdapterMessageShapeTests(unittest.TestCase):
    """Real "nothing published" messages raised by verified/*.py adapters."""

    def test_the_common_for_period_phrasing_is_recognised(self):
        for message in (
            "RuntimeError: HDFC listing has no current monthly workbook for 2019-04",
            "RuntimeError: Mirae downloads API has no monthly workbook for 2018-03",
            "RuntimeError: Canara Robeco returned no schemes for 2017-02",
        ):
            self.assertTrue(br.looks_like_no_data(message), message)

    def test_a_period_stated_as_a_path_is_recognised_too(self):
        # Groww is the one adapter that does not use "for <period>"; the
        # period arrives inside a tree path instead.
        message = "RuntimeError: Groww file tree has no Portfolio/2026-08 workbooks"
        self.assertTrue(br.looks_like_no_data(message))
        row = _run_one(1, message + "\n", period="2026-08")
        self.assertEqual(row["status"], br.NOT_YET_PUBLISHED)

    def test_a_period_alone_is_not_enough_without_a_no_data_phrase(self):
        self.assertFalse(br.looks_like_no_data("RuntimeError: could not parse the 2019-06 payload"))


class OutputPathTests(unittest.TestCase):
    """AMC_OUTPUT_DIR pointing outside the repo must not end the range."""

    def test_a_period_dir_outside_the_repo_is_reported_absolutely(self):
        self.assertEqual(br._display_path(br.ROOT / "data" / "raw"), "data/raw")
        outside = Path("/somewhere/else/raw/axis/2024-01")
        self.assertEqual(br._display_path(outside), str(outside))

    def test_run_one_records_a_success_under_an_external_output_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            # A real absolute output root that is not under ROOT -- the
            # shape that used to raise ValueError out of run_one and, via
            # the future's result(), abandon every remaining cell.
            root = Path(directory).resolve()
            (root / "demo" / "2024-01").mkdir(parents=True)
            with patch.object(br.subprocess, "run", return_value=_completed(0)):
                row = br.run_one(
                    "Demo Mutual Fund", "demo", Path("Demo.py"), "2024-01",
                    output_root=root, discover_only=False,
                    timeout=10, today=TODAY, lag_months=2,
                )
        self.assertEqual(row["status"], br.SUCCESS)
        self.assertTrue(row["file_path"].endswith("demo/2024-01"))

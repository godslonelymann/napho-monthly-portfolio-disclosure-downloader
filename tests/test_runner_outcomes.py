from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import run_verified


class RunnerOutcomeTests(unittest.TestCase):
    def _run(self, returncodes, stdouts=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.py").write_text("", encoding="utf-8")
            (root / "B.py").write_text("", encoding="utf-8")
            stdouts = stdouts or [f"result {code}\n" for code in returncodes]
            results = [
                subprocess.CompletedProcess([], code, stdout=stdout)
                for code, stdout in zip(returncodes, stdouts)
            ]
            with (
                patch.object(run_verified, "VERIFIED_DIR", root),
                patch.object(run_verified, "_period_from_env", return_value="2026-05"),
                patch.object(run_verified.subprocess, "run", side_effect=results),
                patch.object(sys, "argv", ["run_verified.py", "--timeout", "10"]),
            ):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = run_verified.main()
                return code, buffer.getvalue()

    def test_period_unavailable_does_not_fail_the_runner(self):
        code, _ = self._run([2, 2])
        self.assertEqual(code, 0)

    def test_adapter_failure_still_fails_the_runner(self):
        code, _ = self._run([2, 1])
        self.assertEqual(code, 1)


class ValidationReportParsingTests(unittest.TestCase):
    def test_finds_the_validation_report_line_among_other_output(self):
        output = "some other line\nvalidation_report=/tmp/foo/.validation.json\ndownloaded x\n"
        self.assertEqual(
            run_verified._validation_report_path(output),
            Path("/tmp/foo/.validation.json"),
        )

    def test_returns_none_when_no_such_line_exists(self):
        self.assertIsNone(run_verified._validation_report_path("nothing here\n"))

    def test_load_validation_returns_none_for_a_missing_file(self):
        self.assertIsNone(run_verified._load_validation(Path("/nonexistent/.validation.json")))

    def test_load_validation_returns_none_for_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".validation.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(run_verified._load_validation(path))

    def test_load_validation_reads_a_real_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".validation.json"
            payload = {"status": "SUCCESS", "discovered": 3, "downloaded": 3}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(run_verified._load_validation(path), payload)


class RunnerValidationTableIntegrationTests(unittest.TestCase):
    def test_a_successful_runs_real_numbers_flow_into_the_summary_table(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / ".validation.json"
            report_path.write_text(
                json.dumps(
                    {
                        "status": "SUCCESS",
                        "discovered": 80,
                        "downloaded": 80,
                        "missing": 0,
                        "corrupt": 0,
                        "duplicates": 0,
                        "unexpected": 0,
                    }
                ),
                encoding="utf-8",
            )
            stdout = f"discovered=80\nvalidation_report={report_path}\nStatus: SUCCESS\n"
            code, output = self._run_with_directory(directory, [0], [stdout])

        self.assertEqual(code, 0)
        self.assertIn("SUCCESS", output)
        self.assertIn("80", output)

    def test_an_incomplete_runs_missing_count_flows_into_the_table_even_though_it_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / ".validation.json"
            report_path.write_text(
                json.dumps(
                    {
                        "status": "INCOMPLETE",
                        "discovered": 50,
                        "downloaded": 49,
                        "missing": 1,
                        "corrupt": 0,
                        "duplicates": 0,
                        "unexpected": 0,
                    }
                ),
                encoding="utf-8",
            )
            stdout = f"discovered=50\nvalidation_report={report_path}\nStatus: INCOMPLETE\n"
            code, output = self._run_with_directory(directory, [5], [stdout])

        self.assertEqual(code, 1)  # non-zero exit code still fails the overall runner
        self.assertIn("INCOMPLETE", output)

    def _run_with_directory(self, directory, returncodes, stdouts):
        root = Path(directory)
        (root / "A.py").write_text("", encoding="utf-8")
        results = [subprocess.CompletedProcess([], code, stdout=stdout) for code, stdout in zip(returncodes, stdouts)]
        with (
            patch.object(run_verified, "VERIFIED_DIR", root),
            patch.object(run_verified, "_period_from_env", return_value="2026-05"),
            patch.object(run_verified.subprocess, "run", side_effect=results),
            patch.object(sys, "argv", ["run_verified.py", "--timeout", "10"]),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = run_verified.main()
            return code, buffer.getvalue()


if __name__ == "__main__":
    unittest.main()

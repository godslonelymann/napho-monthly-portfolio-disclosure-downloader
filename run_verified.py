"""Run every downloader in ``verified/`` for a single month -- or, with
``--full-range``, for every month from 2017 through 2026.

Each script in ``verified/`` already downloads exactly one month, taken from
the ``AMC_PERIOD`` environment variable (see ``core/config.py``, loaded from
``.env``).  By default this just walks every script in the folder and runs
it with that same month, one at a time, so a failure in one AMC doesn't stop
the rest.

``--full-range`` switches to the same multi-year engine as backfill_range.py
-- every AMC (or one, via ``--amc``) across the whole 2017-2026 range, with
the richer per-(amc, year, month) status vocabulary (SUCCESS,
ALREADY_EXISTS, YEAR_NOT_AVAILABLE, ...) written to
outputs/portfolio_manifest.csv. This file doesn't reimplement any of that --
it imports backfill_range.py and calls its main() directly, so there is
exactly one copy of the classification/resume/rollup logic to keep correct.

Usage:
    python run_verified.py                          # uses AMC_PERIOD from .env
    python run_verified.py --period 2026-06          # overrides it for this run
    python run_verified.py --full-range              # every AMC, 2017-2026
    python run_verified.py --full-range --amc Axis_Mutual_Fund
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERIFIED_DIR = ROOT / "verified"
DEFAULT_TIMEOUT = 600

# Status shown for an AMC that never reached core.cli's validate() step at
# all -- the adapter raised before discovery finished, or AMC_VALIDATE=0
# opted it out. Distinct from any real ValidationReport status so the
# summary table never implies a validation verdict that doesn't exist.
STATUS_ERROR = "ERROR"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_UNAVAILABLE = "UNAVAILABLE"

# Columns pulled straight from ValidationReport.to_dict() (see
# core/validation.py) when a run's own .validation.json is available.
_VALIDATION_COLUMNS = ("discovered", "downloaded", "missing", "corrupt", "duplicates", "unexpected")


def _period_from_env() -> str:
    sys.path.insert(0, str(ROOT))
    from core.config import settings

    return settings().period


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _validation_report_path(output: str) -> Path | None:
    # core.cli.run_cli prints this exact line right before writing the
    # report -- see the "validation_report=" print in core/cli.py -- so
    # this doesn't have to re-derive each AMC's output path itself (slug
    # sanitization, Bandhan's extra subdir level, etc.).
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("validation_report="):
            return Path(line[len("validation_report="):])
    return None


def _load_validation(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _print_validation_table(runs: list[dict]) -> None:
    """One row per AMC: real ValidationReport numbers where available, a
    synthetic status (and dashes) for runs that never reached validate()."""
    header = f"{'AMC':<45}{'Status':<18}" + "".join(f"{column.capitalize():>11}" for column in _VALIDATION_COLUMNS)
    print(header)
    print("-" * len(header))

    totals = {column: 0 for column in _VALIDATION_COLUMNS}
    counted_columns = 0
    for run in runs:
        validation = run["validation"]
        status = validation["status"] if validation else run["status"]
        if validation:
            values = [str(validation.get(column, "-")) for column in _VALIDATION_COLUMNS]
            for column in _VALIDATION_COLUMNS:
                totals[column] += validation.get(column, 0) or 0
            counted_columns += 1
        else:
            values = ["-" for _ in _VALIDATION_COLUMNS]
        row = f"{run['name'][:45]:<45}{status:<18}" + "".join(f"{value:>11}" for value in values)
        print(row)

    print("-" * len(header))
    if counted_columns:
        total_values = [str(totals[column]) for column in _VALIDATION_COLUMNS]
        print(f"{'TOTAL':<45}{'':<18}" + "".join(f"{value:>11}" for value in total_values))


def _run_full_range(args: argparse.Namespace) -> int:
    """Delegate to backfill_range.py's engine -- every AMC (or --amc's
    subset) across --start-year..--end-year, same status vocabulary, same
    outputs/portfolio_manifest.csv. See this module's docstring for why this
    calls into backfill_range rather than re-implementing it here.
    """
    sys.path.insert(0, str(ROOT))
    import backfill_range

    argv: list[str] = []
    if args.start_year is not None:
        argv += ["--start-year", str(args.start_year)]
    if args.end_year is not None:
        argv += ["--end-year", str(args.end_year)]
    for amc in args.amc or []:
        argv += ["--amc", amc]
    if args.discover_only:
        argv.append("--discover-only")
    if args.force:
        argv.append("--force")
    if args.lag_months is not None:
        argv += ["--lag-months", str(args.lag_months)]
    if args.timeout is not None:
        argv += ["--timeout", str(args.timeout)]
    if args.workers is not None:
        argv += ["--workers", str(args.workers)]
    if args.amc_delay is not None:
        argv += ["--amc-delay", str(args.amc_delay)]
    if args.retries is not None:
        argv += ["--retries", str(args.retries)]
    if args.retry_delay is not None:
        argv += ["--retry-delay", str(args.retry_delay)]
    if args.manifest is not None:
        argv += ["--manifest", args.manifest]

    return backfill_range.main(argv)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", help="YYYY-MM to download; defaults to AMC_PERIOD in .env")
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="Per-AMC timeout in seconds (default: AMC_PROCESS_TIMEOUT or 600)",
    )
    parser.add_argument(
        "--full-range", action="store_true",
        help="Run every AMC (or --amc's subset) across --start-year..--end-year "
        "(2017-2026 by default) via backfill_range.py's engine, instead of a single --period",
    )
    parser.add_argument("--start-year", type=int, default=None, help="--full-range only (default: 2017)")
    parser.add_argument("--end-year", type=int, default=None, help="--full-range only (default: 2026)")
    parser.add_argument("--amc", action="append", help="--full-range only: limit to this AMC script stem (repeatable)")
    parser.add_argument("--discover-only", action="store_true", help="--full-range only: classify availability without downloading")
    parser.add_argument("--force", action="store_true", help="--full-range only: re-run cells already recorded as SUCCESS/ALREADY_EXISTS")
    parser.add_argument("--lag-months", type=int, default=None, help="--full-range only (default: 2)")
    parser.add_argument("--workers", type=int, default=None, help="--full-range only: how many AMCs to work on at once (default: 6)")
    parser.add_argument(
        "--amc-delay", type=float, default=None,
        help="--full-range only: seconds between consecutive periods of the same AMC (default: 1.0)",
    )
    parser.add_argument(
        "--retries", type=int, default=None,
        help="--full-range only: extra attempts for a cell that fails with a retryable "
             "status, on top of the first try (default: 3, i.e. up to 4 total attempts)",
    )
    parser.add_argument(
        "--retry-delay", type=float, default=None,
        help="--full-range only: seconds to wait before re-attempting a failed cell (default: 3.0)",
    )
    parser.add_argument("--manifest", default=None, help="--full-range only (default: outputs/portfolio_manifest.csv)")
    args = parser.parse_args()

    if args.full_range:
        return _run_full_range(args)

    period = args.period or _period_from_env()
    if args.timeout is None:
        sys.path.insert(0, str(ROOT))
        from core.config import settings

        timeout = settings().process_timeout
    else:
        timeout = args.timeout

    scripts = sorted(VERIFIED_DIR.glob("*.py"))
    if not scripts:
        print(f"No scripts found in {VERIFIED_DIR}")
        return 1

    env = os.environ.copy()
    env["AMC_PERIOD"] = period

    print(f"Running {len(scripts)} verified downloader(s) for period={period}\n")

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []
    unavailable: list[tuple[str, str]] = []
    timed_out: list[str] = []
    runs: list[dict] = []  # one entry per AMC, feeds the validation table below

    for index, script in enumerate(scripts, start=1):
        name = script.stem
        print(f"[{index}/{len(scripts)}] {name} ...")
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            timed_out.append(name)
            runs.append({"name": name, "status": STATUS_TIMEOUT, "validation": None})
            print(f"    TIMEOUT after {timeout}s\n")
            continue

        output = result.stdout or ""
        for line in output.splitlines():
            print(f"    {line}")

        reason = _last_line(output)
        # core.cli.run_cli prints exactly which .validation.json it wrote
        # (if it got that far) -- read the real per-file counts from there
        # instead of re-deriving each AMC's output path or trusting the
        # last stdout line, which was never meant to carry structured data.
        report_path = _validation_report_path(output)
        validation = _load_validation(report_path) if report_path else None

        if result.returncode == 0:
            succeeded.append(name)
            # A validated download reports its own status (normally SUCCESS,
            # but see the AMC_VALIDATE=0 escape hatch and the "download
            # disabled" dry-run path in core.cli, neither of which writes a
            # report at all -- those still count as a clean exit).
            status = validation["status"] if validation else "SUCCESS"
        elif result.returncode == 2:
            # A distinct signal from core.cli.run_cli: the adapter worked and
            # confirmed the AMC simply doesn't publish this period anywhere
            # reachable, as opposed to the downloader itself being broken.
            unavailable.append((name, reason.removeprefix("unavailable: ")))
            status = STATUS_UNAVAILABLE
        else:
            failed.append((name, reason))
            # Exit codes 5-8 (INCOMPLETE/DOWNLOAD_FAILED/CORRUPT/
            # PARTIAL_BY_CONFIG) always have a report -- validate() wrote it
            # before returning. No report at all means the adapter itself
            # raised before validation ever ran.
            status = validation["status"] if validation else STATUS_ERROR
        runs.append({"name": name, "status": status, "validation": validation})
        print()

    total = len(scripts)
    print("=" * 60)
    print(
        f"done: {len(succeeded)}/{total} succeeded, {len(failed)} failed, "
        f"{len(unavailable)} unavailable, {len(timed_out)} timed out"
    )
    if failed:
        print("\nfailed:")
        for name, reason in failed:
            print(f"  - {name}: {reason}")
    if unavailable:
        print("\nunavailable (not published for this period):")
        for name, reason in unavailable:
            print(f"  - {name}: {reason}")
    if timed_out:
        print("\ntimed out:")
        for name in timed_out:
            print(f"  - {name}")

    print("\nvalidation summary:")
    _print_validation_table(runs)

    return 1 if (failed or timed_out) else 0


if __name__ == "__main__":
    raise SystemExit(main())

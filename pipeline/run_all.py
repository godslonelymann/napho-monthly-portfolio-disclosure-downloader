"""Batch driver: parse -> convert -> validate -> write, for every AMC (or
one, via --amc), for one period.

Defaults to 2026-05 because that's the one month ICRA_Sample.xlsx covers
— every AMC run against it gets a real pass/fail against ICRA's own
numbers, not just "it ran without crashing". Pointed at any other period,
it still parses/converts/writes but skips validation (nothing to check
against) and reports conversion stats only.

A failure in one AMC — a missing period, a corrupt workbook, whatever —
is caught and recorded, not allowed to stop the rest of the run.

Usage:
    python -m pipeline.run_all                        # every AMC, 2026-05, validated
    python -m pipeline.run_all --amc sbi kotak         # just these AMCs
    python -m pipeline.run_all --period 2026-07        # a different month, no validation
    python -m pipeline.run_all --no-write              # summary only, don't write CSVs
"""

from __future__ import annotations

import argparse
import csv
import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pipeline.convert import Lookups, convert, load_amc_mapping, load_lookups
from pipeline.schema import write_final
from pipeline.validate import validate

RAW_DIR = Path("data/raw")
PARSED_DIR = Path("data/parsed")
ICRA_PERIOD = "2026-05"  # the one month ICRA_Sample.xlsx covers


def _parse_period_for_amc(amc: str, period_dir: Path):
    if amc == "360_one":
        from pipeline.amcs.three_sixty_one import parse_period

        return parse_period(period_dir)
    if amc == "tata":
        from pipeline.amcs.tata import parse_period

        return parse_period(period_dir)
    if amc == "pgim":
        from pipeline.amcs.pgim import parse_period

        return parse_period(period_dir)
    from pipeline.extract import parse_period

    return parse_period(period_dir, amc=amc)


@dataclass
class AmcRunResult:
    amc: str
    period: str
    status: str = "ok"  # "ok" | "no_data" | "error"
    error: str = ""
    parsed: int = 0
    converted: int = 0
    unmapped_schemes: int = 0
    tagged_isin: int = 0
    blank_security_name: int = 0
    pct_scale_abstained: int = 0
    schemes_checked: int = 0
    schemes_passed: int = 0


def run_amc(
    amc: str,
    period: str,
    *,
    lookups: Lookups,
    write: bool = True,
    do_validate: bool = True,
) -> AmcRunResult:
    result = AmcRunResult(amc=amc, period=period)
    period_dir = RAW_DIR / amc / period
    if not period_dir.is_dir():
        result.status = "no_data"
        return result

    try:
        rows = _parse_period_for_amc(amc, period_dir)
        result.parsed = len(rows)

        amc_mapping = load_amc_mapping(amc)
        out, report = convert(rows, lookups=lookups, amc_mapping=amc_mapping)
        result.converted = report.converted
        result.unmapped_schemes = len(report.unmapped_schemes)
        result.tagged_isin = len(report.tagged_isin)
        result.blank_security_name = len(report.blank_security_name)
        result.pct_scale_abstained = len(report.pct_scale_abstained)

        if write and out:
            write_final(out, PARSED_DIR / amc / f"{period}.csv", with_audit=True)

        if do_validate and period == ICRA_PERIOD and out:
            checks = validate(out)
            result.schemes_checked = len(checks)
            result.schemes_passed = sum(1 for c in checks if c.ok)

    except Exception as exc:  # one AMC's failure must not stop the batch
        result.status = "error"
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def run_all(
    amcs: list[str] | None = None,
    period: str = ICRA_PERIOD,
    *,
    write: bool = True,
    do_validate: bool = True,
) -> list[AmcRunResult]:
    amc_list = amcs or sorted(p.name for p in RAW_DIR.iterdir() if p.is_dir())
    lookups = load_lookups()
    return [run_amc(amc, period, lookups=lookups, write=write, do_validate=do_validate) for amc in amc_list]


def list_periods(amc: str) -> list[str]:
    amc_dir = RAW_DIR / amc
    if not amc_dir.is_dir():
        return []
    return sorted(p.name for p in amc_dir.iterdir() if p.is_dir())


def run_all_periods(
    amcs: list[str] | None = None,
    *,
    write: bool = True,
    do_validate: bool = True,
) -> list[AmcRunResult]:
    """Every period any of the given AMCs (default: all) has data for —
    not just the ICRA sample month. Only that one month gets a real
    pass/fail; everything else is parse/convert/write only."""
    amc_list = amcs or sorted(p.name for p in RAW_DIR.iterdir() if p.is_dir())
    lookups = load_lookups()
    results = []
    for amc in amc_list:
        for period in list_periods(amc):
            results.append(run_amc(amc, period, lookups=lookups, write=write, do_validate=do_validate))
    return results


def print_summary(results: list[AmcRunResult], *, show_period_col: bool = False) -> None:
    validated_any = any(r.schemes_checked for r in results)
    label_w = 35 if not show_period_col else 25
    header = f"{'AMC':{label_w}s} "
    if show_period_col:
        header += f"{'period':8s} "
    header += f"{'status':8s} {'parsed':>7s} {'conv':>7s} {'unmapped':>9s} {'tagged':>7s} {'blank_nm':>9s}"
    if validated_any:
        header += f" {'schemes':>8s} {'pass':>5s}"
    print(header)
    print("-" * len(header))

    totals = {"parsed": 0, "converted": 0, "unmapped_schemes": 0, "tagged_isin": 0, "blank_security_name": 0,
              "schemes_checked": 0, "schemes_passed": 0}
    n_error = n_no_data = 0

    for r in results:
        line = f"{r.amc:{label_w}s} "
        if show_period_col:
            line += f"{r.period:8s} "
        line += f"{r.status:8s} {r.parsed:7d} {r.converted:7d} {r.unmapped_schemes:9d} {r.tagged_isin:7d} {r.blank_security_name:9d}"
        if validated_any:
            line += f" {r.schemes_checked:8d} {r.schemes_passed:5d}" if r.schemes_checked else " " * 15
        print(line)
        if r.status == "error":
            n_error += 1
            print(f"    ERROR: {r.error}")
        elif r.status == "no_data":
            n_no_data += 1
        for k in totals:
            totals[k] += getattr(r, k)

    print("-" * len(header))
    print(
        f"TOTAL: parsed={totals['parsed']} converted={totals['converted']} "
        f"unmapped={totals['unmapped_schemes']} tagged={totals['tagged_isin']} "
        f"blank_name={totals['blank_security_name']}"
        + (f" schemes={totals['schemes_checked']} pass={totals['schemes_passed']}" if validated_any else "")
    )
    print(f"runs: {len(results)} total, {n_no_data} with no data, {n_error} errored")


def write_summary_csv(results: list[AmcRunResult], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()) if results else [])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--amc", nargs="+", help="only run these AMCs (default: every AMC in data/raw)")
    ap.add_argument("--period", default=ICRA_PERIOD, help=f"YYYY-MM (default: {ICRA_PERIOD}, the ICRA sample's month)")
    ap.add_argument("--all-periods", action="store_true", help="every period each AMC has data for, not just --period")
    ap.add_argument("--no-write", action="store_true", help="don't write data/parsed/<amc>/<period>.csv")
    ap.add_argument("--no-validate", action="store_true", help="skip validation even for the ICRA sample month")
    ap.add_argument("--summary-csv", default="data/parsed/_run_summary.csv", help="where to write the per-AMC summary CSV")
    args = ap.parse_args()

    if args.all_periods:
        results = run_all_periods(amcs=args.amc, write=not args.no_write, do_validate=not args.no_validate)
    else:
        results = run_all(
            amcs=args.amc,
            period=args.period,
            write=not args.no_write,
            do_validate=not args.no_validate,
        )
    print_summary(results, show_period_col=args.all_periods)
    if results:
        write_summary_csv(results, args.summary_csv)
        print(f"\nsummary written to {args.summary_csv}")


if __name__ == "__main__":
    main()

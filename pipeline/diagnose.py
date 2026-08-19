"""Failure taxonomy for the ICRA sample month — which of the four
validate.py checks is failing for each AMC, and by how much.

`pipeline/validate.py` already knows PASS/FAIL per scheme; this just
breaks a FAIL down into which of its four conditions tripped
(row count / corpus-sum / market-value / ISIN-set) and rolls that up
per AMC, so a run before and after a fix shows exactly what moved.

Usage:
    python -m pipeline.diagnose                  # every AMC, ICRA month
    python -m pipeline.diagnose --amc icici tata
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass

from pipeline.convert import Lookups, convert, load_amc_mapping, load_lookups
from pipeline.run_all import ICRA_PERIOD, RAW_DIR, _parse_period_for_amc
from pipeline.validate import SchemeCheck, validate

CHECKS = ("row_count", "corpus_sum", "market_value", "isins")


def failed_checks(c: SchemeCheck) -> list[str]:
    out = []
    if c.our_row_count != c.icra_row_count:
        out.append("row_count")
    if abs(c.our_corpus_per_sum - 100) >= 1.0:
        out.append("corpus_sum")
    if abs(c.our_mkt_value_sum - c.icra_mkt_value_sum) / max(c.icra_mkt_value_sum, 1e-9) >= 0.01:
        out.append("market_value")
    if c.isins_only_in_ours or c.isins_only_in_icra:
        out.append("isins")
    return out


@dataclass
class AmcDiagnosis:
    amc: str
    status: str = "ok"  # "ok" | "no_data" | "error"
    error: str = ""
    schemes_checked: int = 0
    schemes_passed: int = 0
    check_fail_counts: Counter = None
    mkt_value_ratio: float | None = None  # our total / icra total, across all checked schemes

    def __post_init__(self):
        if self.check_fail_counts is None:
            self.check_fail_counts = Counter()


def diagnose_amc(amc: str, *, period: str, lookups: Lookups) -> AmcDiagnosis:
    d = AmcDiagnosis(amc=amc)
    period_dir = RAW_DIR / amc / period
    if not period_dir.is_dir():
        d.status = "no_data"
        return d

    try:
        rows = _parse_period_for_amc(amc, period_dir)
        amc_mapping = load_amc_mapping(amc)
        out, _report = convert(rows, lookups=lookups, amc_mapping=amc_mapping)
        if not out:
            d.status = "no_data"
            return d

        checks = validate(out, icra_sheet=f"Portfolio Data_{_icra_sheet_suffix(period)}")
        d.schemes_checked = len(checks)
        d.schemes_passed = sum(1 for c in checks if c.ok)

        our_total = sum(c.our_mkt_value_sum for c in checks)
        icra_total = sum(c.icra_mkt_value_sum for c in checks)
        if icra_total:
            d.mkt_value_ratio = our_total / icra_total

        for c in checks:
            if not c.ok:
                for check_name in failed_checks(c):
                    d.check_fail_counts[check_name] += 1

    except Exception as exc:
        d.status = "error"
        d.error = f"{type(exc).__name__}: {exc}"

    return d


_MONTH_ABBR = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
    "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


def _icra_sheet_suffix(period: str) -> str:
    year, month = period.split("-")
    return f"{_MONTH_ABBR[month]}{year}"


def diagnose_all(amcs: list[str] | None = None, *, period: str = ICRA_PERIOD) -> list[AmcDiagnosis]:
    amc_list = amcs or sorted(p.name for p in RAW_DIR.iterdir() if p.is_dir())
    lookups = load_lookups()
    return [diagnose_amc(amc, period=period, lookups=lookups) for amc in amc_list]


def print_report(results: list[AmcDiagnosis]) -> None:
    header = f"{'AMC':30s} {'status':8s} {'schemes':>8s} {'pass':>5s} {'mkt_ratio':>10s}  fails by check"
    print(header)
    print("-" * len(header))

    total_checked = total_passed = 0
    for d in sorted(results, key=lambda d: (-sum(d.check_fail_counts.values()), d.amc)):
        ratio_s = f"{d.mkt_value_ratio:.3f}" if d.mkt_value_ratio is not None else "-"
        fails_s = ", ".join(f"{k}={v}" for k, v in d.check_fail_counts.most_common()) or ""
        print(f"{d.amc:30s} {d.status:8s} {d.schemes_checked:8d} {d.schemes_passed:5d} {ratio_s:>10s}  {fails_s}")
        if d.status == "error":
            print(f"    ERROR: {d.error}")
        total_checked += d.schemes_checked
        total_passed += d.schemes_passed

    print("-" * len(header))
    pct = f"{100 * total_passed / total_checked:.1f}%" if total_checked else "-"
    print(f"TOTAL: {total_passed}/{total_checked} schemes passed ({pct})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--amc", nargs="+", help="only run these AMCs (default: every AMC in data/raw)")
    ap.add_argument("--period", default=ICRA_PERIOD, help=f"YYYY-MM (default: {ICRA_PERIOD})")
    args = ap.parse_args()
    results = diagnose_all(amcs=args.amc, period=args.period)
    print_report(results)


if __name__ == "__main__":
    main()

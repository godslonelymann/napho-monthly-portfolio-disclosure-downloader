"""Parse, convert, and validate 360 ONE's monthly portfolio for one period."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.amcs.three_sixty_one import AMC, find_monthly_file, parse_period
from pipeline.convert import convert, load_amc_mapping, load_lookups
from pipeline.lookups import extract_lookups
from pipeline.schema import write_final, write_intermediate, write_unresolved
from pipeline.validate import print_report, validate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="2026-05")
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--skip-validate", action="store_true")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir or f"data/raw/{AMC}/{args.period}")

    lookups_dir = Path("data/lookups")
    if not (lookups_dir / "amfi_codes.csv").exists():
        print("Extracting lookups from ICRA_Sample.xlsx...")
        extract_lookups(out_dir=lookups_dir)

    monthly_file = find_monthly_file(raw_dir)
    print(f"Parsing {monthly_file}")
    intermediate_rows = parse_period(raw_dir)
    inter_path = Path(f"data/intermediate/{AMC}/{args.period}.csv")
    write_intermediate(intermediate_rows, inter_path)
    print(f"{len(intermediate_rows)} intermediate rows -> {inter_path}")

    lookups = load_lookups(lookups_dir)
    amc_mapping = load_amc_mapping(AMC)
    final_rows, report = convert(intermediate_rows, lookups=lookups, amc_mapping=amc_mapping)

    if not report.ok():
        print("\nDROPPED ROWS (no scheme mapping — not written to output):")
        for row in report.unmapped_schemes:
            print(f"  sheet={row.sheet!r} security={row.security_name!r} isin={row.isin!r}")

    final_path = Path(f"data/parsed/{AMC}/{args.period}.csv")
    write_final(final_rows, final_path)
    print(f"\n{report.converted}/{report.total} rows converted -> {final_path}")

    if report.has_tagged():
        tagged = [("isin", row) for row in report.tagged_isin] + [
            ("non_isin", row) for row in report.tagged_non_isin
        ]
        unresolved_path = Path(f"data/parsed/{AMC}/{args.period}_unresolved.csv")
        write_unresolved(tagged, unresolved_path)
        print(
            f"{len(tagged)} row(s) written as 'Undisclosed - Others' "
            f"(unresolved classification) -> {unresolved_path}"
        )

    if not args.skip_validate:
        print("\nValidating against ICRA_Sample.xlsx...\n")
        checks = validate(final_rows)
        all_ok = print_report(checks)
        print(f"\n{'ALL SCHEMES PASS' if all_ok else 'SOME SCHEMES FAILED'}")
        return 0 if all_ok else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

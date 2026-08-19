"""Step 6 — propose isin_aliases.csv entries from validation mismatches.

pipeline/validate.py already tells us, per scheme, which ISINs appear only
in our output and which appear only in ICRA's. Most of those are real
gaps (an ISIN we never resolved). But some are the *same holding* filed
under two different ISINs — a demerger, a re-issue, an AMC using a
since-superseded code — and validate.py's exact-ISIN-set comparison can't
tell the two apart on its own.

Quantity and market value are the tell: they're independent of which ISIN
got typed on either side, so a same-scheme "only in ours" row and "only in
ICRA" row that match on both, almost exactly, are the same holding. That's
strong enough to propose automatically; it is not strong enough to write
into the trusted data/lookups/isin_aliases.csv unattended — this only
writes a review file. A human (or a follow-up pass with a documented
reason per row, matching the file's existing two entries) promotes the
ones that check out.

Usage:
    python -m pipeline.isin_alias_candidates                 # every AMC, ICRA month
    python -m pipeline.isin_alias_candidates --amc icici_prudential
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from pipeline.convert import Lookups, convert, load_amc_mapping, load_lookups
from pipeline.run_all import ICRA_PERIOD, RAW_DIR, _parse_period_for_amc
from pipeline.validate import SchemeCheck, _icra_rows_by_amfi, validate

QTY_REL_TOL = 0.001  # 0.1%
VALUE_REL_TOL = 0.001


@dataclass
class AliasCandidate:
    amc: str
    amfi_code: str
    fund_name: str
    isin_in_amc_file: str
    isin_in_icra: str
    security_name: str
    quantity: float
    mkt_value_ours: float
    mkt_value_icra: float


def _close(a: float | None, b: float | None, rel_tol: float) -> bool:
    if a is None or b is None:
        return False
    if a == 0 and b == 0:
        return True
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= rel_tol


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def find_candidates_for_amc(
    amc: str,
    *,
    period: str = ICRA_PERIOD,
    lookups: Lookups,
    icra_path: str | Path = "ICRA_Sample.xlsx",
    icra_sheet: str = "Portfolio Data_May2026",
) -> list[AliasCandidate]:
    period_dir = RAW_DIR / amc / period
    if not period_dir.is_dir():
        return []

    rows = _parse_period_for_amc(amc, period_dir)
    amc_mapping = load_amc_mapping(amc)
    out, _report = convert(rows, lookups=lookups, amc_mapping=amc_mapping)
    if not out:
        return []

    checks = validate(out, icra_path=icra_path, icra_sheet=icra_sheet)

    ours_by_amfi: dict[str, list[dict]] = {}
    for row in out:
        ours_by_amfi.setdefault(str(row["AMFI Code"]), []).append(row)
    icra_by_amfi = _icra_rows_by_amfi(icra_path, icra_sheet, {c.amfi_code for c in checks})

    candidates: list[AliasCandidate] = []
    for c in checks:
        if not (c.isins_only_in_ours and c.isins_only_in_icra):
            continue

        ours_only = [r for r in ours_by_amfi.get(c.amfi_code, []) if r["ISIN"] in c.isins_only_in_ours]
        icra_only = [r for r in icra_by_amfi.get(c.amfi_code, []) if r["isin"] in c.isins_only_in_icra]

        matched_icra_isins: set[str] = set()
        for our_row in ours_only:
            our_qty = _num(our_row["No_Of_Shares"])
            our_val = _num(our_row["Mkt_Value"])
            best = None
            for icra_row in icra_only:
                if icra_row["isin"] in matched_icra_isins:
                    continue
                if _close(our_qty, _num(icra_row["shares"]), QTY_REL_TOL) and _close(
                    our_val, _num(icra_row["mkt_value"]), VALUE_REL_TOL
                ):
                    best = icra_row
                    break
            if best is None:
                continue
            matched_icra_isins.add(best["isin"])
            candidates.append(
                AliasCandidate(
                    amc=amc,
                    amfi_code=c.amfi_code,
                    fund_name=c.fund_name,
                    isin_in_amc_file=our_row["ISIN"],
                    isin_in_icra=best["isin"],
                    security_name=our_row["Security_Name"] or best["fund"] or "",
                    quantity=our_qty,
                    mkt_value_ours=our_val,
                    mkt_value_icra=_num(best["mkt_value"]),
                )
            )

    return candidates


def find_all_candidates(amcs: list[str] | None = None, *, period: str = ICRA_PERIOD) -> list[AliasCandidate]:
    amc_list = amcs or sorted(p.name for p in RAW_DIR.iterdir() if p.is_dir())
    lookups = load_lookups()
    out: list[AliasCandidate] = []
    for amc in amc_list:
        try:
            out.extend(find_candidates_for_amc(amc, period=period, lookups=lookups))
        except Exception:
            continue
    return out


def write_review_csv(candidates: list[AliasCandidate], path: str | Path = "data/review/isin_alias_candidates.csv") -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "amc", "amfi_code", "fund_name", "isin_in_amc_file", "isin_in_icra",
                "security_name", "quantity", "mkt_value_ours", "mkt_value_icra",
            ]
        )
        for c in candidates:
            writer.writerow(
                [c.amc, c.amfi_code, c.fund_name, c.isin_in_amc_file, c.isin_in_icra,
                 c.security_name, c.quantity, c.mkt_value_ours, c.mkt_value_icra]
            )
    return len(candidates)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--amc", nargs="+", help="only these AMCs (default: every AMC in data/raw)")
    ap.add_argument("--period", default=ICRA_PERIOD)
    ap.add_argument("--out", default="data/review/isin_alias_candidates.csv")
    args = ap.parse_args()

    candidates = find_all_candidates(amcs=args.amc, period=args.period)
    n = write_review_csv(candidates, args.out)
    print(f"{n} candidate(s) written to {args.out}")
    for c in candidates:
        print(f"  [{c.amc}] {c.isin_in_amc_file} -> {c.isin_in_icra}  ({c.security_name}, qty={c.quantity}, val={c.mkt_value_ours})")


if __name__ == "__main__":
    main()

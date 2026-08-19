"""Step 5 — validate a parsed AMC against ICRA's own rows for the same month.

Our raw data and the ICRA sample are the same month, so this is a genuine
pass/fail, not eyeballing: scheme count, Corpus_Per sums to 100, same ISIN
set per scheme, values within tolerance. Anything that doesn't match is
listed, never silently dropped.

A scheme can reconcile (row count, Corpus_Per, market value all line up)
while still containing rows pipeline/convert.py couldn't classify and
wrote as "Undisclosed - Others" — reconciling isn't the same as fully
resolved, so tagged_count is reported alongside ok even on a PASS.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl


@dataclass
class SchemeCheck:
    amfi_code: str
    fund_name: str
    our_row_count: int
    icra_row_count: int
    our_corpus_per_sum: float
    icra_corpus_per_sum: float
    our_mkt_value_sum: float
    icra_mkt_value_sum: float
    isins_only_in_ours: set[str]
    isins_only_in_icra: set[str]
    tagged_count: int = 0

    @property
    def ok(self) -> bool:
        return (
            self.our_row_count == self.icra_row_count
            and abs(self.our_corpus_per_sum - 100) < 1.0
            and abs(self.our_mkt_value_sum - self.icra_mkt_value_sum) / max(self.icra_mkt_value_sum, 1e-9) < 0.01
            and not self.isins_only_in_ours
            and not self.isins_only_in_icra
        )


def _icra_rows_by_amfi(
    icra_path: str | Path, sheet: str, amfi_codes: set[str]
) -> dict[str, list[dict]]:
    wb = openpyxl.load_workbook(icra_path, read_only=True, data_only=True)
    ws = wb[sheet]
    out: dict[str, list[dict]] = defaultdict(list)
    for row in ws.iter_rows(min_row=2, values_only=True):
        amfi, _port_date, isin, instr, nat, basic_ind, ind, sector, macro, corpus, mkt, shares, fund = row
        code = str(amfi)
        if code in amfi_codes:
            out[code].append(
                {
                    "isin": isin,
                    "instrument": instr,
                    "corpus_per": corpus,
                    "mkt_value": mkt,
                    "shares": shares,
                    "fund": fund,
                }
            )
    return out


def validate(
    final_rows: list[dict],
    *,
    icra_path: str | Path = "ICRA_Sample.xlsx",
    icra_sheet: str = "Portfolio Data_May2026",
) -> list[SchemeCheck]:
    ours_by_amfi: dict[str, list[dict]] = defaultdict(list)
    for row in final_rows:
        ours_by_amfi[str(row["AMFI Code"])].append(row)

    amfi_codes = set(ours_by_amfi)
    icra_by_amfi = _icra_rows_by_amfi(icra_path, icra_sheet, amfi_codes)

    checks = []
    for code in sorted(amfi_codes):
        ours = ours_by_amfi[code]
        icra = icra_by_amfi.get(code, [])
        our_isins = {r["ISIN"] for r in ours if r["ISIN"]}
        icra_isins = {r["isin"] for r in icra if r["isin"]}
        checks.append(
            SchemeCheck(
                amfi_code=code,
                fund_name=ours[0]["Fund_Name"] if ours else "?",
                our_row_count=len(ours),
                icra_row_count=len(icra),
                our_corpus_per_sum=sum(r["Corpus_Per"] for r in ours if r["Corpus_Per"] is not None),
                icra_corpus_per_sum=sum(r["corpus_per"] for r in icra if r["corpus_per"] is not None),
                our_mkt_value_sum=sum(r["Mkt_Value"] for r in ours if r["Mkt_Value"] is not None),
                icra_mkt_value_sum=sum(r["mkt_value"] for r in icra if r["mkt_value"] is not None),
                isins_only_in_ours=our_isins - icra_isins,
                isins_only_in_icra=icra_isins - our_isins,
                tagged_count=sum(1 for r in ours if r["Instrument_Name"] == "Undisclosed - Others"),
            )
        )
    return checks


def print_report(checks: list[SchemeCheck]) -> bool:
    all_ok = True
    for c in checks:
        status = "PASS" if c.ok else "FAIL"
        all_ok &= c.ok
        print(f"[{status}] {c.fund_name} (AMFI {c.amfi_code})")
        print(f"    rows:       ours={c.our_row_count:4d}  icra={c.icra_row_count:4d}")
        print(f"    corpus_per: ours={c.our_corpus_per_sum:8.3f}  icra={c.icra_corpus_per_sum:8.3f}")
        print(f"    mkt_value:  ours={c.our_mkt_value_sum:12.4f}  icra={c.icra_mkt_value_sum:12.4f}")
        if c.isins_only_in_ours:
            print(f"    ISINs only in ours: {sorted(c.isins_only_in_ours)}")
        if c.isins_only_in_icra:
            print(f"    ISINs only in ICRA: {sorted(c.isins_only_in_icra)}")
        if c.tagged_count:
            print(f"    tagged: {c.tagged_count} row(s) written as 'Undisclosed - Others' (unresolved ISIN)")
    return all_ok

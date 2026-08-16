"""Step 1 — pull the three seed lookups out of the ICRA sample, once.

ICRA_Sample.xlsx's "Portfolio Data_<Mon><YYYY>" sheet is self-seeding: it
already contains every AMFI Code -> Fund_Name pair, every ISIN's 4-tier
industry classification, and the complete Instrument_Name -> Nature_Name
(+ default industry) vocabulary. This module extracts those into
inspectable, hand-correctable CSVs under data/lookups/, and that's the
only thing the ICRA file is used for from here on.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

AMFI_FIELDS = ["AMFI Code", "Fund_Name"]
ISIN_FIELDS = [
    "ISIN",
    "Instrument_Name",
    "Nature_Name",
    "Basic_Industry",
    "Industry",
    "Sector_Name",
    "Macro_Economic_Sector",
]
INSTRUMENT_FIELDS = [
    "Instrument_Name",
    "Nature_Name",
    "Basic_Industry",
    "Industry",
    "Sector_Name",
    "Macro_Economic_Sector",
]


def _rows(icra_path: Path, sheet: str):
    wb = openpyxl.load_workbook(icra_path, read_only=True, data_only=True)
    ws = wb[sheet]
    it = ws.iter_rows(min_row=2, values_only=True)
    for row in it:
        if row[0] is None and row[12] is None:
            continue
        yield row


def extract_lookups(
    icra_path: str | Path = "ICRA_Sample.xlsx",
    sheet: str = "Portfolio Data_May2026",
    out_dir: str | Path = "data/lookups",
) -> dict[str, int]:
    icra_path = Path(icra_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    amfi_names: dict[int, Counter] = defaultdict(Counter)
    isin_tuples: dict[str, Counter] = defaultdict(Counter)
    instr_tuples: dict[str, Counter] = defaultdict(Counter)

    for row in _rows(icra_path, sheet):
        amfi, _port_date, isin, instr, nat, basic_ind, ind, sector, macro, _corpus, _mkt, _shares, fund = row
        if amfi is not None and fund:
            amfi_names[amfi][fund] += 1
        if instr is not None:
            instr_tuples[instr][(nat, basic_ind, ind, sector, macro)] += 1
        if isin:
            isin_tuples[isin][(instr, nat, basic_ind, ind, sector, macro)] += 1

    amfi_conflicts = {k: v for k, v in amfi_names.items() if len(v) > 1}
    isin_conflicts = {k: v for k, v in isin_tuples.items() if len(v) > 1}
    instr_conflicts = {k: v for k, v in instr_tuples.items() if len(v) > 1}

    with (out_dir / "amfi_codes.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(AMFI_FIELDS)
        for code in sorted(amfi_names, key=str):
            writer.writerow([code, amfi_names[code].most_common(1)[0][0]])

    with (out_dir / "isin_classification.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(ISIN_FIELDS)
        for isin in sorted(isin_tuples):
            tup = isin_tuples[isin].most_common(1)[0][0]
            writer.writerow([isin, *tup])

    with (out_dir / "instrument_types.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(INSTRUMENT_FIELDS)
        for instr in sorted(instr_tuples):
            tup = instr_tuples[instr].most_common(1)[0][0]
            writer.writerow([instr, *tup])

    if amfi_conflicts or isin_conflicts or instr_conflicts:
        with (out_dir / "conflicts.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["kind", "key", "candidates"])
            for code, names in amfi_conflicts.items():
                writer.writerow(["amfi_code", code, dict(names)])
            for isin, tuples in isin_conflicts.items():
                writer.writerow(["isin", isin, dict(tuples)])
            for instr, tuples in instr_conflicts.items():
                writer.writerow(["instrument_name", instr, dict(tuples)])

    return {
        "amfi_codes": len(amfi_names),
        "amfi_conflicts": len(amfi_conflicts),
        "isins": len(isin_tuples),
        "isin_conflicts": len(isin_conflicts),
        "instrument_types": len(instr_tuples),
        "instrument_conflicts": len(instr_conflicts),
    }


if __name__ == "__main__":
    stats = extract_lookups()
    for k, v in stats.items():
        print(f"{k}: {v}")

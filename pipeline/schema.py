"""The two row shapes every AMC parser and the shared converter agree on."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterable

# Raw values, no conversions. This is the contract that keeps 52 separate
# AMC parsers from drifting apart: each parser only ever produces these
# fields, and only pipeline/convert.py knows how to turn them into the
# final ICRA-shaped columns.
INTERMEDIATE_FIELDS = [
    "amc",
    "source_file",
    "sheet",
    "scheme_name_raw",
    "section_header",
    "security_name",
    "isin",
    "industry_raw",
    "quantity",
    "market_value_raw",
    "pct_raw",
]

# ICRA_Sample.xlsx's "Portfolio Data_MonYYYY" sheet, minus Port_Date.
#
# ICRA's sheet has 13 columns; we deliberately emit 12. Port_Date is dropped
# because every row in a given run carries the identical value (the period's
# month-end) and the period is already on the output path
# (data/parsed/<amc>/<period>.csv). Consequence to keep in mind: rows here
# do NOT identify their own month, so concatenating several periods into one
# table loses that distinction — re-add Port_Date first if that's ever done.
FINAL_FIELDS = [
    "AMFI Code",
    "ISIN",
    "Instrument_Name",
    "Nature_Name",
    "Basic_Industry",
    "Industry",
    "Sector_Name",
    "Macro_Economic_Sector",
    "Corpus_Per",
    "Mkt_Value",
    "No_Of_Shares",
    "Fund_Name",
]


@dataclass
class IntermediateRow:
    amc: str
    source_file: str
    sheet: str
    scheme_name_raw: str
    section_header: str | None
    security_name: str
    isin: str | None
    industry_raw: str | None
    quantity: Any
    market_value_raw: Any
    pct_raw: Any


def write_intermediate(rows: Iterable[IntermediateRow], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INTERMEDIATE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
            n += 1
    return n


def read_intermediate(path: str | Path) -> list[IntermediateRow]:
    field_names = {f.name for f in fields(IntermediateRow)}
    out = []
    with Path(path).open(newline="") as f:
        for record in csv.DictReader(f):
            out.append(IntermediateRow(**{k: v for k, v in record.items() if k in field_names}))
    return out


def write_final(rows: Iterable[dict], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FINAL_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            n += 1
    return n


# Audit trail for rows written with FALLBACK_CLASSIFICATION (see
# pipeline/convert.py) — never the primary output, always alongside it.
UNRESOLVED_FIELDS = [
    "reason",
    "amc",
    "scheme_name_raw",
    "sheet",
    "security_name",
    "isin",
    "quantity",
    "market_value_raw",
    "pct_raw",
]


def write_unresolved(tagged: Iterable[tuple[str, IntermediateRow]], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=UNRESOLVED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for reason, row in tagged:
            writer.writerow({"reason": reason, **asdict(row)})
            n += 1
    return n

"""The two row shapes every AMC parser and the shared converter agree on."""

from __future__ import annotations

import calendar
import csv
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterable

_PERIOD_RE = re.compile(r"(\d{4})-(\d{2})")


def month_end_date(period: str) -> str:
    """"YYYY-MM" (a raw/<amc>/<period> folder name) -> ISO month-end date.

    Every AMC's monthly portfolio is "as on" the last calendar day of the
    period, regardless of when the file itself was published.
    """
    m = _PERIOD_RE.search(period)
    if not m:
        raise ValueError(f"Not a YYYY-MM period: {period!r}")
    year, month = int(m.group(1)), int(m.group(2))
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


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
    "port_date",
]

# ICRA_Sample.xlsx's "Portfolio Data_MonYYYY" sheet, per
# ICRA_CONVERSION_PLAN.md's 14-column output: ICRA's 13 columns, plus
# Security_Name. Port_Date is a real date (2026-05-31), not "2026-05".
# Basic_Industry / Industry / Sector_Name / Macro_Economic_Sector are
# always blank — classification only ever fills Instrument_Name /
# Nature_Name (see pipeline/isin_type.py).
FINAL_FIELDS = [
    "AMFI Code",
    "Port_Date",
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
    "Security_Name",
]

# Audit-only, not part of ICRA's shape: which source filled Security_Name
# (harvest / variant / blank) — see pipeline/convert.py.
AUDIT_FIELDS = ["Name_Source"]


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
    port_date: str  # ISO date, e.g. "2026-05-31" — the period this file covers


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


def write_final(rows: Iterable[dict], path: str | Path, *, with_audit: bool = False) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = FINAL_FIELDS + AUDIT_FIELDS if with_audit else FINAL_FIELDS
    n = 0
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
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

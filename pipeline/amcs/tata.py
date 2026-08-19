"""Tata Mutual Fund — monthly portfolio parser.

One workbook, one sheet per scheme (70 of them), but the sheet names are
internal short codes ("TBAF", "TMCAPF", ...) not scheme names — an "Index"
sheet is the only place the code -> full scheme name mapping exists
("CLASSIFICATION", "SCHEME CODE", "SCHEME NAME" columns).

pipeline/extract.py's generic parser handles the row-level parsing fine
(multiple tables per sheet, ISIN/name column detection) but its
_find_scheme_name heuristic has nothing reliable to scan here — the
nearest "title-shaped" text above a holdings table is boilerplate
(riskometer text, merger notices, category-total labels like "EQUITY &
EQUITY RELATED TOTAL"), not the scheme name, so it silently mis-groups
almost every row under a handful of wrong names (2,940 of 3,724 rows
landed under "Scheme Risk-O-Meter" alone). The fix isn't a better title
heuristic; the workbook already tells you the real name for a given sheet
directly, so read that instead of guessing from nearby text.

Also skipped: "Index", "Dividend History", "Tata Scheme Risk-o-meter",
"Debt Index Replication Factor" — reference sheets with no ISIN table.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.extract import _drop_duplicate_blocks, find_portfolio_files, parse_sheet
from pipeline.isin_names import iter_sheets
from pipeline.schema import IntermediateRow, month_end_date

_NON_SCHEME_SHEETS = {
    "index",
    "dividend history",
    "tata scheme risk-o-meter",
    "debt index replication factor",
}


def load_scheme_index(path: str | Path) -> dict[str, str]:
    """Sheet code -> full scheme name, from the workbook's own "Index" sheet."""
    index: dict[str, str] = {}
    for sheet_name, row_iter in iter_sheets(path):
        if sheet_name.strip().lower() != "index":
            continue
        for row in row_iter:
            row = list(row)
            if len(row) < 3:
                continue
            code, name = row[1], row[2]
            if code and name and str(code).strip().upper() != "SCHEME CODE":
                index[str(code).strip()] = str(name).strip()
        break
    return index


def parse_workbook(path: str | Path, *, amc: str, port_date: str) -> list[IntermediateRow]:
    path = Path(path)
    scheme_index = load_scheme_index(path)

    out: list[IntermediateRow] = []
    for sheet_name, row_iter in iter_sheets(path):
        if sheet_name.strip().lower() in _NON_SCHEME_SHEETS:
            continue
        scheme_name = scheme_index.get(sheet_name.strip())
        if scheme_name is None:
            # A sheet the Index doesn't know about — nothing in the
            # workbook says what scheme this is, so it can't be trusted
            # to fall back to the sheet code (that would just create an
            # unmatchable, unreviewable scheme in schemes.csv). Skip it;
            # the survey (pipeline/diagnose.py) will surface it as a drop
            # in row count if it ever matters.
            continue

        rows = [list(r) for r in row_iter]
        sheet_rows = parse_sheet(rows, amc=amc, source_file=path.name, sheet_name=sheet_name, port_date=port_date)
        for r in sheet_rows:
            r.scheme_name_raw = scheme_name
        out.extend(sheet_rows)

    return out


def parse_period(period_dir: str | Path, *, amc: str = "tata") -> list[IntermediateRow]:
    period_dir = Path(period_dir)
    port_date = month_end_date(period_dir.name)
    out: list[IntermediateRow] = []
    for path in find_portfolio_files(period_dir):
        try:
            out.extend(parse_workbook(path, amc=amc, port_date=port_date))
        except Exception:
            continue
    return _drop_duplicate_blocks(out)

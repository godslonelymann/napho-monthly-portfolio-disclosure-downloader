"""PGIM India Mutual Fund — monthly portfolio parser.

One file per scheme, scheme name in the filename ("PGIM INDIA FLEXI CAP
FUND May 2026.xlsx"). pipeline/extract.py's generic _find_scheme_name
heuristic has nothing usable to find near the holdings table: every
scheme's title rows are riskometer disclosure text ("The risk of the
scheme is very high risk") or a scheme-merger notice, both of which read
as more "title-shaped" than anything else nearby, so 100% of rows across
every scheme collapse onto a handful of those boilerplate strings instead
of the real scheme name. The filename already has the real name; use that
instead of scanning rows for it.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline.extract import _drop_duplicate_blocks, find_portfolio_files, parse_sheet
from pipeline.isin_names import iter_sheets
from pipeline.schema import IntermediateRow, month_end_date

_MONTH_YEAR_SUFFIX_RE = re.compile(
    r"\s+(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|aug(ust)?|"
    r"sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?)\s+\d{4}\s*$",
    re.IGNORECASE,
)


def scheme_name_from_filename(path: str | Path) -> str:
    stem = Path(path).stem
    return _MONTH_YEAR_SUFFIX_RE.sub("", stem).strip()


def parse_workbook(path: str | Path, *, amc: str, port_date: str) -> list[IntermediateRow]:
    path = Path(path)
    scheme_name = scheme_name_from_filename(path)

    out: list[IntermediateRow] = []
    for sheet_name, row_iter in iter_sheets(path):
        rows = [list(r) for r in row_iter]
        sheet_rows = parse_sheet(rows, amc=amc, source_file=path.name, sheet_name=sheet_name, port_date=port_date)
        for r in sheet_rows:
            r.scheme_name_raw = scheme_name
        out.extend(sheet_rows)

    return out


def parse_period(period_dir: str | Path, *, amc: str = "pgim") -> list[IntermediateRow]:
    period_dir = Path(period_dir)
    port_date = month_end_date(period_dir.name)
    out: list[IntermediateRow] = []
    for path in find_portfolio_files(period_dir):
        try:
            out.extend(parse_workbook(path, amc=amc, port_date=port_date))
        except Exception:
            continue
    return _drop_duplicate_blocks(out)

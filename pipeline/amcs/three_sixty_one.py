"""360 ONE Mutual Fund — monthly portfolio parser.

Which files/sheets are schemes: the raw/360_one/<period>/ folder holds a lot
of unrelated downloads (daily AAUM disclosures, the AMC dashboard, portfolio
overlap reports, single-scheme YTM sheets). The one true monthly portfolio
file is named "..._MONTHLY_PORTFOLIO_<Mon><YYYY>..._Final....xlsx" and has
one sheet per scheme — no index sheet, no multi-scheme sheets.

Header row: row 4 in every sheet ("Name of the Instrument", "ISIN", ...).
Row 1 is the scheme's full title, row 3 is "Monthly Portfolio Statement as
on <date>". Column layout is fixed across all 12 sheets:

    A: internal broker/security code (unused)
    B: Name of the Instrument
    C: ISIN
    D: Industry / Rating (raw, AMC's own wording)
    E: Quantity
    F: Market/Fair Value (Rs. in Lacs)
    G: Rounded % to Net Assets (fraction, e.g. 0.0525 = 5.25%)
    H: YTM (unused)
    I: ~YTC (unused)

Section headers: rows where column B has text but E/F/G are all empty.
Top-level headers ("Equity & Equity related", "Debt Instruments", "Money
Market Instruments", "TREPS / Reverse Repo", "Gold", "REIT/InvIT
Instruments", "Others") and finer sub-labels ("Certificate of Deposit",
"Commercial Paper", "Treasury Bill", ...) are both tracked into
section_header for context/debugging, but pipeline/convert.py does NOT rely
on section_header to determine Instrument_Name — ISIN determines it
deterministically dataset-wide (verified against ICRA_Sample.xlsx: 0 ISINs
in 128k rows map to more than one Instrument_Name), and the handful of
non-ISIN rows (TREPS, Reverse Repo, Gold, Silver, Net Receivables,
commodity futures) are keyed off their own security_name instead — see
data/mappings/360_one/non_isin_instruments.csv. One AMC quirk this
sidesteps: 360 ONE's own file mislabels the Silver ETF's section header as
"Gold" (template copy-paste), which section-header-based classification
would get wrong.

"(a) Listed / awaiting listing..." / "(b) Privately placed / Unlisted" rows
carry no type information (just exchange-listing status) and are skipped.
Rows literally named "Sub Total" / "Total" / "GRAND TOTAL" are skipped.
Parsing of a sheet stops at "Notes:" — everything after is NAV/IDCW/YTM
footnotes, not holdings.

Units (handled in pipeline/convert.py, not here): column F is Rs. in Lacs
(-> divide by 100 for the crores ICRA expects); column G is a fraction
(0.0525, not 5.25) so it's multiplied by 100 for Corpus_Per. This parser
hands both through unconverted — converting here would make the
intermediate CSV lie about what's actually in the source file.

ISIN drift vs. ICRA_Sample.xlsx: two securities in this file (Talwandi
Sabo Power, Info Edge) carry an ISIN that differs from the one ICRA's May
2026 sample uses for the same holding — same quantity and value on both
sides, just a different ISIN string (Vedanta demerger fallout in one
case). This is not a parsing bug; see data/lookups/isin_aliases.csv and
pipeline/convert.py, which resolve it at the shared-converter layer, not
per-AMC.

What every future AMC parser should copy from this one: find the header
row by label, not by row number; keep classification keyed off ISIN, not
section labels — 360 ONE's own file mislabels the Silver ETF's section as
"Gold" (template copy-paste), and header-based logic would get it wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline.isin_names import iter_sheets
from pipeline.schema import IntermediateRow, month_end_date

AMC = "360_one"

_MONTHLY_FILE_RE = re.compile(r"MONTHLY_PORTFOLIO", re.IGNORECASE)

TOP_LEVEL_SECTIONS = {
    "equity & equity related",
    "debt instruments",
    "money market instruments",
    "treps / reverse repo",
    "gold",
    "reit/invit instruments",
    "others",
}

SUBSECTION_LABELS = {
    "certificate of deposit",
    "commercial paper",
    "treasury bill",
    "corporate debt market development fund",
    "exchange traded funds",
    "commodity future",
}

TOTAL_WORDS = {"sub total", "total", "grand total"}
STOP_MARKERS = {"notes:"}
SKIP_LABEL_PREFIXES = ("(a)", "(b)")


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Matches a month name immediately followed by a 2- or 4-digit year,
# with or without a separator ("October_2020", "Feb_2025", "May2026",
# "August_22") — this is the period the file is *for*, as distinct from
# the numeric download-date stamp most filenames also carry
# ("_07112020_") which would misidentify the period if matched instead.
_PERIOD_HINT_RE = re.compile(
    # (?!\d) instead of \b: filenames glue date fragments together with
    # underscores ("July_2020_08082020"), and \b never fires between two
    # digits joined by an underscore (both are \w), so it would silently
    # let \d{4} swallow just the first 4 digits of an 8-digit stamp
    # ("0807" out of "08072020") as a bogus "year". A negative lookahead
    # rejects any match immediately followed by another digit instead.
    r"(" + "|".join(_MONTHS) + r")[a-z]*[\s_\-]?(\d{4}|\d{2})(?!\d)", re.IGNORECASE
)


def _period_hint(filename: str) -> tuple[int, int] | None:
    m = _PERIOD_HINT_RE.search(filename)
    if not m:
        return None
    month = _MONTHS[m.group(1).lower()]
    year = int(m.group(2))
    if year < 100:
        year += 2000
    return year, month


def find_monthly_file(period_dir: str | Path) -> Path:
    period_dir = Path(period_dir)
    candidates = [
        p
        for p in list(period_dir.glob("*.xlsx")) + list(period_dir.glob("*.xls")) + list(period_dir.glob("*.XLS"))
        if _MONTHLY_FILE_RE.search(p.name)
    ]
    # De-dup in case a filename matches more than one glob pattern on a
    # case-insensitive filesystem.
    candidates = sorted(set(candidates), key=lambda p: p.name)

    if not candidates:
        raise FileNotFoundError(f"No monthly-portfolio workbook found in {period_dir}")
    if len(candidates) == 1:
        return candidates[0]

    # More than one monthly-portfolio file usually means a stale prior
    # month's file was left in the folder alongside the current one
    # (360 ONE's own naming isn't period-exclusive). Pick the one whose
    # filename actually names this period; if that's not unique either,
    # stay honest and raise rather than guess.
    year, month = (int(x) for x in period_dir.name.split("-"))
    matching = [p for p in candidates if _period_hint(p.name) == (year, month)]
    if len(matching) == 1:
        return matching[0]

    raise RuntimeError(f"Ambiguous monthly-portfolio workbooks in {period_dir}: {candidates}")


def _find_header_row(rows: list[list]) -> int:
    """Index (0-based) of the row containing "Name of the Instrument"."""
    for i, row in enumerate(rows[:10]):
        for cell in row:
            if isinstance(cell, str) and cell.strip() == "Name of the Instrument":
                return i
    raise ValueError("Header row not found")


def _parse_sheet(rows: list[list], *, amc: str, source_file: str, sheet_name: str, port_date: str) -> list[IntermediateRow]:
    header_row = _find_header_row(rows)
    scheme_name_raw = sheet_name
    section: str | None = None
    subsection: str | None = None
    out: list[IntermediateRow] = []

    for row in rows[header_row + 1 :]:
        name = row[1] if len(row) > 1 else None
        if name is None:
            continue
        key = str(name).strip()
        if not key:
            continue
        key_norm = key.lower()

        if key_norm in TOTAL_WORDS:
            continue
        if key.startswith(SKIP_LABEL_PREFIXES):
            continue
        if key_norm in STOP_MARKERS:
            break

        # xlrd (real .xls) hands back "" for a blank cell instead of
        # None the way openpyxl does — without normalizing, a blank
        # label row would look like it has_value and get emitted as a
        # fake holding.
        def _blank_none(v):
            return None if v == "" else v

        isin = _blank_none(row[2]) if len(row) > 2 else None
        industry_raw = _blank_none(row[3]) if len(row) > 3 else None
        quantity = _blank_none(row[4]) if len(row) > 4 else None
        market_value_raw = _blank_none(row[5]) if len(row) > 5 else None
        pct_raw = _blank_none(row[6]) if len(row) > 6 else None
        has_value = any(v is not None for v in (quantity, market_value_raw, pct_raw))

        if not has_value:
            if key_norm in TOP_LEVEL_SECTIONS:
                section = key
                subsection = None
            elif key_norm in SUBSECTION_LABELS:
                subsection = key
            # else: unrecognized footnote/label row — ignore
            continue

        out.append(
            IntermediateRow(
                amc=amc,
                source_file=source_file,
                sheet=sheet_name,
                scheme_name_raw=scheme_name_raw,
                section_header=subsection or section,
                security_name=key,
                isin=(str(isin).strip() if isin else None),
                industry_raw=(str(industry_raw).strip() if industry_raw not in (None, "") else None),
                quantity=quantity,
                market_value_raw=market_value_raw,
                pct_raw=pct_raw,
                port_date=port_date,
            )
        )

    return out


def parse_workbook(path: str | Path, *, port_date: str) -> list[IntermediateRow]:
    path = Path(path)
    # Byte-sniffed, not extension-trusted: some periods' "monthly
    # portfolio" file is a real .xls (OLE2), others are xlsx content
    # saved under a .xls name — see pipeline/isin_names.iter_sheets.
    out: list[IntermediateRow] = []
    for sheet_name, row_iter in iter_sheets(path):
        rows = [list(r) for r in row_iter]
        try:
            out.extend(_parse_sheet(rows, amc=AMC, source_file=path.name, sheet_name=sheet_name, port_date=port_date))
        except ValueError:
            # No "Name of the Instrument" header in this sheet — not a
            # scheme's holdings table (an empty stray "Sheet1", a notes
            # tab, etc). One non-holdings sheet in the workbook shouldn't
            # sink every real scheme sheet alongside it.
            continue
    return out


def parse_period(period_dir: str | Path) -> list[IntermediateRow]:
    period_dir = Path(period_dir)
    port_date = month_end_date(period_dir.name)
    return parse_workbook(find_monthly_file(period_dir), port_date=port_date)


if __name__ == "__main__":
    import sys

    period_dir = sys.argv[1] if len(sys.argv) > 1 else "data/raw/360_one/2026-05"
    rows = parse_period(period_dir)
    print(f"{len(rows)} rows from {find_monthly_file(period_dir).name}")
    schemes = sorted({r.sheet for r in rows})
    print(f"{len(schemes)} schemes: {schemes}")

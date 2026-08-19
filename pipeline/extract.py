"""Step 1 — the generic row-level parser. Builds on pipeline/isin_names.py's
already-proven column-finding (byte-sniffed xlsx/xls, ISIN column by
regex, name column by content uniqueness rather than trusting the
heading) and adds what that module doesn't need for its own job:
quantity / market value / % columns, section-header tracking for the
18-row non-ISIN set, scheme-name extraction, and support for more than
one table per sheet (Tata alone has 123 across 66 sheets).

This is the fallback every AMC gets before it earns a hand-written parser
in pipeline/amcs/.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pipeline.isin_check import is_valid_isin
from pipeline.isin_names import ISIN_HDR, NAME_HDR, SKIP_ROW, clean_name, iter_sheets, is_isin
from pipeline.schema import IntermediateRow, month_end_date

TOTAL_WORDS = {"total", "sub total", "subtotal", "grand total", "net total"}
# "GRAND TOTAL (AUM)", "Total Assets", "Sub Total :" etc all start with a
# total-word but carry trailing text an exact-match check misses — and a
# missed total row is a real holding-count and Corpus_Per bug (its own
# mkt value/percentage double-counts the scheme's true total). Tata puts
# "TOTAL" at the *end* instead ("EQUITY & EQUITY RELATED TOTAL",
# "PORTFOLIO TOTAL", "GOVERNMENT SECURITIES TOTAL") — same bug, opposite
# word order, so the trailing form needs its own alternative rather than
# assuming "total" only ever leads.
_TOTAL_ROW_RE = re.compile(
    r"^(sub\s*[-]?\s*total|grand\s*total|net\s*total|total)\b|\btotal\s*$", re.IGNORECASE
)
STOP_MARKERS = {"notes", "notes:", "note:", "disclaimer"}

# Some AMCs (HDFC) print a second "Asset Allocation" or "Rating Profile"
# summary table further down the same sheet, restating each category's
# share as a fresh row — "Equity  69.32", "Debt  ...", no ISIN — after
# the real holdings have already been listed and summed once. Nothing
# else distinguishes it from a real holding (it has a name-shaped cell
# and a numeric value), so a bare asset-class word standing in for a
# security name, with no ISIN, is treated as a restated category total
# rather than a security — it always doubles that category's true share
# otherwise (confirmed against HDFC Balanced Advantage Fund: every
# section's % sum came out to almost exactly double, and the total
# summed to 400% instead of 100%).
_ASSET_CLASS_LABELS = {
    "equity", "debt", "cash", "others", "gilt", "reits", "invits", "invit",
    "money market", "government securities", "sovereign", "treasury bills",
    "gold", "silver", "commodity", "derivatives", "futures", "options",
}

# Same bug, richer text: ICICI's files carry the same restated-category-
# total row, but as a full instrument-class heading with its own aggregate
# %/market value ("Equity & Equity Related Instruments (Note -1)",
# "Listed / Awaiting Listing On Stock Exchanges", "Units of Mutual Funds")
# rather than a bare word — the exact-match set above only catches the
# HDFC form. These are structurally never a real security's own name (a
# real holding has an issuer name; this is a SEBI-standard category
# label), and every one measured is followed by the individual holdings
# whose own %s sum to the same total, so it's always double-counted
# rather than dropped.
_CATEGORY_HEADER_RE = re.compile(
    r"^(equity\s*&\s*equity\s*related(\s*instruments?)?"
    r"|debt\s*instruments?"
    r"|money\s*market\s*instruments?"
    r"|government\s*securities?"
    r"|units?\s*of\s*mutual\s*funds?|mutual\s*fund\s*units?"
    r"|non[\s-]*convertible\s*debentures?\s*/?\s*bonds?"
    r"|listed\s*/?\s*awaiting\s*listing(\s*on\s*(the\s*)?stock\s*exchanges?)?"
    r"|unlisted(\s*securities?)?"
    r"|privately\s*placed(\s*/?\s*unlisted)?)"
    r"(\s*\(note\s*[-:]?\s*\d+\))?\s*$",
    re.IGNORECASE,
)

_WS = re.compile(r"\s+")


def _norm(text: Any) -> str:
    if text is None:
        return ""
    return _WS.sub(" ", str(text)).strip()


def _norm_lower(text: Any) -> str:
    return _norm(text).lower()


_QTY_HDR = ("quantity", "qty", "no. of shares", "no of shares", "no. of units", "units")
_MKT_HDR = ("market value", "market/fair value", "fair value", "value (rs", "amount", "mkt value", "mkt val")
_PCT_HDR = ("% to", "% of", "percentage")
_INDUSTRY_HDR = ("industry", "rating", "sector")


def _find_col(header_row: list[Any], keywords: tuple[str, ...]) -> int | None:
    for i, cell in enumerate(header_row):
        # Hyphens/underscores as word separators too — UTI's "MARKET-VALUE"
        # otherwise fails every _MKT_HDR keyword (all written with a
        # space) and the column silently isn't found at all.
        t = re.sub(r"[-_]", " ", _norm_lower(cell))
        if any(kw in t for kw in keywords):
            return i
    return None


def find_header_rows(rows: list[list[Any]]) -> list[int]:
    """Every row index carrying an "ISIN" cell — used to find table
    boundaries (more than one per sheet is normal, not an error) and the
    header text for the qty/market-value/% columns, which — unlike the
    name column — aren't a documented failure mode, so trusting the
    heading for them is fine."""
    out = []
    for i, row in enumerate(rows):
        for cell in row:
            if ISIN_HDR.match(_norm(cell)):
                out.append(i)
                break
    return out


def _best_isin_name_cols(
    rows: list[list[Any]], start: int, end: int
) -> tuple[int | None, int | None]:
    """Content-based ISIN/name column choice, restricted to this one
    table block — pipeline.isin_names._locate_columns does the same job
    scored over a whole sheet, which is fine for its single-table use but
    would blend columns across scheme boundaries here."""
    from pipeline.isin_names import _locate_columns

    block = rows[start:end]
    located = _locate_columns(block)
    if located is None:
        return None, None
    name_col, isin_col = located
    return isin_col, name_col


_BLANK_PLACEHOLDERS = {"", "nil", "n.a.", "na", "n/a", "-", "--", "none"}


def _cell(row: list[Any], col: int | None):
    if col is None or col >= len(row):
        return None
    v = row[col]
    if isinstance(v, str) and v.strip().lower() in _BLANK_PLACEHOLDERS:
        return None
    return v if v not in ("",) else None


_TITLE_BOILERPLATE = re.compile(
    r"portfolio statement|monthly portfolio|statement of portfolio|as on|scheme name",
    re.IGNORECASE,
)

# Rows that carry information but are never the scheme name itself: the
# scheme's own investment objective ("(An open ended scheme investing
# in...)", "AN OPEN ENDED DEBT SCHEME...", bullet-point objectives), or
# AMC letterhead boilerplate (registered office, CIN, investment manager
# line). Both regularly sit closer to the ISIN header than the actual
# name and would otherwise win a "closest non-boilerplate row" scan.
_TITLE_EXCLUDE = re.compile(
    r"^\(.*\)$"  # entire cell wrapped in parens - always an objective/description
    r"|^an?\s+open|^an?\s+close"  # "An open ended...", "A close ended..."
    r"|^[•\-•]"  # bullet-point objective lines
    r"|registered office|investment manager|^cin[:\s]|^email|^visit us",
    re.IGNORECASE,
)
_FUND_KEYWORD_RE = re.compile(r"\bfund\b|\betf\b|\bplan\b|\bscheme\b|\bfof\b", re.IGNORECASE)


def _find_scheme_name(rows: list[list[Any]], block_start: int, header_idx: int, sheet_name: str) -> str:
    for r in range(header_idx - 1, max(block_start - 1, -1), -1):
        row = rows[r]
        for c, cell in enumerate(row):
            t = _norm_lower(cell)
            if "scheme name" in t:
                for c2 in range(c + 1, len(row)):
                    v = _norm(row[c2])
                    if v:
                        return v
        for cell in row:
            t = _norm(cell)
            if not t:
                continue
            # "Portfolio Statement of <scheme> as on <date>" / "Portfolio
            # of <scheme> as on <date>" carry the real name embedded in
            # otherwise-boilerplate wording — pull it out before falling
            # back to "closest non-boilerplate row", which would
            # otherwise reject this whole line and grab the scheme's
            # parenthetical description on the next row instead.
            m = re.search(
                r"portfolio\s+(?:statement\s+)?of\s+(.+?)(?:\s+as\s+on\b|\s+as\s+at\b|$)",
                t,
                re.IGNORECASE,
            )
            if m and m.group(1).strip():
                return m.group(1).strip()
    candidates: list[str] = []
    for r in range(header_idx - 1, max(block_start - 1, -1), -1):
        row = rows[r]
        for cell in row:
            t = _norm(cell)
            if len(t) > 8 and not _TITLE_BOILERPLATE.search(t) and not _TITLE_EXCLUDE.search(t):
                candidates.append(t)
    # Closest-to-header candidate that actually looks like a fund name
    # wins; a bare description slipping past the exclude list (row order
    # varies by AMC) shouldn't beat one that says "Fund"/"ETF"/"Plan".
    for t in candidates:
        if _FUND_KEYWORD_RE.search(t):
            return t
    if candidates:
        return candidates[0]
    return sheet_name


def parse_sheet(
    rows: list[list[Any]], *, amc: str, source_file: str, sheet_name: str, port_date: str
) -> list[IntermediateRow]:
    header_positions = find_header_rows(rows)
    out: list[IntermediateRow] = []
    if not header_positions:
        return out

    for i, header_idx in enumerate(header_positions):
        block_start = 0 if i == 0 else header_positions[i - 1] + 1
        block_end = header_positions[i + 1] if i + 1 < len(header_positions) else len(rows)

        header_row = rows[header_idx]
        qty_col = _find_col(header_row, _QTY_HDR)
        mkt_col = _find_col(header_row, _MKT_HDR)
        pct_col = _find_col(header_row, _PCT_HDR)
        industry_col = _find_col(header_row, _INDUSTRY_HDR)
        isin_col, name_col = _best_isin_name_cols(rows, header_idx + 1, block_end)

        if isin_col is None and name_col is None:
            continue
        scheme_name_raw = _find_scheme_name(rows, block_start, header_idx, sheet_name)

        section: str | None = None
        for r in range(header_idx + 1, block_end):
            row = rows[r]
            name_val = _cell(row, name_col)
            raw_name = _norm(name_val) if name_val is not None else ""

            isin_val = _cell(row, isin_col)
            qty_val = _cell(row, qty_col)
            mkt_val = _cell(row, mkt_col)
            pct_val = _cell(row, pct_col)
            industry_val = _cell(row, industry_col)
            # A number, or a numeric-looking string ("4.74", from xlrd's
            # all-strings-are-strings cells) — not just non-None. Footer
            # legend lines ("@ Less than 0.01%.", "~ YTC is disclosed...")
            # can land whole sentences of text in the quantity/value
            # columns once the real table has ended; without this check
            # any non-None text counts as "has a value" and the row gets
            # emitted as a fake holding.
            def _is_numeric(v):
                if isinstance(v, (int, float)):
                    return True
                if isinstance(v, str):
                    try:
                        float(v)
                        return True
                    except ValueError:
                        return False
                return False

            has_value = any(_is_numeric(v) for v in (qty_val, mkt_val, pct_val))

            if raw_name and (SKIP_ROW.match(raw_name) or NAME_HDR.search(raw_name) or ISIN_HDR.match(raw_name)):
                continue

            # A single holding's own share of a scheme is never anywhere
            # near 1000% — a pct_val past that bound is a corrupted cell,
            # not data (Edelweiss's files carry a recurring garbage row,
            # same literal value on every sheet, with pct_raw in the
            # billions and a matching absurd market value — a broken
            # template/formula artifact, not a real holding). Bounding on
            # the % column catches it generically rather than special-
            # casing the AMC.
            if _is_numeric(pct_val) and abs(float(pct_val)) > 1000:
                continue

            if not has_value:
                # A label row with no figures: either a section header
                # ("Money Market Instruments", "Listed/Awaiting listing...")
                # or a stray footnote. Its text isn't necessarily in the
                # name column — some AMCs put section labels one column
                # to the left of where holding names live — so fall back
                # to the first non-empty text cell in the row.
                label = raw_name or next((_norm(c) for c in row if _norm(c)), "")
                label_l = label.lower()
                if label_l in STOP_MARKERS or any(label_l.startswith(m) for m in STOP_MARKERS):
                    break
                if label:
                    section = label
                continue

            isin_str = str(isin_val).strip().upper() if isin_val else None
            if isin_str and not is_valid_isin(isin_str):
                # Not really an ISIN — either a "Sub Total" row's label
                # landing in the ISIN column (layout shifts for a subtotal
                # line), or a derivatives contract code that matches the
                # 12-character shape exactly but fails the ISO 6166 check
                # digit (AUBANK300626, BHEL29052025 — see
                # pipeline/isin_check.py). Either way it doesn't belong in
                # the ISIN column: keep it around only as a fallback name
                # candidate, same as any other non-ISIN label — if the
                # name column is also empty, this is the only place the
                # row's own label is.
                fallback_from_isin_col = isin_str
                isin_str = None
            else:
                fallback_from_isin_col = None

            name, _is_deriv = clean_name(raw_name) if raw_name else ("", False)
            if not name and isin_str is None:
                # Has figures but nothing usable in the name column and
                # no real ISIN either — some AMCs put a row like "Net
                # Current Assets/(Liabilities)" or "Sub Total" with the
                # label back in a different column than the one holding
                # names normally live in. Last resort: whatever text
                # landed in the ISIN column, else any non-empty cell.
                fallback = fallback_from_isin_col or next((_norm(c) for c in row if _norm(c)), "")
                name, _is_deriv = clean_name(fallback) if fallback else ("", False)
                if not name:
                    continue
            if name:
                name_l = name.lower()
                if name_l in STOP_MARKERS or any(name_l.startswith(m) for m in STOP_MARKERS):
                    break
                if name_l in TOTAL_WORDS or _TOTAL_ROW_RE.search(name_l):
                    continue

            if isin_str is None and name.strip().lower() in _ASSET_CLASS_LABELS:
                continue
            if isin_str is None and _CATEGORY_HEADER_RE.match(name.strip()):
                continue

            out.append(
                IntermediateRow(
                    amc=amc,
                    source_file=source_file,
                    sheet=sheet_name,
                    scheme_name_raw=scheme_name_raw,
                    section_header=section,
                    security_name=name,
                    isin=isin_str,
                    industry_raw=(_norm(industry_val) or None),
                    quantity=qty_val,
                    market_value_raw=mkt_val,
                    pct_raw=pct_val,
                    port_date=port_date,
                )
            )

    return out


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _drop_duplicate_blocks(rows: list[IntermediateRow]) -> list[IntermediateRow]:
    """Applied once per period, over every file's rows combined — not
    per-workbook. The observed cause (PPFAS, and likely ICICI / Baroda BNP
    / Motilal / Samco, all "one file per scheme" layouts) is a roll-up
    workbook that restates every scheme already covered by its own
    per-scheme file, whose filename doesn't happen to match
    _CONSOLIDATED_NAME_RE (PPFAS's is "PPFAS_Monthly_Portfolio_Report...",
    not "...All Schemes..." or "...Consolidated..."), so
    find_portfolio_files keeps both. A same-sheet restated table (a second
    "Asset Allocation" breakdown, the dedicated HDFC-only patch above
    already handles the single-row-per-category form of that) would hit
    the same detection here too.

    Detection: a scheme whose % column sums to ≈N×100 (or ≈N×1, before
    convert.py's scale detection runs) for some small integer N, and whose
    row list — across every source file, in encounter order — splits into
    N equal-length, element-identical (by ISIN + market value) chunks.
    Printing the same holdings twice is exact duplication, not an
    unrelated coincidence. Keep only the first chunk; anything less exact
    than that is left alone rather than guessed at.
    """
    by_scheme: dict[str, list[IntermediateRow]] = {}
    for r in rows:
        by_scheme.setdefault(r.scheme_name_raw, []).append(r)

    keep_ids: set[int] = {id(r) for r in rows}
    for srows in by_scheme.values():
        pcts = [_to_float(r.pct_raw) for r in srows]
        total_pct = sum(p for p in pcts if p is not None)
        if total_pct <= 0 or len(srows) < 4:
            continue

        for n in (4, 3, 2):
            if len(srows) % n != 0:
                continue
            target_pct = 100 * n if total_pct > n * 5 else n  # percent-scale vs fraction-scale
            if abs(total_pct - target_pct) > 0.1 * target_pct:
                continue

            chunk_len = len(srows) // n
            chunks = [srows[i * chunk_len:(i + 1) * chunk_len] for i in range(n)]

            def _row_key(r: IntermediateRow):
                return (r.isin, r.security_name, _to_float(r.market_value_raw))

            first_keys = [_row_key(r) for r in chunks[0]]
            if all([_row_key(r) for r in c] == first_keys for c in chunks[1:]):
                for c in chunks[1:]:
                    for r in c:
                        keep_ids.discard(id(r))
                break

    return [r for r in rows if id(r) in keep_ids]


def parse_workbook(path: str | Path, *, amc: str, port_date: str) -> list[IntermediateRow]:
    path = Path(path)
    out: list[IntermediateRow] = []
    for sheet_name, row_iter in iter_sheets(path):
        rows = [list(r) for r in row_iter]
        out.extend(
            parse_sheet(rows, amc=amc, source_file=path.name, sheet_name=sheet_name, port_date=port_date)
        )
    return out


_CONSOLIDATED_NAME_RE = re.compile(
    r"all[\s_-]*scheme|all[\s_-]*fund|consolidated|master\s*sheet", re.IGNORECASE
)


def find_portfolio_files(period_dir: str | Path) -> list[Path]:
    period_dir = Path(period_dir)
    out = []
    for p in sorted(period_dir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() in (".xlsx", ".xls"):
            out.append(p)

    # Some AMCs (SBI) ship one file per scheme *and* an "all schemes"
    # roll-up covering the same data in one workbook — parsing both
    # double-counts every scheme that appears in each. An AMC that only
    # ever publishes the roll-up (Kotak's "Consolidated Sebi Portfolio")
    # has nothing else to exclude it in favor of, so it stays; only drop
    # it when per-scheme files are also present.
    if len(out) > 1:
        non_consolidated = [p for p in out if not _CONSOLIDATED_NAME_RE.search(p.name)]
        if non_consolidated:
            out = non_consolidated

    return out


def parse_period(period_dir: str | Path, *, amc: str) -> list[IntermediateRow]:
    period_dir = Path(period_dir)
    port_date = month_end_date(period_dir.name)
    out: list[IntermediateRow] = []
    for path in find_portfolio_files(period_dir):
        try:
            out.extend(parse_workbook(path, amc=amc, port_date=port_date))
        except Exception:
            continue
    return _drop_duplicate_blocks(out)

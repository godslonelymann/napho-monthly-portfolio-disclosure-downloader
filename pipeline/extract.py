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

from pipeline.isin_check import is_valid_isin, looks_like_contract_code
from pipeline.isin_names import ISIN_HDR, NAME_HDR, SKIP_ROW, clean_name, iter_sheets, is_isin
from pipeline.non_isin import load_non_isin_rules
from pipeline.schema import IntermediateRow, month_end_date

TOTAL_WORDS = {"total", "sub total", "subtotal", "grand total", "net total"}
# "Net Assets" (and "Net Asset Value") get their own set rather than
# joining TOTAL_WORDS: a "Sub Total"/"Total" row is mid-table — a
# Treasury Bill sub-total, say — with more real holdings still to come
# below it, so those stay a skip-this-row "continue". "Net Assets" is
# the scheme's 100%-of-NAV grand total and, by the SEBI portfolio
# format, always the last row of the actual holdings table — anything
# after it (Franklin Templeton's debt funds: an "Outstanding Interest
# Rate Swap Position" table restating each swap counterparty's notional
# value and % as if it were a holding) is off-balance-sheet derivatives
# disclosure, not part of the portfolio. Ending the block here (break,
# not continue) drops that trailing table wholesale instead of reading
# its notional %s as ~46 more points of Corpus_Per.
_NET_ASSETS_WORDS = {"net assets", "net asset value"}
# Franklin Templeton's summary row is labelled "Net Assets", not "Total" —
# no "total" word at all, so SKIP_ROW's `net\s*assets?` alternative can't
# rescue it either: this row's label lands in the ISIN column (a shifted
# total-row layout), so raw_name (from the name column) comes back empty
# and SKIP_ROW never even runs — the label only surfaces later via the
# fallback-name path below. Without the break above, it would otherwise
# fall through the plain "total"-word check as a fake holding worth the
# entire fund (its own Mkt_Value/Corpus_Per cells restate the scheme's
# grand total), same failure mode _TOTAL_ROW_RE below exists to catch.
# "GRAND TOTAL (AUM)", "Total Assets", "Sub Total :" etc all start with a
# total-word but carry trailing text an exact-match check misses — and a
# missed total row is a real holding-count and Corpus_Per bug (its own
# mkt value/percentage double-counts the scheme's true total). Tata puts
# "TOTAL" at the *end* instead ("EQUITY & EQUITY RELATED TOTAL",
# "PORTFOLIO TOTAL", "GOVERNMENT SECURITIES TOTAL") — same bug, opposite
# word order, so the trailing form needs its own alternative rather than
# assuming "total" only ever leads.
_TOTAL_ROW_RE = re.compile(
    r"^(sub\s*[-_]?\s*total|grand[\s_]*total|net[\s_]*total|total)\b|\btotal\s*$", re.IGNORECASE
)
# NJ Mutual Fund prints "GRAND_TOTAL" with an underscore in place of the
# space "grand\s*total" expects (\s* doesn't match "_"), so its 100%-of-
# NAV summary row slipped through as a fake extra holding — every scheme
# came out at 200% instead of 100%. [\s_]* covers both separators without
# assuming which one a given AMC uses.
# Derivative tables are NOT stopped here. ICRA carries every futures row
# (7737 in the May 2026 sample, none with an ISIN), and they are not
# optional detail: a scheme's Corpus_Per only reaches 100 with them in.
# Arbitrage funds hold large *short* futures at negative Corpus_Per, so
# dropping them leaves the portfolio summing far over 100 — scheme
# 153977 comes to 184.99% without its 123 futures rows and exactly
# 100.00% with them. Across the 270 schemes ICRA gives derivative rows,
# 99.3% sum to 100 including them and 24.1% do so excluding them.
#
# What still ends a table is a genuine end-of-table marker: notes and
# disclaimers, plus the "Net Assets" grand total handled below.
STOP_MARKERS = {
    "notes", "notes:", "note:", "disclaimer",
}

# A holding is recognised by content, not by wording.
#
# What stood here was three lists of phrases junk rows happen to use:
# _ASSET_CLASS_LABELS ("equity", "debt", "long", "short"),
# _CATEGORY_HEADER_RE (SEBI's category headings in nine spellings), and
# _NET_RECEIVABLES_LABELS. They could only ever grow — every AMC invents
# new wording — and they interfered with each other: a "commercial
# papers" entry added for a Liquid Fund dropped real holdings from an
# Ultra Short Term one.
#
# The replacement is a property of the row rather than of its text, and
# it lives in convert.py (_is_holding) rather than here: deciding it
# needs all three of the row's ISIN cell, its instrument classification,
# and the name->ISIN lookup, and only convert.py holds the last of those.
# Judging on the first two alone drops real holdings whose ISIN cell the
# AMC simply left blank — HDFC Nifty Next 50 lost 2.9% of its corpus
# that way. extract.py stays a reader; what counts as a holding is a
# classification question.
_RULES_CACHE: dict[str, Any] = {}


def _non_isin_rules(amc: str):
    if amc not in _RULES_CACHE:
        _RULES_CACHE[amc] = load_non_isin_rules(amc)
    return _RULES_CACHE[amc]


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


def _header_isin_name_cols(header_row: list[Any]) -> tuple[int | None, int | None]:
    """Fallback for a table block whose data rows carry no valid ISIN at
    all — a stock/index futures block, say, where every row's ISIN cell
    is blank because derivatives don't have one. _best_isin_name_cols's
    content-based scan needs at least one real ISIN value to anchor on
    and returns nothing here, silently dropping the whole block (ICICI
    Prudential Infrastructure Fund's "Details of Stock Future / Index
    Future" table, 2 rows, both missing ISIN). The header row itself
    still names its columns ("Company/Issuer/Instrument Name", "ISIN"),
    so fall back to that text when content-based detection comes up
    empty — find_header_rows already guarantees this row has an ISIN
    header cell."""
    isin_col = None
    name_col = None
    for i, cell in enumerate(header_row):
        t = _norm(cell)
        if isin_col is None and ISIN_HDR.match(t):
            isin_col = i
        if name_col is None and NAME_HDR.search(t):
            name_col = i
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
    r"|registered office|investment manager|^cin[:\s]|^email|^visit us"
    # SEBI-standard portfolio category headers ("Units of an Alternative
    # Investment Fund (AIF)", "Units of Mutual Fund", "Units of REITs",
    # ITI's list-lettered "a) Mutual Fund Units / Exchange Traded Funds")
    # contain the same "Fund"/"Plan"/"Scheme" keywords a real scheme name
    # does, so without this they can outrank the actual name in the
    # fund-keyword candidate scan below — ICICI Prudential Infrastructure
    # Fund's derivatives-annex table (a second ISIN-header block on the
    # same sheet, past the end of the holdings table) picked "Units of an
    # Alternative Investment Fund (AIF)" as its scheme name and every row
    # under it failed to match any real scheme. An optional leading list
    # marker ("a)", "1.") is stripped first since these categories are
    # often enumerated.
    r"|^(?:[a-z0-9]+[).]\s*)?(units?\s*of\b|mutual\s*fund\s*units?\b|exchange\s*traded\s*funds?\b)"
    # Annex-table titles ("Details of Stock Future / Index Future",
    # "Details of derivatives") sit directly above their own ISIN header,
    # closer to it than any real scheme name — same failure mode as the
    # category headers above, just without a "Fund" keyword to trigger
    # the fund-keyword-priority scan, so it wins via the closest-candidate
    # fallback instead.
    r"|^details\s+of\b",
    re.IGNORECASE,
)
_FUND_KEYWORD_RE = re.compile(r"\bfund\b|\betf\b|\bplan\b|\bscheme\b|\bfof\b", re.IGNORECASE)


_ISIN_CELL_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _looks_like_data_row(row: list[Any]) -> bool:
    """A row of holdings, as opposed to a title/label row."""
    numeric = 0
    for cell in row:
        t = _norm(cell)
        if not t:
            continue
        if _ISIN_CELL_RE.match(t.upper()):
            return True
        if isinstance(cell, bool):
            continue
        if isinstance(cell, (int, float)):
            numeric += 1
            continue
        try:
            float(t.replace(",", ""))
            numeric += 1
        except ValueError:
            pass
    return numeric >= 2


def _find_scheme_name(rows: list[list[Any]], block_start: int, header_idx: int, sheet_name: str) -> str | None:
    """Scan upward from the header for this block's scheme name, stopping
    at the first row of actual data.

    The stop is what keeps a later block honest. A scheme's title sits in
    the header area above its table, never below another table's rows, so
    once the scan reaches data it has left this block's title area and
    everything above belongs to the previous scheme. Without it the
    window for a second table spans the whole first table, and any
    fund-shaped text anywhere in it wins: ICICI's arbitrage sheet ends
    with a "Details of Stock Future / Index Future" annex whose 298
    futures rows were credited to "ICICI Prudential Money Market fund -
    Direct Plan - Growth Option", a fund mentioned in passing further up.
    Finding nothing is the right answer for an annex — parse_sheet then
    carries the previous block's scheme forward, which is what it is.
    """
    for r in range(header_idx - 1, max(block_start - 1, -1), -1):
        row = rows[r]
        if _looks_like_data_row(row):
            break
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
            # UTI's consolidated file stacks every scheme's portfolio in
            # one giant sheet (8266 rows, 80+ schemes), each block opening
            # with a literal "SCHEME: UTI - Arbitrage Fund" label rather
            # than the fund name on its own. Without stripping the label,
            # scheme_name_raw comes out as "SCHEME: UTI - Arbitrage Fund"
            # — a string the scheme-name matcher (and the schemes.csv
            # mapping it feeds) has never seen, so it gets a low-
            # confidence fuzzy match to a completely unrelated scheme,
            # the same wrong AMFI code for every one of the ~30 blocks
            # this happened to (all landing on "UTI Regular Saving Fund
            # (Segregated)" — its own real 15 rows plus everyone else's,
            # 1731 total against ICRA's true 0 for a wound-down fund).
            m = re.search(r"^scheme\s*:\s*(.+)$", t, re.IGNORECASE)
            if m and m.group(1).strip():
                return m.group(1).strip()
    candidates: list[str] = []
    for r in range(header_idx - 1, max(block_start - 1, -1), -1):
        row = rows[r]
        if _looks_like_data_row(row):
            break
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
    # The plain "closest candidate" fallback is only safe for a sheet's
    # first table, where the window reaches all the way up to the sheet's
    # own title rows. A later table's window (block_start > 0) never
    # reaches that title — it's bounded by the previous table's header —
    # so nothing in range is ever the real scheme name; a derivatives
    # annex's own leftover category/footer text ("Total Net Assets",
    # "TREPS") would otherwise win here and get treated as a distinct,
    # unmatchable scheme. Return None so the caller carries the previous
    # table's real scheme name forward instead.
    if candidates and block_start == 0:
        return candidates[0]
    return None


def parse_sheet(
    rows: list[list[Any]], *, amc: str, source_file: str, sheet_name: str, port_date: str
) -> list[IntermediateRow]:
    header_positions = find_header_rows(rows)
    out: list[IntermediateRow] = []
    if not header_positions:
        return out

    prev_scheme_name: str | None = None
    stopped = False
    for i, header_idx in enumerate(header_positions):
        if stopped:
            # A NOTES:/Disclaimer marker earlier in this sheet already
            # ended the real holdings table for good. A post-mortem
            # footnote disclosure — JM Financial's "Value of the security
            # ... classified as below investment grade or default" note,
            # listing a defaulted bond at Rs.0/0% — can carry its own
            # "Security | ISIN | Rs. In Lakhs" table header, which
            # find_header_rows has no way to tell apart from a real
            # holdings header. Without this, that table gets parsed as a
            # fresh block of real holdings (the ISIN reappearing as a
            # phantom zero-value row not in ICRA at all) rather than
            # skipped along with the rest of the sheet's tail.
            break
        block_start = 0 if i == 0 else header_positions[i - 1] + 1
        block_end = header_positions[i + 1] if i + 1 < len(header_positions) else len(rows)

        header_row = rows[header_idx]
        qty_col = _find_col(header_row, _QTY_HDR)
        mkt_col = _find_col(header_row, _MKT_HDR)
        pct_col = _find_col(header_row, _PCT_HDR)
        industry_col = _find_col(header_row, _INDUSTRY_HDR)
        isin_col, name_col = _best_isin_name_cols(rows, header_idx + 1, block_end)
        if isin_col is None and name_col is None and (qty_col is not None or pct_col is not None):
            # Only trust the header row's own column labels when this
            # still looks like a holdings table (it reports quantity or a
            # %-of-NAV, same as every real holding row does) — a "Total
            # Securities in default" or similar administrative disclosure
            # table can carry an "ISIN Code" column heading purely by
            # convention while never reporting one, with only a bare
            # market-value-in-Rs column beside it. Falling back for that
            # table too would extend this block's row range across
            # whatever unrelated NAV/dividend-per-unit tables happen to
            # follow it with no header of their own to stop at, and their
            # numbers would get read as holdings (ITI Liquid Fund: NAV
            # and per-unit dividend figures for each plan option, ~10
            # rows, all landing in Mkt_Value).
            isin_col, name_col = _header_isin_name_cols(header_row)

        if isin_col is None and name_col is None:
            continue
        # No real scheme-name text found in this block's own window (a
        # derivatives-annex table past the end of the holdings table has
        # nothing but category headers and footnotes above it) — this is
        # a continuation of the same sheet's scheme, not a nameless one,
        # so carry the previous block's name forward rather than falling
        # back to the sheet's short internal code (e.g. "INFRA"), which
        # would never match the scheme lookup and silently drop the rows.
        scheme_name_raw = _find_scheme_name(rows, block_start, header_idx, sheet_name) or prev_scheme_name or sheet_name
        prev_scheme_name = scheme_name_raw

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

            # A "Notes :" (or "Disclaimer") row marks the end of the
            # holdings table for good, not just this one line — but it
            # also matches SKIP_ROW's `notes?\s*:` below, and SKIP_ROW
            # only skips the row and keeps scanning. Checked first so it
            # wins: everything past it is footnotes and per-plan-option
            # NAV/dividend tables (Motilal Oswal Ultra Short Term Fund's
            # "NAV at the beginning/end of the month" and "IDCW declared"
            # tables list every plan option with a numeric NAV-per-unit
            # value in the quantity column and no ISIN, so without this
            # they read as ~30 extra ISIN-less holdings per scheme).
            if raw_name:
                stop_l = raw_name.lower()
                if stop_l in STOP_MARKERS or any(stop_l.startswith(m) for m in STOP_MARKERS):
                    stopped = True
                    break

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
                    stopped = True
                    break
                if label:
                    section = label
                continue

            isin_str = str(isin_val).strip().upper() if isin_val else None
            if isin_str and (not is_valid_isin(isin_str) or looks_like_contract_code(isin_str)):
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
                    stopped = True
                    break
                if name_l in _NET_ASSETS_WORDS:
                    stopped = True
                    break
                if name_l in TOTAL_WORDS or _TOTAL_ROW_RE.search(name_l):
                    continue


            # Mahindra Manulife sometimes prints "Net Receivables /
            # (Payables)" as two separate consecutive rows within one
            # scheme's table — a genuine split in the source, but ICRA's
            # own dataset merges them into a single row summing both
            # values. Two rows here would read as a real extra holding:
            # right row count, wrong total. Scoped tightly to this one
            # bucket label (not "any repeated name") — a blanket same-
            # name merge tried here first also matched genuinely distinct
            # consecutive rows that happen to share text (two separate
            # TREPS legs both booked against "Clearing Corporation of
            # India Ltd.", two different-dated Interest Rate Swap legs)
            # and wrongly collapsed them, costing real passes elsewhere
            # (Shriram, Nippon India, Bajaj Finserv).
            if (
                isin_str is None
                and (_non_isin_rules(amc).recognize(name, section) or (None,))[0] == "Net Receivables/(Payables)"
                and out
                and out[-1].isin is None
                and out[-1].security_name == name
                and out[-1].scheme_name_raw == scheme_name_raw
            ):
                prev = out[-1]
                prev.market_value_raw = (_to_float(prev.market_value_raw) or 0) + (_to_float(mkt_val) or 0)
                prev.pct_raw = (_to_float(prev.pct_raw) or 0) + (_to_float(pct_val) or 0)
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


def _drop_repeated_labels(rows: list[IntermediateRow], amc: str) -> list[IntermediateRow]:
    """Drop rows whose "name" is a column value, not a security name.

    A portfolio lists each security once, so a name with no ISIN, naming
    none of the known non-ISIN instruments, and appearing more than once
    within a single scheme is not a security — it is a column of the
    source being read as one. Baroda BNP's derivatives annex heads its
    first column "Long/Short", which the column scan picks as the name
    column, so 240 rows across its schemes arrived named "Short".

    Here rather than in convert.py, where the rest of the is-this-a-
    holding test lives, because _drop_duplicate_blocks below has to see
    the rows already cleaned: it identifies a restated scheme by
    splitting its rows into N identical chunks, and these extras break
    the split, leaving one Baroda scheme at 184 rows against ICRA's 91.
    """
    rules = _non_isin_rules(amc)
    by_scheme: dict[str, list[IntermediateRow]] = {}
    for row in rows:
        by_scheme.setdefault(row.scheme_name_raw, []).append(row)

    drop: set[int] = set()
    for srows in by_scheme.values():
        counts: dict[str, int] = {}
        for r in srows:
            if r.isin or rules.recognize(r.security_name, r.section_header):
                continue
            key = r.security_name.strip().lower()
            counts[key] = counts.get(key, 0) + 1
        repeated = {k for k, n in counts.items() if n > 1}
        if not repeated:
            continue
        for r in srows:
            if r.isin or rules.recognize(r.security_name, r.section_header):
                continue
            if r.security_name.strip().lower() in repeated:
                drop.add(id(r))
    return [r for r in rows if id(r) not in drop]


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
        sheet_rows = parse_sheet(
            rows, amc=amc, source_file=path.name, sheet_name=sheet_name, port_date=port_date
        )
        out.extend(sheet_rows)
        # Every sheet is also checked for a derivative disclosure, not
        # just the ones the holdings parser came up empty on: some AMCs
        # give the disclosure its own sheet (HDFC), others append it
        # below the holdings on the same one (Mirae). It has no ISIN
        # column for find_header_rows to anchor on either way. Schemes
        # named on this sheet are offered first, so a same-sheet
        # disclosure attaches to the right one.
        deriv = parse_derivative_sheet(
            rows, amc=amc, source_file=path.name, sheet_name=sheet_name, port_date=port_date,
            known_schemes=[r.scheme_name_raw for r in sheet_rows] + [r.scheme_name_raw for r in out],
        )
        if deriv:
            # The holdings parser sometimes reaches the same table on its
            # own; keep whichever copy is already there rather than
            # counting the position twice.
            seen = {
                (r.scheme_name_raw, _to_float(r.quantity), _to_float(r.market_value_raw))
                for r in out
            }
            out.extend(
                r for r in deriv
                if (r.scheme_name_raw, _to_float(r.quantity), _to_float(r.market_value_raw)) not in seen
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


# SEBI's derivative disclosure (circular CIR/IMD/DF/11/2010) is published
# as its own table — usually its own sheet — and carries no ISIN column at
# all, because a futures position has no ISIN. find_header_rows keys on an
# ISIN header, so the entire sheet was invisible: HDFC Arbitrage's
# "DerivativeHDFCAR" sheet holds 221 futures rows and contributed nothing.
# Those rows are not optional. ICRA carries every one of them, and a
# scheme's Corpus_Per only reaches 100 with them included — arbitrage
# funds hold the short leg at negative value, so without it a portfolio
# sums to ~185% instead of 100%.
_DERIV_UNDERLYING_HDR = re.compile(r"^underlying\b", re.IGNORECASE)
# "Long / Short" is the quantity column for some AMCs and a direction
# label ("Short") for others, which then carry the size in a separate
# "Quantity" column — so an explicit one wins when both are present.
_DERIV_QTY_HDR = re.compile(r"^quantity\b", re.IGNORECASE)
_DERIV_LONGSHORT_HDR = re.compile(r"long\s*/?\s*\(?\s*short", re.IGNORECASE)
_DERIV_MKT_HDR = re.compile(r"market\s*/?\s*(fair\s*)?value|fair\s*value", re.IGNORECASE)
_DERIV_PCT_HDR = re.compile(r"%\s*to\b|%\s*of\b", re.IGNORECASE)
_DERIV_TITLE_RE = re.compile(r"derivative\s+disclosure\s*[-:]\s*(.+)$", re.IGNORECASE)
# Some AMCs head the disclosure with a period instead of a scheme
# ("(4) Derivative disclosure for the period ending May 31,2026"). There
# is no name to take from that, so the scheme comes from the rows the
# workbook has already yielded.
_DERIV_TITLE_NO_NAME_RE = re.compile(
    r"derivative\s+disclosure\s+for\s+the\s+period|^\(?\d+\)?\s*derivative\s+disclosure",
    re.IGNORECASE,
)
# Only the futures section is a portfolio holding. The same sheet also
# carries "B. Other than Hedging Positions", options sections, and
# totals; each is introduced by its own lettered heading, so a heading
# that is not the futures one ends the run of rows being collected.
_DERIV_FUTURES_SECTION_RE = re.compile(
    r"^[a-z][).]\s*.*hedging\s+positions?\s+through\s+futures", re.IGNORECASE
)
_DERIV_SECTION_RE = re.compile(r"^[a-z][).]\s+\S", re.IGNORECASE)


def _resolve_scheme_alias(title: str | None, known: list[str]) -> str | None:
    if not title:
        return known[-1] if known else None
    t = _match_norm_name(title)
    if not t:
        return title
    for name in known:
        k = _match_norm_name(name)
        if k and (k == t or t in k or k in t):
            return name
    return title


def _match_norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", (s or "").upper())).strip()


def parse_derivative_sheet(
    rows: list[list[Any]], *, amc: str, source_file: str, sheet_name: str, port_date: str,
    known_schemes: list[str] | None = None,
) -> list[IntermediateRow]:
    """Rows from a SEBI derivative-disclosure table.

    Separate from parse_sheet because the shape is genuinely different —
    no ISIN column to anchor on, and the quantity column is headed
    "Long / (Short)" rather than "Quantity" — not because this AMC words
    things differently. The columns are located by their header text, and
    the section headings decide which rows are in scope.
    """
    out: list[IntermediateRow] = []
    title = None
    has_disclosure = False
    for row in rows:
        for c in row:
            t = _norm(c)
            if not t:
                continue
            m = _DERIV_TITLE_RE.search(t)
            if m:
                title, has_disclosure = m.group(1).strip(), True
                break
            if _DERIV_TITLE_NO_NAME_RE.search(t) or _DERIV_FUTURES_SECTION_RE.match(t):
                has_disclosure = True
        if title:
            break
    if not has_disclosure:
        return out

    # The disclosure titles the scheme plainly ("DERIVATIVE DISCLOSURE -
    # HDFC Arbitrage Fund") while its holdings sheet spells out the SEBI
    # product description too ("... (An open ended scheme investing in
    # arbitrage opportunities)"). Those are different mapping keys, so
    # taking the title verbatim leaves every one of these rows unmapped
    # and silently dropped. Resolve it against the schemes this workbook
    # has already yielded, and use their wording.
    scheme = _resolve_scheme_alias(title, known_schemes or [])
    if scheme is None:
        return out

    name_col = qty_col = mkt_col = industry_col = pct_col = None
    in_futures = False
    for row in rows:
        texts = [_norm(c) for c in row]
        joined = " ".join(t for t in texts if t)

        if _DERIV_FUTURES_SECTION_RE.match(joined):
            in_futures = True
            name_col = qty_col = mkt_col = industry_col = pct_col = None
            continue
        if in_futures and _DERIV_SECTION_RE.match(joined) and not _DERIV_FUTURES_SECTION_RE.match(joined):
            in_futures = False
            continue
        if not in_futures:
            continue

        if name_col is None:
            if any(_DERIV_UNDERLYING_HDR.match(t) for t in texts) and any(
                _DERIV_QTY_HDR.match(t) or _DERIV_LONGSHORT_HDR.search(t) for t in texts
            ):
                longshort_col = None
                for i, t in enumerate(texts):
                    if not t:
                        continue
                    if name_col is None and _DERIV_UNDERLYING_HDR.match(t):
                        name_col = i
                    elif qty_col is None and _DERIV_QTY_HDR.match(t):
                        qty_col = i
                    elif longshort_col is None and _DERIV_LONGSHORT_HDR.search(t):
                        longshort_col = i
                    elif mkt_col is None and _DERIV_MKT_HDR.search(t):
                        mkt_col = i
                    elif pct_col is None and _DERIV_PCT_HDR.search(t):
                        pct_col = i
                    elif industry_col is None and t.lower().startswith("industry"):
                        industry_col = i
                if qty_col is None:
                    qty_col = longshort_col
            continue

        name = _norm(_cell(row, name_col))
        qty = _cell(row, qty_col)
        mkt = _cell(row, mkt_col)
        # A real position reports both a size and a value. The trailing
        # summary line puts the fund's own name in the Underlying column
        # with a quantity but no market value.
        if not name or isinstance(qty, bool):
            continue
        if not isinstance(qty, (int, float)) or not isinstance(mkt, (int, float)):
            continue
        out.append(
            IntermediateRow(
                amc=amc,
                source_file=source_file,
                sheet=sheet_name,
                scheme_name_raw=scheme,
                # non_isin.py recognises Futures off the section header,
                # which is exactly what this table is.
                section_header="Futures",
                security_name=name,
                isin=None,
                industry_raw=_norm(_cell(row, industry_col)) or None,
                quantity=qty,
                market_value_raw=mkt,
                # Published by some AMCs, derived by convert.py from the
                # scheme's net assets when it is not.
                pct_raw=_cell(row, pct_col) if pct_col is not None else None,
                port_date=port_date,
            )
        )

    # The holdings table already sums to 100 on its own — it accounts for
    # the derivative exposure in one lump ("Net Current Assets includes
    # the adjustment amount for disclosures of derivatives at exposure
    # values"), and this sheet then itemises it. Adding the itemised legs
    # without that lump leaves the scheme at 100 minus the hedge: HDFC
    # Arbitrage came to 30.63%. ICRA carries the lump as a single
    # Undisclosed - Others row of exactly the opposite value, so restore
    # it here. The name is deliberately one nothing classifies, which is
    # what puts it in that bucket.
    if out:
        offset = -sum(_to_float(r.market_value_raw) or 0.0 for r in out)
        out.append(
            IntermediateRow(
                amc=amc,
                source_file=source_file,
                sheet=sheet_name,
                scheme_name_raw=scheme,
                section_header=None,
                security_name="Derivative Exposure Adjustment",
                isin=None,
                industry_raw=None,
                quantity=None,
                market_value_raw=offset,
                pct_raw=None,
                port_date=port_date,
            )
        )
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
    return _drop_duplicate_blocks(_drop_repeated_labels(out, amc))

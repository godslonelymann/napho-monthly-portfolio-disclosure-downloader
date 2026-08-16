"""Fuzzy but conservative month-period parsing."""

from __future__ import annotations

import html as html_module
import re
from datetime import date
from urllib.parse import unquote


MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
MONTH_ALIASES = {
    "jan": 1, "janaury": 1, "january": 1,
    "feb": 2, "febuary": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "apirl": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
MONTH_PATTERN = r"(?:" + "|".join(sorted(MONTH_ALIASES, key=len, reverse=True)) + r")"
DAY_PATTERN = r"(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?"


def month_name(period: str, *, abbreviated: bool = False) -> str:
    month = int(period[5:7])
    return MONTHS[month - 1][:3] if abbreviated else MONTHS[month - 1]


def _year(value: str) -> int:
    number = int(value)
    return 2000 + number if len(value) == 2 and number < 70 else 1900 + number if len(value) == 2 else number


# Each entry is (regex, formatter). formatter takes the match and returns
# the "YYYY-MM" string. Shared by extract_periods (unordered, deduplicated)
# and periods_in_order/last_period (ordered by where the date ends in the
# text) so both stay in sync with a single set of patterns.
# Every pattern's trailing year token is guarded by "(?![\dA-Za-z])", not the
# weaker "(?!\d)" its shape might suggest: a filename's trailing content-hash
# ID (Strapi/CDN uploads routinely append one, e.g. "..._2b1a0513aa.xlsx")
# is alphanumeric, and a bare 2-digit-year alternative like "(20\d{2}|\d{2})"
# will otherwise happily consume the hash's first two digits as "the year"
# since only a following *digit* was excluded, not a following *letter* --
# e.g. "old_bridge_..._may_26_20e8c1644b.xlsx" actually means May 2026
# ("may_26"), but without this guard "may_26_20" reads as day=26, year=' 20
# (2020), and that longer match wins under last_period()'s "last one found
# wins" rule because it ends later in the string than the correct one does.
_PERIOD_PATTERNS: tuple[tuple[re.Pattern[str], "Callable[[re.Match[str]], str]"], ...] = (
    # ISO and compact calendar dates.
    (
        re.compile(r"(?<!\d)(20\d{2})[-_/](0?[1-9]|1[0-2])(?:[-_/](?:0?[1-9]|[12]\d|3[01]))?(?![\dA-Za-z])"),
        lambda match: f"{match.group(1)}-{int(match.group(2)):02d}",
    ),
    (
        re.compile(r"(?<!\d)([0-3]?\d)[.\-/](0?[1-9]|1[0-2])[.\-/](20\d{2})(?![\dA-Za-z])"),
        lambda match: f"{match.group(3)}-{int(match.group(2)):02d}",
    ),
    (
        re.compile(r"(?<!\d)([0-3]\d)(0[1-9]|1[0-2])(20\d{2})(?![\dA-Za-z])"),
        lambda match: f"{match.group(3)}-{int(match.group(2)):02d}",
    ),
    # Common filename forms with a day between the month and year, or a day
    # before the month: July_31_2026 and 31-July-2026.
    (
        re.compile(rf"(?<![a-z])({MONTH_PATTERN})[\s._\-/,-]*{DAY_PATTERN}[\s._\-/,-]*(20\d{{2}}|\d{{2}})(?![\dA-Za-z])", re.I),
        lambda match: f"{_year(match.group(2)):04d}-{MONTH_ALIASES[match.group(1).lower()]:02d}",
    ),
    (
        re.compile(rf"(?<!\d){DAY_PATTERN}[\s._\-/,-]*({MONTH_PATTERN})[\s._\-/,-]*(20\d{{2}}|\d{{2}})(?![\dA-Za-z])", re.I),
        lambda match: f"{_year(match.group(2)):04d}-{MONTH_ALIASES[match.group(1).lower()]:02d}",
    ),
    # Month followed by year, with or without separators: July-2026, July2026,
    # July_26, and Jan-13Monthly-portfolio.
    (
        re.compile(rf"(?<![a-z])({MONTH_PATTERN})[\s._\-/]*(20\d{{2}}|\d{{2}})(?![\dA-Za-z])(?![\s._\-/]+20\d{{2}}(?!\d))", re.I),
        lambda match: f"{_year(match.group(2)):04d}-{MONTH_ALIASES[match.group(1).lower()]:02d}",
    ),
    # Year followed by month, e.g. 2026/July in an archive path.
    (
        re.compile(rf"(?<!\d)(20\d{{2}})[\s._\-/]*({MONTH_PATTERN})(?![a-z])", re.I),
        lambda match: f"{match.group(1)}-{MONTH_ALIASES[match.group(2).lower()]:02d}",
    ),
)


def _normalize(value: str) -> str:
    text = unquote(html_module.unescape(str(value))).replace("\\u002F", "/")
    # Strip URL query strings before matching.  Sites routinely append an
    # upload timestamp or cache-busting id after "?" (e.g. a Strapi/CMS
    # upload id, or "?sfvrsn=..."), and those digits can coincidentally look
    # like a DDMMYYYY date and be mistaken for the document's data period.
    text = re.sub(r"\?\S*", "", text)
    return text.lower()


def _period_matches(value: str) -> list[tuple[int, str]]:
    """Every (end_position, "YYYY-MM") found in ``value``, left to right.

    Position is the match's end index in the normalized (lowercased,
    query-stripped) text, so callers that want "the last date mentioned"
    can sort on it -- a filename that embeds both a scheme's own maturity
    date and its as-of date almost always states the as-of date last, e.g.
    "hsbc-crisil-ibx-gilt-june-2027-index-fund-31-jul-2026.xlsx".
    """
    lowered = _normalize(value)
    matches: list[tuple[int, str]] = []
    for pattern, formatter in _PERIOD_PATTERNS:
        for match in pattern.finditer(lowered):
            matches.append((match.end(), formatter(match)))
    matches.sort(key=lambda item: item[0])
    return matches


def extract_periods(value: str) -> set[str]:
    return {period for _, period in _period_matches(value)}


def periods_in_order(value: str) -> list[str]:
    """Every period mentioned in ``value``, in left-to-right order, duplicates kept."""
    return [period for _, period in _period_matches(value)]


def last_period(value: str, *, before: str | None = None) -> str | None:
    """The last period mentioned in ``value``, or ``None`` if it mentions none.

    When a string names more than one date -- a fund's own maturity date
    alongside its as-of date, or a publish-date folder alongside the
    filename's as-of date -- the as-of date is conventionally the one
    stated last. Pass ``before`` (a "YYYY-MM" string) to additionally
    discard any period later than it; an as-of date can never be in the
    future, so this filters out maturity dates like "...gilt-june-2027..."
    when they're the only date present and would otherwise win by default.
    """
    ordered = periods_in_order(value)
    if before is not None:
        ordered = [period for period in ordered if period <= before]
    return ordered[-1] if ordered else None


def current_period() -> str:
    """Today's "YYYY-MM". An as-of date can never be later than this."""
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def resolve_as_of_period(*sources: str, before: str | None = None) -> str | None:
    """The period a document is *about*, from several candidate text sources.

    A single document can have its as-of date stated in more than one place
    -- a filename, the link text pointing to it, the folder it's filed
    under -- and those places disagree surprisingly often: a scheme's own
    name can embed an unrelated date (a target-maturity fund named
    "...gilt-june-2027..."), and a folder can be named for the day a site
    *published* something rather than the month the data is *for* (HSBC
    files "document-08012025" holds December 2024's data).

    Pass sources ordered narrowest/most-reliable first -- typically
    filename, then link text, then folder/path. Each source is resolved
    independently with last_period() (see there for how a single source's
    ambiguity is settled), and the first source that yields anything wins;
    broader sources are only consulted when a narrower one has nothing
    usable at all, not to override it.

    `before` should normally be current_period(): the *real* current month,
    not just the period being searched for. Cutting off at the search
    period itself would still let a future scheme-maturity date win when
    nothing else in the source is dated, since the only thing being ruled
    out at that point is dates *after the search target* -- a maturity date
    that happens to fall before it would sail through uncaught.
    """
    for source in sources:
        if not source:
            continue
        resolved = last_period(source, before=before)
        if resolved:
            return resolved
    return None


def period_matches(value: str, period: str, *, month_end_only: bool = False) -> bool:
    periods = extract_periods(value)
    if period in periods:
        return True
    if periods:
        return False
    # Some APIs provide a month/year label without a date boundary.  The
    # parser above already handles that; this fallback only supports a clean
    # query-free month token supplied by a caller.
    return False


def period_conflicts(value: str, period: str) -> bool:
    periods = extract_periods(value)
    return bool(periods) and period not in periods

"""Step 2 — scheme name -> AMFI Code + Fund_Name. The main gap: without
this, convert() has no fund to attach a row to and drops it outright, so
this is what turns parsed rows into anything ICRA can use at all.

navall.txt already groups every scheme under its AMC's fund-house name,
which narrows the search from ~14,000 schemes to the ~50-300 one AMC
actually has. Within that group, ICRA wants Regular Plan + Growth Option,
which AMFI's own scheme-name text tells you directly ("... - Direct Plan
- Growth" vs "... - Regular Plan - Growth") — no separate scheme-master
file needed.

Matching is base-name-to-base-name: strip the plan/option suffix off each
navall.txt candidate ("SBI Banking & PSU Fund - Regular Plan - Growth" ->
"SBI Banking & PSU Fund"), normalize both sides, and compare. Anything
below the confidence threshold goes to a review file instead of a silent
wrong guess — a bad AMFI code is worse than a missing one, it's a return
you don't get to notice.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

NAVALL_ENCODING = "cp1252"

_CATEGORY_RE = re.compile(r"schemes\(", re.IGNORECASE)
_OPEN_CLOSE_RE = re.compile(r"^(open|close|interval)\s+ended", re.IGNORECASE)


def _norm_key(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s.replace("mutualfund", "")


def load_navall_by_house(
    navall_path: str | Path = "data/external/navall.txt",
) -> dict[str, list[dict]]:
    """Parse navall.txt's ragged structure: category header lines, blank
    lines, a bare fund-house name line, then ';'-delimited scheme rows —
    repeated for every category within that house."""
    houses: dict[str, list[dict]] = {}
    current_house: str | None = None

    with Path(navall_path).open(encoding=NAVALL_ENCODING) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if ";" in line:
                if current_house is None:
                    continue
                parts = line.split(";")
                if len(parts) < 4 or parts[0].strip().lower() == "scheme code":
                    continue
                code, isin_g, isin_d, name = parts[0], parts[1], parts[2], parts[3]
                houses.setdefault(current_house, []).append(
                    {
                        "code": code.strip(),
                        "isin_growth": isin_g.strip(),
                        "isin_div": isin_d.strip(),
                        "name": name.strip(),
                    }
                )
                continue
            if _CATEGORY_RE.search(line) or _OPEN_CLOSE_RE.match(line):
                continue
            # A bare line that's neither a category header nor a scheme
            # row is the fund-house name.
            current_house = line

    return houses


def map_amc_to_house(slug: str, houses: list[str]) -> str | None:
    sn = _norm_key(slug)
    house_norms = {_norm_key(h): h for h in houses}
    if sn in house_norms:
        return house_norms[sn]
    matches = [h for hn, h in house_norms.items() if hn and (hn in sn or sn in hn)]
    if not matches:
        return None
    return max(matches, key=len)


_GROWTH_RE = re.compile(r"\bgrowth\b", re.IGNORECASE)
_EXCLUDE_OPTION_RE = re.compile(r"idcw|dividend|bonus|payout|reinvest", re.IGNORECASE)
_DIRECT_RE = re.compile(r"\bdirect\b", re.IGNORECASE)
_OTHER_PLAN_RE = re.compile(r"\bretail\b|\binstitutional\b|\bsegregated\b", re.IGNORECASE)
_REGULAR_RE = re.compile(r"\bregular\b", re.IGNORECASE)
_ANY_OPTION_RE = re.compile(r"\bgrowth\b|\bidcw\b|\bdividend\b|\bbonus\b|\bpayout\b|\breinvest", re.IGNORECASE)
_PARENS_RE = re.compile(r"\([^)]*\)")


# The share-class vocabulary AMFI uses in the suffix, spelled out in full
# ("Payout of Income Distribution cum capital withdrawal option").
# Notably absent: "fund", which is what stops the trailing-run scan below
# from eating into the fund's own name.
_CLASS_WORDS = {
    "regular", "direct", "retail", "institutional", "segregated", "seg",
    "growth", "idcw", "dividend", "bonus", "plan", "option", "options",
    "payout", "payment", "reinvest", "reinvestment", "income",
    "distribution", "cum", "capital", "withdrawal", "unclaimed",
    "redemption", "monthly", "quarterly", "annual", "and", "of", "the",
    # AMFI's own typo, in SBI's rows. Cheaper to name than to leave a
    # whole class of suffixes un-strippable.
    "paln",
}
_WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9&]+")


def split_class_suffix(full_name: str) -> tuple[str, str]:
    """Split a scheme name into (fund name, share-class suffix).

    The suffix is the longest *trailing run* of words that are share-class
    vocabulary. Position-based truncation — cut at the first plan keyword
    anywhere — looks equivalent and is what this used to do, but it fails
    destructively whenever a plan word appears inside the fund's own name:
    "Nippon India Growth Mid Cap Fund-Growth Plan-Growth Option" cut at
    its first "Growth" leaves "Nippon India", a substring of every Nippon
    scheme, so match_scheme's containment rule scored one code 0.90
    against all of them and 39 of Nippon's 91 schemes mapped to Growth
    Mid Cap. Scanning from the right instead stops at the first word that
    belongs to the name ("Fund", "Savings", "Yield"), so a fund can be
    called Growth, Regular, or Dividend Yield without losing its identity.
    """
    s = _PARENS_RE.sub("", full_name)
    words = [w for w in _WORD_SPLIT_RE.split(s) if w]
    cut = len(words)
    while cut > 0 and words[cut - 1].lower() in _CLASS_WORDS:
        cut -= 1
    if cut == 0:  # nothing but class words; there is no name to keep
        return " ".join(words), ""
    return " ".join(words[:cut]), " ".join(words[cut:])


def base_scheme_name(full_name: str) -> str:
    return split_class_suffix(full_name)[0]


def is_regular_growth(scheme_name: str) -> bool:
    """Regular-plan + Growth-option, decided on the share-class suffix
    alone. Scanning the whole name reads the fund's own words as class
    markers: "DSP Regular Savings Fund - Direct Plan - Growth" counted as
    Regular because of the fund's name, so the Direct row was accepted as
    the Regular one (same for ICICI's and Aditya Birla's Regular Savings
    funds); and every "Dividend Yield Fund" was excluded outright as an
    IDCW class, leaving those schemes with no candidate at all."""
    suffix = split_class_suffix(scheme_name)[1]
    if not suffix:
        return False
    if _EXCLUDE_OPTION_RE.search(suffix):
        return False
    # A suffix naming a plan but no option at all ("ICICI Prudential
    # Children's Fund - Regular Plan", "Samco Mid Cap Fund - Regular
    # Plan") is a scheme with no Growth/IDCW split: that row *is* its
    # growth class. Requiring the literal word "Growth" left those
    # schemes with no candidate, so the builder fell back to whatever
    # else scored highest and merged them into a sibling fund.
    if not _GROWTH_RE.search(suffix) and _ANY_OPTION_RE.search(suffix):
        return False
    return bool(_REGULAR_RE.search(suffix)) or not (
        _DIRECT_RE.search(suffix) or _OTHER_PLAN_RE.search(suffix)
    )


def is_single_class(scheme_name: str) -> bool:
    """ETFs carry no Regular/Direct or Growth/IDCW split at all in
    navall.txt — just the bare name ("360 ONE Gold ETF") — so
    is_regular_growth's "growth" requirement excludes every ETF outright.
    Anything with zero plan/option markers is its own single class."""
    return not split_class_suffix(scheme_name)[1]


_MONTHS = r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?"
# Some AMCs (Zerodha) stamp the report period onto the scheme name itself
# ("... FOR JULY 2026"). It's real text in the file, so extract.py is
# right to capture it verbatim — but it must not reach the matcher: a
# bare year digit-gates out every candidate in match_scheme (a scheme
# name and its report-period year are unrelated numbers), silently
# rejecting an otherwise perfect match.
_REPORT_PERIOD_RE = re.compile(
    rf"\s+((for\s+the\s+period\s+ended|for|as\s+on|as\s+of)\s+)?({_MONTHS})\s*[\-,]?\s*(\d{{1,2}}\s*,\s*)?\d{{4}}\s*$",
    re.IGNORECASE,
)
# Some AMCs (Groww) prefix an internal fund code onto the scheme name
# ("IB01-Groww Large Cap Fund"). Its digits digit-gate out every real
# candidate the same way a report-period stamp does.
_LEADING_CODE_RE = re.compile(r"^[A-Z]{1,4}\d{1,4}[\-\s]+", re.IGNORECASE)


# Fixed-maturity ETF/FOF series (Bharat Bond) are permanently named after
# their target maturity year ("Bharat Bond ETF – April 2030") — that's
# not a report-period stamp, it's the one thing that tells apart four
# otherwise-identically-named schemes (April 2030/2031/2032/2033). The
# report-period regex above can't tell a fund's real trailing year from
# an incidental one — both are a bare month+year at the string's end —
# so stripping it here collapsed all four maturities to the same
# match_key and merged their holdings into a single scheme (200 rows
# read for one AMFI code that ICRA lists at 85, corpus at ~400% instead
# of 100%). Excluded by name rather than tightening the regex generally:
# Zerodha's own report-period stamp has the same bare-suffix shape with
# no "for"/"as on" qualifier to distinguish it, so the regex has to stay
# permissive for everyone else.
_FIXED_MATURITY_SERIES = ("bharat bond",)


def _strip_report_period(name: str) -> str:
    if any(kw in name.lower() for kw in _FIXED_MATURITY_SERIES):
        return name
    return _REPORT_PERIOD_RE.sub("", name)


def _match_norm(s: str) -> str:
    s = _LEADING_CODE_RE.sub("", s)
    s = _strip_report_period(s)
    s = s.upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class SchemeCandidate:
    amfi_code: str
    fund_name: str  # full AMFI scheme name (Regular-Growth), what ICRA's Fund_Name column expects
    base_norm: str


def build_candidates(house_rows: list[dict]) -> list[SchemeCandidate]:
    out = []
    for row in house_rows:
        if not (is_regular_growth(row["name"]) or is_single_class(row["name"])):
            continue
        base = base_scheme_name(row["name"])
        if not base:
            continue
        out.append(
            SchemeCandidate(amfi_code=row["code"], fund_name=row["name"], base_norm=_match_norm(base))
        )
    return out


@dataclass
class MatchResult:
    scheme_name_raw: str
    amfi_code: str | None
    fund_name: str | None
    score: float


_DIGITS_RE = re.compile(r"\d+")


def match_scheme(scheme_name_raw: str, candidates: list[SchemeCandidate]) -> MatchResult:
    target = _match_norm(scheme_name_raw)
    if not target or not candidates:
        return MatchResult(scheme_name_raw, None, None, 0.0)

    target_digits = _DIGITS_RE.findall(target)

    best: SchemeCandidate | None = None
    best_score = 0.0
    for cand in candidates:
        # Series/plan numbers (FMP 194 vs FMP 294, Index 2025 vs 2030...)
        # decide which specific fund this is — two schemes can otherwise
        # be textually near-identical apart from that number, and string
        # similarity alone will happily score the wrong one highest.
        # Reject outright rather than let a close text match paper over a
        # digit mismatch.
        if target_digits:
            cand_digits = _DIGITS_RE.findall(cand.base_norm)
            if not all(d in cand_digits for d in target_digits):
                continue

        if cand.base_norm == target:
            return MatchResult(scheme_name_raw, cand.amfi_code, cand.fund_name, 1.0)
        score = SequenceMatcher(None, target, cand.base_norm).ratio()
        if target in cand.base_norm or cand.base_norm in target:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = cand, score

    if best is None:
        return MatchResult(scheme_name_raw, None, None, 0.0)
    return MatchResult(scheme_name_raw, best.amfi_code, best.fund_name, best_score)


CONFIDENCE_THRESHOLD = 0.82

ICRA_CODES_PATH = "data/lookups/amfi_codes.csv"


def _icra_code_index(path: str | Path = ICRA_CODES_PATH) -> dict[str, tuple[str, str]]:
    """ICRA's accepted codes keyed by the normalized fund name.

    ICRA identifies a scheme, not a share class, and its choice of code
    frequently is not AMFI's Regular-Growth one: it lists SBI Consumption
    Opportunities under 100645 (AMFI's IDCW row) and Quantum's funds
    under their Direct-plan codes, while the Regular-Growth codes those
    schemes do have are absent from ICRA's list entirely. A mapping built
    purely from navall.txt is therefore correct about the fund and still
    unjoinable, so the AMFI match below is translated through this index
    before it is written.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("AMFI Code") or "").strip()
            name = (row.get("Fund_Name") or "").strip()
            if code and code != "--" and name:
                index.setdefault(_match_norm(base_scheme_name(name)), []).append((code, name))
    # Ambiguous names are left out rather than guessed between.
    return {k: v[0] for k, v in index.items() if len(v) == 1}


def to_icra(result: MatchResult, icra_index: dict[str, tuple[str, str]]) -> MatchResult:
    if result.fund_name is None:
        return result
    hit = icra_index.get(_match_norm(base_scheme_name(result.fund_name)))
    if hit is None:
        return result
    return MatchResult(result.scheme_name_raw, hit[0], hit[1], result.score)


def resolve_amc_schemes(
    amc: str,
    scheme_names: set[str],
    *,
    navall_path: str | Path = "data/external/navall.txt",
    threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[list[MatchResult], list[MatchResult], str | None]:
    """Returns (matched, needs_review, house_name)."""
    houses = load_navall_by_house(navall_path)
    house = map_amc_to_house(amc, list(houses.keys()))
    if house is None:
        return [], [MatchResult(s, None, None, 0.0) for s in sorted(scheme_names)], None

    candidates = build_candidates(houses[house])
    icra_index = _icra_code_index()
    matched, review = [], []
    for name in sorted(scheme_names):
        result = to_icra(match_scheme(name, candidates), icra_index)
        if result.amfi_code is not None and result.score >= threshold:
            matched.append(result)
        else:
            review.append(result)
    return matched, review, house


def write_schemes_csv(matched: list[MatchResult], out_path: str | Path) -> int:
    """Keyed by match_key (_match_norm of scheme_name_raw), not the raw
    text — the same scheme's name varies across periods (Zerodha stamps
    "FOR JULY 2026" onto it, Groww prefixes an internal code), and
    convert.py looks rows up by that normalized key so a mapping built
    from one period's files still matches another's. sheet_name is kept
    only as an audit trail of what was actually seen; duplicate match_keys
    across periods collapse to the highest-scoring one."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    best_by_key: dict[str, MatchResult] = {}
    for r in matched:
        key = _match_norm(r.scheme_name_raw)
        if key not in best_by_key or r.score > best_by_key[key].score:
            best_by_key[key] = r
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["match_key", "sheet_name", "amfi_code", "fund_name", "match_score"])
        for key, r in sorted(best_by_key.items()):
            writer.writerow([key, r.scheme_name_raw, r.amfi_code, r.fund_name, f"{r.score:.3f}"])
    return len(best_by_key)


def write_review_csv(review: list[MatchResult], out_path: str | Path) -> int:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sheet_name", "best_guess_amfi_code", "best_guess_fund_name", "score"])
        for r in review:
            writer.writerow([r.scheme_name_raw, r.amfi_code or "", r.fund_name or "", f"{r.score:.3f}"])
    return len(review)


# AMCs with a hand-written parser in pipeline/amcs/ must be surveyed with
# *that* parser, not the generic extractor — convert.py joins on
# scheme_name_raw, and whichever parser actually runs at conversion time
# is the one whose keys this mapping has to match. Using the generic
# extractor here for an AMC that runs its own parser in production
# silently builds a schemes.csv full of the wrong keys (every row comes
# out "unmapped").
def _parse_period_for_amc(amc: str, period_dir: Path):
    if amc == "360_one":
        from pipeline.amcs.three_sixty_one import parse_period as p

        return p(period_dir)
    if amc == "tata":
        from pipeline.amcs.tata import parse_period as p

        return p(period_dir)
    if amc == "pgim":
        from pipeline.amcs.pgim import parse_period as p

        return p(period_dir)
    from pipeline.extract import parse_period as p

    return p(period_dir, amc=amc)


def _distinct_scheme_names(amc: str, raw_dir: Path, max_periods_tried: int = 6) -> tuple[set[str], str | None]:
    """scheme_name_raw values for one AMC, unioned across its most recent
    periods (a single period can miss a scheme that was added/dropped, or
    fail to parse outright)."""
    amc_dir = raw_dir / amc
    if not amc_dir.is_dir():
        return set(), None
    periods = sorted((p.name for p in amc_dir.iterdir() if p.is_dir()), reverse=True)

    names: set[str] = set()
    tried = 0
    last_period = None
    for period in periods:
        if tried >= max_periods_tried:
            break
        try:
            rows = _parse_period_for_amc(amc, amc_dir / period)
        except Exception:
            continue
        tried += 1
        if rows:
            last_period = last_period or period
            names |= {r.scheme_name_raw for r in rows}
    return names, last_period


def build_all(
    raw_dir: str | Path = "data/raw",
    mappings_dir: str | Path = "data/mappings",
    navall_path: str | Path = "data/external/navall.txt",
    *,
    amcs: list[str] | None = None,
) -> dict[str, dict]:
    raw_dir, mappings_dir = Path(raw_dir), Path(mappings_dir)
    amc_list = amcs or sorted(p.name for p in raw_dir.iterdir() if p.is_dir())

    stats: dict[str, dict] = {}
    for amc in amc_list:
        names, period_used = _distinct_scheme_names(amc, raw_dir)
        if not names:
            stats[amc] = {"house": None, "schemes_seen": 0, "matched": 0, "review": 0, "period": None}
            continue
        matched, review, house = resolve_amc_schemes(amc, names, navall_path=navall_path)
        write_schemes_csv(matched, mappings_dir / amc / "schemes.csv")
        if review:
            write_review_csv(review, mappings_dir / amc / "schemes_review.csv")
        stats[amc] = {
            "house": house,
            "schemes_seen": len(names),
            "matched": len(matched),
            "review": len(review),
            "period": period_used,
        }
    return stats


if __name__ == "__main__":
    import sys

    amcs = sys.argv[1:] or None
    stats = build_all(amcs=amcs)
    total_seen = total_matched = 0
    for amc, s in stats.items():
        total_seen += s["schemes_seen"]
        total_matched += s["matched"]
        print(f"{amc:35s} house={s['house'] or '?':30s} seen={s['schemes_seen']:4d} matched={s['matched']:4d} review={s['review']:4d} period={s['period']}")
    print(f"\nTOTAL: {total_matched}/{total_seen} schemes matched ({100*total_matched/max(total_seen,1):.1f}%)")

"""Audit data/mappings/*/schemes.csv against the code list ICRA accepts.

A wrong AMFI code is the highest-cost error in this pipeline and the
quietest: convert.py happily attaches an AMC's holdings to whatever code
the mapping names, so the run still "succeeds" and the rows land under
some other fund. Every instance found by hand so far has been one of four
shapes, and all four are mechanically detectable — which is the point of
this module: find every remaining one in a single pass rather than one
AMC at a time.

  missing_code   the code is in no AMFI list at all (a typo)
  wrong_scheme   the code exists but names a different scheme than the
                 mapping's own match_key describes (Gilt->Gold,
                 Nifty 50->Nifty Next 50, Index Fund->sibling ETF)
  duplicate_code two match_keys in one AMC share a code, so one scheme's
                 holdings silently merge into the other's
  not_in_icra    the code is a real AMFI code but not one ICRA lists, so
                 the row cannot match no matter how correct it looks
  stale_name     code is right but the cached fund_name column drifted

Two authorities, deliberately: data/lookups/amfi_codes.csv is ICRA's own
list (its 2089 codes are exactly the distinct codes in ICRA_Sample.xlsx),
and it decides whether a code is *acceptable*; navall.txt decides what
scheme and share class a code actually names, which is what makes a
report readable ("this is the Direct plan") rather than a bare rejection.
Judging acceptability by navall instead gets real cases backwards: AMFI
calls 153872 the Direct class of The Wealth Company Flexi Cap Fund, but
153872 is the code ICRA lists for that fund and 153870 is absent from
ICRA entirely.

Read-only: it reports, it does not rewrite mappings. --fix applies only
the suggestions that are unambiguous (see apply_fixes).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path

from pipeline.schemes import load_navall_by_house

# Below this, the code's own AMFI name and the mapping's match_key are
# describing different funds. Deliberately well under the builder's 0.82
# match threshold: this is looking for codes that are plainly wrong, not
# re-litigating every borderline match the builder already accepted.
SIMILARITY_FLOOR = 0.72


@dataclass
class Issue:
    amc: str
    kind: str
    match_key: str
    amfi_code: str
    detail: str
    suggested_code: str = ""
    suggested_name: str = ""
    suggested_score: float = 0.0
    # Set when the suggestion differs from the current mapping only in
    # share-class wording — same fund, every significant word matching.
    exact_rename: bool = False

    @property
    def fixable(self) -> bool:
        if self.kind == "stale_name":
            return True  # refreshing a name cannot attach rows to another fund
        return bool(self.suggested_code) and self.exact_rename


# The mapping's match_key and AMFI's own scheme name describe the same
# fund in different registers: AMCs append the SEBI product blurb ("... AN
# OPEN ENDED SCHEME INVESTING IN ..."), rename history ("ERSTWHILE KNOWN
# AS ..."), and spell "&" out. Raw string similarity reads all three as
# disagreement, which is what made the first pass flag 102 schemes that
# were correctly mapped. Strip them before comparing, so a low score means
# "different fund" rather than "different house style".
_BLURB_RE = re.compile(
    r"\b(AN?\s+OPEN\s+ENDED|AN?\s+CLOSE[D]?\s+ENDED|ERSTWHILE|FORMERLY|SCHEME\s+HAS|THE\s+SCHEME)\b.*$"
)
_FILLER = {"FUND", "SCHEME", "PLAN", "OPTION", "REGULAR", "GROWTH", "THE", "OF", "AN", "A", "AND"}


def _cmp_norm(s: str) -> str:
    s = s.upper().replace("&", " AND ")
    s = _BLURB_RE.sub("", s)
    # "Nifty50" / "Smallcap250" / "Midcap150" are the same name as "Nifty
    # 50" / "Smallcap 250" / "Midcap 150"; AMCs and AMFI disagree on the
    # space freely. Split the boundary so the number is its own token on
    # both sides, or the digit rule below reads a formatting difference
    # as two different indices.
    s = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> set[str]:
    return {t for t in _cmp_norm(s).split() if t not in _FILLER}


def _sim(a: str, b: str) -> float:
    """Similarity on the *comparable* part of two scheme names. Token
    containment is checked alongside the character ratio because the two
    sides differ mostly by length of boilerplate, not by content."""
    if not a or not b:
        return 0.0
    ca, cb = _cmp_norm(a), _cmp_norm(b)
    if ca == cb:
        return 1.0
    score = SequenceMatcher(None, ca, cb).ratio()
    if ca in cb or cb in ca:
        score = max(score, 0.95)
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and (ta <= tb or tb <= ta):
        score = max(score, 0.95)
    if ta and tb:
        # Numbers in a scheme name (Nifty 50 vs Nifty 500, Series 194 vs
        # 294) are identity, not detail: disagreement there is decisive
        # however similar the surrounding words are.
        na = {t for t in ta if t.isdigit()}
        nb = {t for t in tb if t.isdigit()}
        # A conflict, not merely a difference: one side listing extra
        # numbers is normal (a match_key drops the house's own "360 ONE"
        # prefix, so {50} meets {360, 50}). Only genuinely incompatible
        # sets — Series 194 against 294, Nifty 50 against Nifty 500 —
        # settle the question.
        if na and nb and not (na <= nb or nb <= na):
            return min(score, 0.5)
        score = max(score, len(ta & tb) / len(ta | tb))
    return score


def load_amfi_codes(path: str | Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("AMFI Code") or "").strip()
            if code and code != "--":
                out[code] = (row.get("Fund_Name") or "").strip()
    return out


def _icra_name_index(amfi_codes: dict[str, str]) -> dict[frozenset[str], tuple[str, str]]:
    """ICRA's names keyed by significant-token set, so a mapping's
    match_key can be matched against what ICRA actually calls the fund.
    Ambiguous token sets are dropped rather than guessed between."""
    index: dict[frozenset[str], list[tuple[str, str]]] = defaultdict(list)
    for code, name in amfi_codes.items():
        index[frozenset(_tokens(name))].append((code, name))
    return {k: v[0] for k, v in index.items() if len(v) == 1 and k}


def _navall_code_names(navall_path: str | Path) -> dict[str, str]:
    """code -> AMFI's own scheme name, over every navall row. Used only
    to explain a code ICRA doesn't list ("AMFI names it ... Direct
    Plan"), never to judge one."""
    return {
        r["code"]: r["name"]
        for rows in load_navall_by_house(navall_path).values()
        for r in rows
    }


def audit_amc(
    amc: str,
    schemes_csv: Path,
    amfi_codes: dict[str, str],
    code_name: dict[str, str],
    icra_by_tokens: dict[frozenset[str], tuple[str, str]],
) -> list[Issue]:
    with schemes_csv.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []

    issues: list[Issue] = []
    by_code: dict[str, list[str]] = defaultdict(list)
    suggestion_for: dict[str, Issue] = {}

    def row_key(r: dict) -> str:
        # Two header shapes are in use: most AMCs have match_key, the two
        # Bandhan mappings only carry sheet_name. Either is the name side
        # of the mapping, which is all this audit needs.
        return (r.get("match_key") or r.get("sheet_name") or "").strip()

    for r in rows:
        by_code[(r.get("amfi_code") or "").strip()].append(row_key(r))

    for r in rows:
        key = row_key(r)
        code = (r.get("amfi_code") or "").strip()
        cached_name = (r.get("fund_name") or "").strip()

        icra_hit = icra_by_tokens.get(frozenset(_tokens(key)))
        s_code, s_name = icra_hit if icra_hit and icra_hit[0] != code else ("", "")

        def mk(kind: str, detail: str) -> Issue:
            return Issue(amc, kind, key, code, detail, s_code, s_name,
                         1.0 if s_code else 0.0, bool(s_code))

        suggestion_for[key] = mk("", "")

        if not code:
            issues.append(mk("missing_code", "row has no amfi_code"))
            continue

        icra_name = amfi_codes.get(code, "")
        if not icra_name:
            # navall explains *what* the code is, which turns "unusable"
            # into something actionable; ICRA still decides that it is.
            amfi_name = code_name.get(code)
            detail = (f"not in ICRA's code list; AMFI names it {amfi_name!r}"
                      if amfi_name else "code appears in no AMFI or ICRA list")
            issues.append(mk("not_in_icra", detail))
            continue

        score = _sim(key, icra_name)
        if score < SIMILARITY_FLOOR:
            issues.append(mk("wrong_scheme", f"ICRA lists this code as {icra_name!r} (similarity {score:.2f})"))
            continue

        if cached_name != icra_name:
            issues.append(mk("stale_name", f"cached {cached_name!r} != ICRA {icra_name!r}"))

    for code, keys in by_code.items():
        if not code or len(keys) < 2:
            continue
        # Sharing a code is normal and correct when the keys are just the
        # same scheme spelled differently across periods ("OVERNIGHT" /
        # "OVERNIGHT FUND"). It is a silent holdings merge only when the
        # keys name genuinely different schemes.
        if all(_sim(a, b) >= 0.9 for a in keys for b in keys):
            continue
        for key in keys:
            sugg = suggestion_for.get(key)
            issues.append(
                Issue(amc, "duplicate_code", key, code,
                      f"shared with: {', '.join(k[:40] for k in keys if k != key)[:150]}",
                      *((sugg.suggested_code, sugg.suggested_name, sugg.suggested_score,
                         sugg.exact_rename) if sugg else ("", "", 0.0, False)))
            )

    return issues


def audit_all(
    mappings_dir: str | Path = "data/mappings",
    amfi_codes_path: str | Path = "data/lookups/amfi_codes.csv",
    navall_path: str | Path = "data/external/navall.txt",
    *,
    amcs: list[str] | None = None,
) -> list[Issue]:
    mappings_dir = Path(mappings_dir)
    amfi_codes = load_amfi_codes(amfi_codes_path)
    code_name = _navall_code_names(navall_path)

    targets = sorted(
        p for p in mappings_dir.iterdir()
        if p.is_dir() and (p / "schemes.csv").exists() and (not amcs or p.name in amcs)
    )
    icra_by_tokens = _icra_name_index(amfi_codes)
    issues: list[Issue] = []
    for d in targets:
        issues.extend(audit_amc(d.name, d / "schemes.csv", amfi_codes, code_name, icra_by_tokens))
    return issues


def apply_fixes(issues: list[Issue], mappings_dir: str | Path = "data/mappings") -> int:
    """Rewrite only rows whose suggestion is an exact rename — every
    significant word of the match_key matching ICRA's own name for the
    replacement code. Character similarity is not enough on its own: it
    rates "Nifty Bank Index" against "Nifty 50 Index", and "Silver ETF
    Fund of Fund" against the plain "Silver ETF", above 0.90."""
    fixable_kinds = {"missing_code", "wrong_scheme", "stale_name",
                     "duplicate_code", "not_in_icra"}
    by_amc: dict[str, dict[str, Issue]] = defaultdict(dict)
    for i in issues:
        if i.kind in fixable_kinds and i.fixable:
            by_amc[i.amc][i.match_key] = i

    changed = 0
    for amc, keyed in by_amc.items():
        path = Path(mappings_dir) / amc / "schemes.csv"
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            rows = list(reader)
        for r in rows:
            issue = keyed.get((r.get("match_key") or r.get("sheet_name") or "").strip())
            if not issue:
                continue
            if issue.kind == "stale_name":
                r["fund_name"] = issue.detail.split("!= ICRA ")[-1].strip().strip("'\"")
            else:
                r["amfi_code"] = issue.suggested_code
                r["fund_name"] = issue.suggested_name
                r["match_score"] = f"{issue.suggested_score:.3f}"
            changed += 1
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("amcs", nargs="*", help="limit to these AMC slugs")
    ap.add_argument("--fix", action="store_true", help="apply unambiguous suggestions")
    ap.add_argument("--json", dest="json_out", help="write full issue list here")
    args = ap.parse_args()

    issues = audit_all(amcs=args.amcs or None)

    by_kind: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        by_kind[i.kind].append(i)

    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        group = by_kind[kind]
        print(f"\n=== {kind}  ({len(group)}) ===")
        for i in sorted(group, key=lambda x: (x.amc, x.match_key)):
            mark = "FIX" if i.fixable else "   "
            sugg = (f"  {mark}-> {i.suggested_code} {i.suggested_name}"
                    if i.suggested_code else "")
            print(f"  {i.amc:22s} {i.match_key[:48]:48s} {i.amfi_code:>8s}  {i.detail}{sugg}")

    fixable = sum(1 for i in issues if i.fixable)
    print(f"\nTOTAL {len(issues)} issues across {len({i.amc for i in issues})} AMCs; {fixable} auto-fixable")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps([asdict(i) for i in issues], indent=2))

    if args.fix:
        print(f"applied {apply_fixes(issues)} fixes")


if __name__ == "__main__":
    main()

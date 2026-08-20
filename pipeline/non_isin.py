"""Step 4 — the closed set of instruments that carry no ISIN and are
*supposed to* stay that way: 10.4% of ICRA rows (13,309 in the May-2026
sample), 18 distinct Instrument_Name values. These must never land in the
review file just because they have no ISIN — that's their normal shape.

One central table, built once from data/lookups/instrument_types.csv
(itself extracted from the ICRA seed — see pipeline/lookups.py) plus the
recognition hints in ICRA_CONVERSION_PLAN.md's step-4 table. A per-AMC
override file (data/mappings/<amc>/non_isin_instruments.csv) is checked
first, for the stragglers a single central list can't cover — most AMCs
need no override at all.

For every row this module classifies, Security_Name is the name already
printed on the row (`Net Receivables`, `TREPS`, `Gold`) — nothing to look
up, it was captured in step 1.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

# Ordered so the most specific / highest-volume patterns are tried first
# (Futures + Money Market alone are 70% of the 18-row set). Each rule
# matches on the row's own security_name and/or the section header it was
# read under, whichever the ICRA_CONVERSION_PLAN "recognise by" column
# says is reliable for that instrument.
_CENTRAL_RULES: list[dict] = [
    {"instrument": "Futures", "nature": "EQ", "section_kw": ["future"], "exclude_kw": ["commodity"]},
    {"instrument": "Money Market", "nature": "Others", "section_kw": ["money market"]},
    {"instrument": "Net Receivables/(Payables)", "nature": "Others", "name_kw": ["net receivable", "net payable", "net current asset"]},
    {"instrument": "IRS", "nature": "Debt", "section_kw": ["irs", "interest rate swap"]},
    {"instrument": "Margin Deposit", "nature": "Debt", "name_kw": ["margin deposit"]},
    # "Clearing Corporation of India Ltd" is TREPS/repo's counterparty,
    # printed as the security_name itself by AMCs (PGIM among them)
    # instead of the word "TREPS" — without this it falls through to the
    # name->ISIN fallback, which resolves to a placeholder code
    # ("INTREP020226") that a data-quality gap in the harvested
    # isin_names.csv let through as if it were real (see
    # data/lookups/contract_codes.csv, which correctly flags dozens of
    # other AMCs' shape-valid-but-checksum-invalid CCIL placeholders as
    # contracts, not ISINs — this one just happened to pass the checksum
    # too). JM Financial and The Wealth Company print the bare acronym
    # "CCIL" instead of the spelled-out name — same fallback-to-a-fake-
    # ISIN failure, just missed by the "clearing corporation of india"
    # phrase match (ICRA's own row for this line carries no ISIN either).
    {"instrument": "Reverse repo", "nature": "Others", "name_kw": ["reverse repo", "treps", "tri-party", "triparty", "clearing corporation of india", "ccil"]},
    {"instrument": "Options", "nature": "EQ", "section_kw": ["option"]},
    {"instrument": "Repo", "nature": "Others", "name_kw": ["repo"]},
    {"instrument": "Current Assets", "nature": "Others", "name_kw": ["current asset"]},
    {"instrument": "Cash", "nature": "Others", "name_kw": ["cash"]},
    {"instrument": "Gold", "nature": "Others", "name_kw": ["gold"]},
    {"instrument": "Silver", "nature": "Others", "name_kw": ["silver"]},
    {"instrument": "Commodity Futures", "nature": "Others", "section_kw": ["commodity future"]},
    {"instrument": "MFEquity", "nature": "EQ", "name_kw": ["mutual fund unit", "mf unit"]},
    {"instrument": "FD", "nature": "Debt", "name_kw": ["fixed deposit"]},
    {"instrument": "Preference Shares", "nature": "EQ", "section_kw": ["preference share"], "name_kw": ["preference share"]},
    {"instrument": "Equity", "nature": "EQ", "section_kw": ["unlisted", "awaiting listing"]},
]

FALLBACK = {"Instrument_Name": "Undisclosed - Others", "Nature_Name": "Others"}

# Instruments whose recognition is trusted over a *present* ISIN cell.
# Extends convert.py's existing name-based set with the derivatives,
# which are recognised by section rather than by name.
#
# Why derivatives belong here: ICRA records Futures 0-with-ISIN /
# 7737-without, Options 0/91, Commodity Futures 0/18 — no exceptions.
# AMCs nonetheless print the *underlying's* ISIN on a futures line
# ("Infosys Ltd.-JUN2026" carrying INE009A01021), so the row reads as an
# ordinary equity holding. That is how Kotak Flexicap came out with two
# Infosys equity rows where ICRA has one Equity row and one Futures row
# with a blank ISIN. A section headed "Futures" or "Options" contains
# nothing else, so trusting it costs nothing.
#
# Why the rest of ICRA's blank-ISIN instruments are NOT here, even though
# they are just as consistently blank in ICRA's own data: the risk is
# misrecognition, not misclassification. "Money Market" is matched on the
# section header, and a "Money Market Instruments" section is full of
# CDs, CPs and T-Bills that all carry real ISINs (3972 + 2354 + 1362 rows
# in ICRA) — clearing those would be catastrophic. "Cash" is matched on
# the substring "cash", which also matches the ISIN-bearing "Cash
# Management Bill". Those instruments still classify correctly when the
# row genuinely has no ISIN; they simply do not get to overrule one.
ISIN_OVERRIDE_SAFE = frozenset({
    "Futures",
    "Options",
    "Commodity Futures",
    "Net Receivables/(Payables)",
    "Reverse repo",
    "Margin Deposit",
})

def _matches(rule: dict, security_name: str, section_header: str) -> bool:
    name_l = (security_name or "").lower()
    section_l = (section_header or "").lower()
    for kw in rule.get("exclude_kw", ()):
        # Section only. Matching the security name too meant Futures'
        # "commodity" exclusion — there to hand commodity futures to
        # their own rule below — fired on any *company* with the word in
        # its name, so a Multi Commodity Exchange of India future fell
        # through to no classification at all.
        if kw in section_l:
            return False
    for kw in rule.get("name_kw", ()):
        if kw in name_l:
            return True
    for kw in rule.get("section_kw", ()):
        if kw in section_l:
            return True
    return False


@dataclass
class NonIsinRule:
    match_type: str  # "exact" | "prefix"
    pattern: str
    instrument: str
    nature: str


@dataclass
class NonIsinRules:
    overrides: list[NonIsinRule]

    def recognize(
        self, security_name: str, section_header: str | None = None
    ) -> tuple[str, str] | None:
        """The instrument this row names, or None if it matches no rule.

        classify() cannot answer this: it substitutes FALLBACK for "no
        match", which is the right output for a row already known to be a
        holding but useless for deciding *whether* a row is one. That
        distinction is what lets a no-ISIN row be judged on content —
        a real holding without an ISIN is one of these instruments, and
        anything else is a section header, a subtotal, or a stray table.
        """
        section_header = section_header or ""
        for rule in self.overrides:
            if rule.match_type == "exact" and security_name == rule.pattern:
                return rule.instrument, rule.nature
            if rule.match_type == "prefix" and security_name.startswith(rule.pattern):
                return rule.instrument, rule.nature
        for rule in _CENTRAL_RULES:
            if _matches(rule, security_name, section_header):
                return rule["instrument"], rule["nature"]
        return None

    def classify(self, security_name: str, section_header: str | None = None) -> tuple[str, str]:
        hit = self.recognize(security_name, section_header)
        return hit or (FALLBACK["Instrument_Name"], FALLBACK["Nature_Name"])


def load_non_isin_rules(amc: str, mappings_dir: str | Path = "data/mappings") -> NonIsinRules:
    overrides: list[NonIsinRule] = []
    path = Path(mappings_dir) / amc / "non_isin_instruments.csv"
    if path.exists():
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                overrides.append(
                    NonIsinRule(
                        match_type=row["match_type"],
                        pattern=row["pattern"],
                        instrument=row["Instrument_Name"],
                        nature=row["Nature_Name"],
                    )
                )
    return NonIsinRules(overrides=overrides)

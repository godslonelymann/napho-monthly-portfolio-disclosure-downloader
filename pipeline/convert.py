"""Step 3 — the one shared converter: intermediate rows -> the ICRA columns.

Handles it all in one place: scheme name -> AMFI Code + Fund_Name, ISIN ->
the four industry-classification columns, non-ISIN rows (TREPS, Gold,
Silver, Net Receivables, commodity futures, ...) -> Instrument_Name via a
per-AMC reviewed mapping, lakhs -> crores, fraction -> 0-100 percent scale,
empty quantity -> "NULL".

ICRA's Port_Date column is deliberately not emitted — see
pipeline/schema.FINAL_FIELDS for why, and for the one consequence.

A row with no scheme mapping is dropped — with no AMFI Code there's no
fund for it to belong to, so it can't be written. Everything else is kept:
an ISIN that misses the classification lookup is first tried against
data/lookups/isin_aliases.csv (market-wide ISIN changes — demergers,
re-issues — that ICRA's sample hasn't caught up to yet), and failing that,
written anyway with the FALLBACK_CLASSIFICATION ("Undisclosed - Others",
ICRA's own vocabulary for exactly this). Rows that fall back are always
reported separately (report.tagged_isin / report.tagged_non_isin) so a
human can look at them — they make a scheme's row count and Corpus_Per
reconcile, but they are not a substitute for a real classification.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.schema import IntermediateRow


# ICRA's own bucket for holdings it can't otherwise classify (243 rows in
# the May 2026 sample, all with a blank ISIN — see data/lookups/instrument_types.csv).
FALLBACK_CLASSIFICATION = {
    "Instrument_Name": "Undisclosed - Others",
    "Nature_Name": "Others",
    "Basic_Industry": "Miscellaneous",
    "Industry": "Miscellaneous",
    "Sector_Name": "Miscellaneous",
    "Macro_Economic_Sector": "Miscellaneous",
}


@dataclass
class Lookups:
    amfi_names: dict[str, str]
    isin_classification: dict[str, dict]  # ISIN -> {Instrument_Name, Nature_Name, 4 tiers}
    isin_aliases: dict[str, str]  # ISIN as it appears in the AMC file -> ISIN as it appears in ICRA


def load_lookups(lookups_dir: str | Path = "data/lookups") -> Lookups:
    lookups_dir = Path(lookups_dir)
    amfi_names: dict[str, str] = {}
    with (lookups_dir / "amfi_codes.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            amfi_names[row["AMFI Code"]] = row["Fund_Name"]

    isin_classification: dict[str, dict] = {}
    with (lookups_dir / "isin_classification.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            isin_classification[row["ISIN"]] = row

    isin_aliases: dict[str, str] = {}
    aliases_path = lookups_dir / "isin_aliases.csv"
    if aliases_path.exists():
        with aliases_path.open(newline="") as f:
            for row in csv.DictReader(f):
                isin_aliases[row["isin_in_amc_file"]] = row["isin_in_icra"]

    return Lookups(amfi_names=amfi_names, isin_classification=isin_classification, isin_aliases=isin_aliases)


@dataclass
class SchemeMapping:
    amfi_code: str
    fund_name: str


@dataclass
class NonIsinRule:
    match_type: str  # "exact" | "prefix"
    pattern: str
    classification: dict  # Instrument_Name, Nature_Name, 4 tiers


@dataclass
class AmcMapping:
    schemes: dict[str, SchemeMapping]  # sheet_name -> mapping
    non_isin_rules: list[NonIsinRule]

    def resolve_non_isin(self, security_name: str) -> dict | None:
        for rule in self.non_isin_rules:
            if rule.match_type == "exact" and security_name == rule.pattern:
                return rule.classification
            if rule.match_type == "prefix" and security_name.startswith(rule.pattern):
                return rule.classification
        return None


def load_amc_mapping(amc: str, mappings_dir: str | Path = "data/mappings") -> AmcMapping:
    amc_dir = Path(mappings_dir) / amc

    schemes: dict[str, SchemeMapping] = {}
    with (amc_dir / "schemes.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            schemes[row["sheet_name"]] = SchemeMapping(
                amfi_code=row["amfi_code"], fund_name=row["fund_name"]
            )

    non_isin_rules: list[NonIsinRule] = []
    non_isin_path = amc_dir / "non_isin_instruments.csv"
    if non_isin_path.exists():
        with non_isin_path.open(newline="") as f:
            for row in csv.DictReader(f):
                non_isin_rules.append(
                    NonIsinRule(
                        match_type=row["match_type"],
                        pattern=row["pattern"],
                        classification={
                            "Instrument_Name": row["Instrument_Name"],
                            "Nature_Name": row["Nature_Name"],
                            "Basic_Industry": row["Basic_Industry"],
                            "Industry": row["Industry"],
                            "Sector_Name": row["Sector_Name"],
                            "Macro_Economic_Sector": row["Macro_Economic_Sector"],
                        },
                    )
                )

    return AmcMapping(schemes=schemes, non_isin_rules=non_isin_rules)


@dataclass
class ConvertReport:
    total: int = 0
    converted: int = 0
    unmapped_schemes: list[IntermediateRow] = field(default_factory=list)
    # Written to the output, but with FALLBACK_CLASSIFICATION rather than a
    # real one — kept here so a human can find and resolve them.
    tagged_isin: list[IntermediateRow] = field(default_factory=list)
    tagged_non_isin: list[IntermediateRow] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.unmapped_schemes

    def has_tagged(self) -> bool:
        return bool(self.tagged_isin or self.tagged_non_isin)


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def convert(
    rows: list[IntermediateRow],
    *,
    lookups: Lookups,
    amc_mapping: AmcMapping,
) -> tuple[list[dict], ConvertReport]:
    report = ConvertReport(total=len(rows))
    out: list[dict] = []

    for row in rows:
        scheme = amc_mapping.schemes.get(row.scheme_name_raw)
        if scheme is None:
            report.unmapped_schemes.append(row)
            continue

        output_isin = row.isin or ""
        classification: dict | None
        if row.isin:
            classification = lookups.isin_classification.get(row.isin)
            if classification is None:
                alias_isin = lookups.isin_aliases.get(row.isin)
                if alias_isin is not None:
                    classification = lookups.isin_classification.get(alias_isin)
                    if classification is not None:
                        output_isin = alias_isin
            if classification is None:
                classification = FALLBACK_CLASSIFICATION
                report.tagged_isin.append(row)
        else:
            classification = amc_mapping.resolve_non_isin(row.security_name)
            if classification is None:
                classification = FALLBACK_CLASSIFICATION
                report.tagged_non_isin.append(row)

        mkt_value_lacs = _num(row.market_value_raw)
        pct_fraction = _num(row.pct_raw)
        quantity = _num(row.quantity)

        out.append(
            {
                "AMFI Code": scheme.amfi_code,
                "ISIN": output_isin,
                "Instrument_Name": classification["Instrument_Name"],
                "Nature_Name": classification["Nature_Name"],
                "Basic_Industry": classification["Basic_Industry"],
                "Industry": classification["Industry"],
                "Sector_Name": classification["Sector_Name"],
                "Macro_Economic_Sector": classification["Macro_Economic_Sector"],
                "Corpus_Per": pct_fraction * 100 if pct_fraction is not None else None,
                "Mkt_Value": mkt_value_lacs / 100 if mkt_value_lacs is not None else None,
                "No_Of_Shares": quantity if quantity is not None else "NULL",
                "Fund_Name": scheme.fund_name,
            }
        )
        report.converted += 1

    return out, report

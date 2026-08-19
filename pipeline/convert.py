"""The shared converter: intermediate rows -> the 14 ICRA-shaped columns.

Handles it all in one place: scheme name -> AMFI Code + Fund_Name
(pipeline/schemes.py), ISIN -> Instrument_Name + Nature_Name
(pipeline/isin_type.py), non-ISIN rows (TREPS, Gold, Silver, Net
Receivables, ...) -> Instrument_Name via the closed set in
pipeline/non_isin.py, ISIN -> Security_Name (pipeline/names.py, step 6),
a missing ISIN -> name-matched against the harvested spellings
(pipeline/names.py, step 3), lakhs -> crores, fraction -> 0-100 percent
scale, empty quantity -> "NULL".

Basic_Industry / Industry / Sector_Name / Macro_Economic_Sector are always
blank — see ICRA_CONVERSION_PLAN.md's 14-column output.

A row with no scheme mapping is dropped — with no AMFI Code there's no
fund for it to belong to, so it can't be written. Everything else is kept
and reported: rows whose Instrument_Name/Nature_Name couldn't be resolved
fall back to FALLBACK_CLASSIFICATION ("Undisclosed - Others", ICRA's own
vocabulary for exactly this), and rows whose Security_Name couldn't be
resolved are written with a blank Security_Name. Both make a scheme's row
count and Corpus_Per reconcile without being a substitute for a real
answer — report.tagged_isin / blank_security_name exist so a human can go
find them.

Order matters for a no-ISIN row: pipeline/non_isin.py's closed set is
checked *before* the name->ISIN fallback. A row like "TREPS" can, purely
by coincidence, match a fake ISIN-shaped placeholder code some AMC used
internally (it happens — see data/lookups/isin_name_variants.csv); classify
it as the known non-ISIN instrument it actually is before ever attempting
a name match, so that only rows the closed set truly doesn't recognize
fall through to Step 3.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.isin_type import IsinTypeTable, load_isin_type_table
from pipeline.names import NameTables, load_name_tables
from pipeline.non_isin import NonIsinRules, load_non_isin_rules
from pipeline.schema import IntermediateRow
from pipeline.schemes import _match_norm

# ICRA's own bucket for holdings it can't otherwise classify (243 rows in
# the May 2026 sample, all with a blank ISIN — see data/lookups/instrument_types.csv).
FALLBACK_CLASSIFICATION = {"Instrument_Name": "Undisclosed - Others", "Nature_Name": "Others"}

# Non-ISIN closed-set categories safe to trust over a *present* ISIN cell
# (see convert()'s precheck below) — deliberately a small subset of
# non_isin.py's full rule set. "Gold"/"Silver"/"Cash" are single generic
# words that collide with real, ISIN-bearing holdings by name coincidence
# (Senco Gold Limited, Sky Gold And Diamonds Limited, a fund holding
# another gold ETF's units); these three phrases are not real names.
_NAME_OVERRIDE_SAFE = {"Net Receivables/(Payables)", "Reverse repo", "Margin Deposit"}

BLANK_INDUSTRY = {
    "Basic_Industry": "",
    "Industry": "",
    "Sector_Name": "",
    "Macro_Economic_Sector": "",
}


@dataclass
class Lookups:
    amfi_names: dict[str, str]
    isin_aliases: dict[str, str]  # ISIN as it appears in the AMC file -> ISIN as it appears in ICRA
    isin_types: IsinTypeTable
    names: NameTables


def load_lookups(lookups_dir: str | Path = "data/lookups") -> Lookups:
    lookups_dir = Path(lookups_dir)
    amfi_names: dict[str, str] = {}
    with (lookups_dir / "amfi_codes.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            amfi_names[row["AMFI Code"]] = row["Fund_Name"]

    isin_aliases: dict[str, str] = {}
    aliases_path = lookups_dir / "isin_aliases.csv"
    if aliases_path.exists():
        with aliases_path.open(newline="") as f:
            for row in csv.DictReader(f):
                isin_aliases[row["isin_in_amc_file"]] = row["isin_in_icra"]

    return Lookups(
        amfi_names=amfi_names,
        isin_aliases=isin_aliases,
        isin_types=load_isin_type_table(lookups_dir / "isin_classification.csv"),
        names=load_name_tables(lookups_dir),
    )


@dataclass
class SchemeMapping:
    amfi_code: str
    fund_name: str


@dataclass
class AmcMapping:
    schemes: dict[str, SchemeMapping]  # sheet_name -> mapping
    non_isin: NonIsinRules


def load_amc_mapping(amc: str, mappings_dir: str | Path = "data/mappings") -> AmcMapping:
    amc_dir = Path(mappings_dir) / amc

    # Keyed by match_key (pipeline.schemes._match_norm of scheme_name_raw),
    # not the raw sheet_name text — the same scheme's name varies across
    # periods (a report-period stamp, a leading fund code), so convert()
    # below normalizes the row's own name the same way before looking it
    # up. Older schemes.csv files without a match_key column still work:
    # fall back to normalizing sheet_name at load time.
    schemes: dict[str, SchemeMapping] = {}
    schemes_path = amc_dir / "schemes.csv"
    if schemes_path.exists():
        with schemes_path.open(newline="") as f:
            for row in csv.DictReader(f):
                key = row["match_key"] if "match_key" in row else _match_norm(row["sheet_name"])
                schemes[key] = SchemeMapping(amfi_code=row["amfi_code"], fund_name=row["fund_name"])

    return AmcMapping(schemes=schemes, non_isin=load_non_isin_rules(amc, mappings_dir))


@dataclass
class ConvertReport:
    total: int = 0
    converted: int = 0
    unmapped_schemes: list[IntermediateRow] = field(default_factory=list)
    # Written to the output, but with FALLBACK_CLASSIFICATION rather than a
    # real one — kept here so a human can find and resolve them.
    tagged_isin: list[IntermediateRow] = field(default_factory=list)
    # Written with a blank Security_Name — nothing in the harvested
    # isin_names.csv/isin_name_variants.csv covered this ISIN.
    blank_security_name: list[IntermediateRow] = field(default_factory=list)
    # Schemes where the raw % column summed to neither ~100 nor ~1, so
    # convert() couldn't tell fraction from percent and wrote it as-is
    # (percent scale, factor 1) rather than guess.
    pct_scale_abstained: set[str] = field(default_factory=set)

    def ok(self) -> bool:
        return not self.unmapped_schemes

    def has_tagged(self) -> bool:
        return bool(self.tagged_isin or self.blank_security_name)


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        # Some AMCs print "NIL"/"-"/"N.A." in a numeric column instead of
        # leaving it blank — same meaning, not a parseable number.
        return None


def _detect_pct_scale(rows: list[IntermediateRow]) -> tuple[dict[str, float], set[str]]:
    """Some AMCs print 5.98 for 5.98%, some print 0.0598. Detect per
    scheme: the raw % column sums to ~100 if it's already percent, ~1 if
    it's a fraction — whatever the scale actually is, the weights in one
    scheme's holdings still have to add up to the whole. A sum outside
    both ranges means abstain (write the raw value, scale 1) and flag the
    scheme rather than silently guess and produce a Corpus_Per of 10000%.
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        try:
            p = float(row.pct_raw)
        except (TypeError, ValueError):
            continue
        sums[row.scheme_name_raw] = sums.get(row.scheme_name_raw, 0.0) + p
        counts[row.scheme_name_raw] = counts.get(row.scheme_name_raw, 0) + 1

    scale: dict[str, float] = {}
    abstained: set[str] = set()
    for scheme, total in sums.items():
        if counts[scheme] == 0:
            continue
        if abs(total - 100) <= 20:
            scale[scheme] = 1.0
        elif abs(total - 1) <= 0.2:
            scale[scheme] = 100.0
        else:
            scale[scheme] = 1.0
            abstained.add(scheme)
    return scale, abstained


def convert(
    rows: list[IntermediateRow],
    *,
    lookups: Lookups,
    amc_mapping: AmcMapping,
) -> tuple[list[dict], ConvertReport]:
    report = ConvertReport(total=len(rows))
    out: list[dict] = []

    pct_scale, report.pct_scale_abstained = _detect_pct_scale(rows)

    for row in rows:
        scheme = amc_mapping.schemes.get(_match_norm(row.scheme_name_raw))
        if scheme is None:
            report.unmapped_schemes.append(row)
            continue

        output_isin = row.isin or ""
        security_name: str | None = None

        # A row's own ISIN cell can hold a placeholder code that happens
        # to be shape- *and* checksum-valid (PGIM's TREPS rows print
        # "INTREP020226" — is_valid_isin(...) is True by coincidence) even
        # though the row is a known non-ISIN instrument — see
        # _NAME_OVERRIDE_SAFE above for why only some categories qualify.
        precheck_instr, _precheck_nat = amc_mapping.non_isin.classify(row.security_name, None)
        row_is_known_non_isin = precheck_instr in _NAME_OVERRIDE_SAFE

        if row.isin and not row_is_known_non_isin:
            output_isin = lookups.isin_aliases.get(row.isin, row.isin)
            instr, nat, _source = lookups.isin_types.classify(output_isin)
            if instr is None and output_isin != row.isin:
                instr, nat, _source = lookups.isin_types.classify(row.isin)
            if instr is None:
                instr, nat = FALLBACK_CLASSIFICATION["Instrument_Name"], FALLBACK_CLASSIFICATION["Nature_Name"]
                report.tagged_isin.append(row)

            security_name = lookups.names.security_name(output_isin)
            if security_name is None:
                report.blank_security_name.append(row)
        else:
            # No ISIN on the row, or the closed-set precheck above says
            # what's in the ISIN cell isn't really one — either way,
            # nothing from row.isin belongs in the output. Check the
            # closed set of non-ISIN instruments first (TREPS, Gold, Net
            # Receivables, ...) — only if that falls through to the
            # generic fallback do we try to identify a real ISIN by
            # matching the printed name.
            output_isin = ""
            instr, nat = amc_mapping.non_isin.classify(row.security_name, row.section_header)
            if instr == FALLBACK_CLASSIFICATION["Instrument_Name"]:
                matched_isin = lookups.names.isin_from_name(row.security_name)
                if matched_isin:
                    output_isin = matched_isin
                    instr, nat, _source = lookups.isin_types.classify(matched_isin)
                    if instr is None:
                        instr, nat = (
                            FALLBACK_CLASSIFICATION["Instrument_Name"],
                            FALLBACK_CLASSIFICATION["Nature_Name"],
                        )
                    security_name = lookups.names.security_name(matched_isin) or row.security_name
                else:
                    security_name = row.security_name
                    report.tagged_isin.append(row)
            else:
                security_name = row.security_name

        mkt_value_lacs = _num(row.market_value_raw)
        pct_raw = _num(row.pct_raw)
        quantity = _num(row.quantity)
        scale = pct_scale.get(row.scheme_name_raw, 1.0)

        out.append(
            {
                "AMFI Code": scheme.amfi_code,
                "Port_Date": row.port_date,
                "ISIN": output_isin,
                "Instrument_Name": instr,
                "Nature_Name": nat,
                **BLANK_INDUSTRY,
                "Corpus_Per": pct_raw * scale if pct_raw is not None else None,
                "Mkt_Value": mkt_value_lacs / 100 if mkt_value_lacs is not None else None,
                "No_Of_Shares": quantity if quantity is not None else "NULL",
                "Fund_Name": scheme.fund_name,
                "Security_Name": security_name or "",
            }
        )
        report.converted += 1

    return out, report

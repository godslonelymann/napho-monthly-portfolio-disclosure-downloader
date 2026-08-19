"""Steps 3 and 6 — both directions between a row's ISIN and its name,
entirely over data/lookups/isin_names.csv and isin_name_variants.csv
(pipeline/isin_names.py's harvest of data/raw itself — no external
masters, no network calls).

Step 6 (ISIN -> Security_Name): data/lookups/isin_names.csv already has
one canonical, most-recently-observed spelling per ISIN. Direct lookup.

Step 3 (name -> ISIN): a row is a real security but the AMC printed no
ISIN. data/lookups/isin_name_variants.csv has every spelling any AMC has
ever used for every ISIN, keyed the same way pipeline/isin_names.py
groups them (match_key: uppercased, punctuation stripped, Ltd/Limited
folded together) — build the reverse index once and look the row's own
(equally normalized) name up in it.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pipeline.isin_names import match_key


@dataclass
class NameTables:
    isin_to_name: dict[str, str]  # ISIN -> canonical Security_Name
    match_key_to_isin: dict[str, str]  # match_key(any observed spelling) -> ISIN

    def security_name(self, isin: str) -> str | None:
        return self.isin_to_name.get(isin)

    def isin_from_name(self, name: str) -> str | None:
        return self.match_key_to_isin.get(match_key(name))


def load_name_tables(lookups_dir: str | Path = "data/lookups") -> NameTables:
    lookups_dir = Path(lookups_dir)

    isin_to_name: dict[str, str] = {}
    names_path = lookups_dir / "isin_names.csv"
    if names_path.exists():
        with names_path.open(newline="") as f:
            for row in csv.DictReader(f):
                isin_to_name[row["ISIN"]] = row["canonical_name"]

    # A match_key can, in principle, span more than one ISIN (two
    # different entities that normalize the same way) — pick whichever
    # ISIN has the most observations under that key so an ambiguous name
    # still resolves to its most-likely owner rather than an arbitrary one.
    key_isin_counts: dict[str, dict[str, int]] = defaultdict(dict)
    variants_path = lookups_dir / "isin_name_variants.csv"
    if variants_path.exists():
        with variants_path.open(newline="") as f:
            for row in csv.DictReader(f):
                key = row["match_key"]
                isin = row["ISIN"]
                n = int(row["count"])
                key_isin_counts[key][isin] = key_isin_counts[key].get(isin, 0) + n

    # A count-1 variant is often a one-off misread (a column-misaligned
    # row somewhere in 43,000+ source files landing a section-header
    # label like "(a) Listed/awaiting listing on Stock Exchanges" next to
    # some unrelated ISIN) rather than a real spelling of that security.
    # Require a few independent observations before trusting a key enough
    # to hand out its ISIN to every future row that normalizes the same way.
    _MIN_OBSERVATIONS = 3
    match_key_to_isin = {
        key: best_isin
        for key, isin_counts in key_isin_counts.items()
        for best_isin, best_count in [max(isin_counts.items(), key=lambda kv: kv[1])]
        if best_count >= _MIN_OBSERVATIONS
    }

    return NameTables(isin_to_name=isin_to_name, match_key_to_isin=match_key_to_isin)

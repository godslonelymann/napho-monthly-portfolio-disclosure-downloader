"""Step 5 — ISIN -> Instrument_Name + Nature_Name.

Key on ISIN[:3] + ISIN[7:9] (the 3-char country/type prefix, plus the
2-digit code at offset 7). Keying on the 2-digit code alone scores 90.6%
because INF fund units collide with INE equities on the same code; adding
the 3-char prefix separates them and scores 96.7%, measured against every
ISIN in data/lookups/isin_classification.csv (the ICRA May-2026 seed).
Entirely self-contained — no external data, just ICRA's own sample.

For an ISIN ICRA has already classified, use that seed value directly —
it's exact, not a 96.7%-accurate guess. The key table only fires for ISINs
the seed has never seen.

Basic_Industry / Industry / Sector_Name / Macro_Economic_Sector are not
produced here — the 14-column output leaves them blank; ISIN
classification only ever fills Instrument_Name + Nature_Name.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Source = Literal["exact", "key", "none"]


def _key(isin: str) -> str | None:
    if len(isin) < 9:
        return None
    return isin[:3] + isin[7:9]


@dataclass
class IsinTypeTable:
    seed: dict[str, tuple[str, str]]  # full ISIN -> (Instrument_Name, Nature_Name)
    key_table: dict[str, tuple[str, str]]  # ISIN[:3]+ISIN[7:9] -> (Instrument_Name, Nature_Name)

    def classify(self, isin: str) -> tuple[str | None, str | None, Source]:
        hit = self.seed.get(isin)
        if hit is not None:
            return hit[0], hit[1], "exact"
        key = _key(isin)
        hit = self.key_table.get(key) if key else None
        if hit is not None:
            return hit[0], hit[1], "key"
        return None, None, "none"


def build_key_table(
    isin_classification_path: str | Path = "data/lookups/isin_classification.csv",
) -> dict[str, tuple[str, str]]:
    groups: dict[str, Counter] = defaultdict(Counter)
    with Path(isin_classification_path).open(newline="") as f:
        for row in csv.DictReader(f):
            key = _key(row["ISIN"])
            if key is None:
                continue
            groups[key][(row["Instrument_Name"], row["Nature_Name"])] += 1
    return {key: counter.most_common(1)[0][0] for key, counter in groups.items()}


def load_isin_type_table(
    isin_classification_path: str | Path = "data/lookups/isin_classification.csv",
) -> IsinTypeTable:
    seed: dict[str, tuple[str, str]] = {}
    with Path(isin_classification_path).open(newline="") as f:
        for row in csv.DictReader(f):
            seed[row["ISIN"]] = (row["Instrument_Name"], row["Nature_Name"])
    key_table = build_key_table(isin_classification_path)
    return IsinTypeTable(seed=seed, key_table=key_table)


def write_key_table(
    key_table: dict[str, tuple[str, str]],
    out_path: str | Path = "data/lookups/isin_type_keys.csv",
) -> int:
    with Path(out_path).open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["key", "Instrument_Name", "Nature_Name"])
        for key in sorted(key_table):
            instr, nat = key_table[key]
            writer.writerow([key, instr, nat])
    return len(key_table)


if __name__ == "__main__":
    table = load_isin_type_table()
    n = write_key_table(table.key_table)
    print(f"seed ISINs: {len(table.seed)}")
    print(f"key rules:  {n}")

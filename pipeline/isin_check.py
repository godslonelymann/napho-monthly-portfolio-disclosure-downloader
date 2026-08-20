"""ISO 6166 ISIN check-digit validation.

Needed because things that are shaped exactly like an ISIN aren't always
one: `AUBANK300626` and `BHEL29052025` are futures contract codes, same
12 characters, same alnum layout, wrong check digit. Trusting the shape
alone routes them into the ISIN column; validating the digit routes them
to the contracts file instead.

Same algorithm pipeline/isin_names.py already has inline
(isin_check_digit_ok) — pulled out here so extract.py and everything else
can share one implementation instead of a second copy drifting.
"""

from __future__ import annotations

import re

ISIN_SHAPE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# A futures/options contract code built as TICKER + expiry date
# ("MARUTI300626", "INDIGO300626", "BEML00000000") — 4-8 letters then a
# 6- or 8-digit run filling out the rest of the 12 characters. Most of
# these fail the ISO 6166 check digit and is_valid_isin() alone already
# rejects them, but a coincidental few pass it (Unifi's MARUTI300626 and
# INDIGO300626 among them) the same way PGIM's TREPS placeholder
# "INTREP020226" did — see pipeline/non_isin.py. A real ISIN never has
# this shape: positions 3+ mix letters and digits throughout (an actual
# Indian ISIN reads like "INE040A01034"), never a clean letters-then-
# nothing-but-digits split.
#
# "IDIA" + 8 digits (IDIA00500002, ...) matches this same shape but is a
# real, deliberately-used identifier in this data (SBI Silver ETF's
# depository-receipt-style code for physical silver, among others, and
# ICRA's own dataset carries the identical code) — excluded explicitly
# rather than caught by a length/prefix coincidence, so a future
# four-letter ticker that happens to also start "IDIA" doesn't
# accidentally slip back through.
_CONTRACT_CODE_SHAPE = re.compile(r"^[A-Z]{4,8}\d{6,8}$")
_CONTRACT_CODE_EXCLUDE_PREFIXES = ("IDIA",)


def looks_like_contract_code(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip().upper()
    if v.startswith(_CONTRACT_CODE_EXCLUDE_PREFIXES):
        return False
    return bool(_CONTRACT_CODE_SHAPE.match(v))


def _expand(isin: str) -> str:
    out = []
    for ch in isin:
        if ch.isdigit():
            out.append(ch)
        else:
            out.append(str(ord(ch) - ord("A") + 10))
    return "".join(out)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def has_isin_shape(value: str | None) -> bool:
    if not value:
        return False
    return bool(ISIN_SHAPE.match(value.strip().upper()))


def is_valid_isin(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip().upper()
    if not ISIN_SHAPE.match(v):
        return False
    return _luhn_ok(_expand(v))

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

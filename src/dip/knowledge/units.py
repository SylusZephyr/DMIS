"""Unit normalisation (spec 67): find quantities in free text and convert them to canonical units.

    "50,000 rpm", "50000 RPM", "50k RPM", "50 KRPM"  -> 50000 rpm
    "2.8 N.cm", "0.028 N·m"                          -> 2.8 N·cm
    "1.2 kg", "1200 g"                               -> 1200 g
    "pack of 50", "50 pcs", "50-pack"                -> 50 count

Every match keeps the original text and its character span, so an extracted value can always be
shown next to the words it came from (spec 14: provenance). Nothing is inferred beyond the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"


@dataclass(frozen=True)
class Quantity:
    quantity: str        # rpm | volt | watt | amp | torque | length | mass | volume | count
    value: float         # in the canonical unit
    unit: str            # canonical unit
    raw: str             # the matched text
    start: int
    end: int


def _num(s: str) -> float:
    return float(s.replace(",", ""))


# (quantity, regex, converter(match) -> value in canonical unit, canonical unit)
_RULES: list[tuple[str, re.Pattern, object, str]] = [
    ("rpm", re.compile(_NUM + r"\s*(k)?\s*(rpm|r/min|r\.p\.m\.?)\b", re.I),
     lambda m: _num(m.group(1)) * (1000 if m.group(2) else 1), "rpm"),
    ("rpm", re.compile(_NUM + r"\s*krpm\b", re.I), lambda m: _num(m.group(1)) * 1000, "rpm"),
    ("rpm", re.compile(_NUM + r"\s*(k|万)?\s*转", re.I),
     lambda m: _num(m.group(1)) * {"k": 1000, "K": 1000, "万": 10000}.get(m.group(2) or "", 1), "rpm"),
    ("torque", re.compile(_NUM + r"\s*n\s*[.·]?\s*cm\b", re.I), lambda m: _num(m.group(1)), "N·cm"),
    ("torque", re.compile(_NUM + r"\s*n\s*[.·]\s*m\b", re.I), lambda m: _num(m.group(1)) * 100, "N·cm"),
    ("watt", re.compile(_NUM + r"\s*(w|watts?)\b", re.I), lambda m: _num(m.group(1)), "W"),
    ("volt", re.compile(_NUM + r"\s*(v|volts?|vac|vdc)\b", re.I), lambda m: _num(m.group(1)), "V"),
    ("amp", re.compile(_NUM + r"\s*(a|amps?)\b(?!\s*[a-z])", re.I), lambda m: _num(m.group(1)), "A"),
    ("length", re.compile(_NUM + r"\s*mm\b", re.I), lambda m: _num(m.group(1)), "mm"),
    ("length", re.compile(_NUM + r"\s*cm\b", re.I), lambda m: _num(m.group(1)) * 10, "mm"),
    ("length", re.compile(_NUM + r"\s*(inch(?:es)?|in\.|\")", re.I), lambda m: _num(m.group(1)) * 25.4, "mm"),
    ("mass", re.compile(_NUM + r"\s*kg\b", re.I), lambda m: _num(m.group(1)) * 1000, "g"),
    ("mass", re.compile(_NUM + r"\s*(g|grams?)\b", re.I), lambda m: _num(m.group(1)), "g"),
    ("mass", re.compile(_NUM + r"\s*(lbs?|pounds?)\b", re.I), lambda m: _num(m.group(1)) * 453.592, "g"),
    ("mass", re.compile(_NUM + r"\s*oz\b(?!\s*fl)", re.I), lambda m: _num(m.group(1)) * 28.3495, "g"),
    ("volume", re.compile(_NUM + r"\s*ml\b", re.I), lambda m: _num(m.group(1)), "ml"),
    ("volume", re.compile(_NUM + r"\s*fl\.?\s*oz\b", re.I), lambda m: _num(m.group(1)) * 29.5735, "ml"),
    ("count", re.compile(r"\bpack\s+of\s+" + _NUM, re.I), lambda m: _num(m.group(1)), "count"),
    ("count", re.compile(_NUM + r"\s*[- ]?\s*(pcs|pc|pieces|pack|count|ct|sets?|pairs?)\b", re.I), lambda m: _num(m.group(1)), "count"),
]


def find(text: str | None) -> list[Quantity]:
    """Every quantity in ``text``, left to right; overlapping matches keep the earliest rule."""
    if not isinstance(text, str) or not text:
        return []
    out: list[Quantity] = []
    taken: list[tuple[int, int]] = []
    for q, rx, conv, unit in _RULES:
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            try:
                value = float(conv(m))  # type: ignore[operator]
            except (ValueError, TypeError):
                continue
            taken.append((s, e))
            out.append(Quantity(q, round(value, 4), unit, m.group(0), s, e))
    return sorted(out, key=lambda x: x.start)


def best(text: str | None, quantity: str, plausible: tuple[float, float] | None = None) -> Quantity | None:
    """The largest plausible value of one quantity (a spec sheet states the maximum, e.g. max RPM)."""
    hits = [x for x in find(text) if x.quantity == quantity
            and (plausible is None or plausible[0] <= x.value <= plausible[1])]
    return max(hits, key=lambda x: x.value) if hits else None


def first(text: str | None, quantity: str, plausible: tuple[float, float] | None = None) -> Quantity | None:
    """The first plausible value of one quantity, in reading order."""
    hits = [x for x in find(text) if x.quantity == quantity
            and (plausible is None or plausible[0] <= x.value <= plausible[1])]
    return hits[0] if hits else None

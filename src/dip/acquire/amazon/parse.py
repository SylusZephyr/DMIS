"""Small, tested parsers for the text Amazon (and data APIs quoting it) show: money, ratings, counts,
"bought in past month" badges, weights and review dates."""

from __future__ import annotations

import re
from datetime import date, datetime

_MONEY = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
_BOUGHT = re.compile(r"(\d+(?:\.\d+)?)\s*([KkMm])?\+?\s*bought in past month", re.I)
_RATING = re.compile(r"(\d(?:\.\d)?)\s*out of\s*5", re.I)
_INT = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(ounces?|oz|pounds?|lbs?|lb|kilograms?|kg|grams?|g)\b", re.I)
_TO_G = {"ounce": 28.3495, "ounces": 28.3495, "oz": 28.3495, "pound": 453.592, "pounds": 453.592, "lb": 453.592,
         "lbs": 453.592, "kilogram": 1000.0, "kilograms": 1000.0, "kg": 1000.0, "gram": 1.0, "grams": 1.0, "g": 1.0}
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}
_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})", re.I)


def money(text: str | float | None) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _MONEY.search(str(text).replace(" ", " "))
    if not m:
        return None
    return float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))


def bought_past_month(text: str | None) -> float | None:
    """'1K+ bought in past month' -> 1000.0 (the badge floor); None when there is no badge."""
    if not text:
        return None
    m = _BOUGHT.search(text)
    if not m:
        return None
    v = float(m.group(1)) * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)
    return float(round(v))


def rating(text: str | float | None) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _RATING.search(text)
    if m:
        return float(m.group(1))
    try:
        v = float(str(text).strip())
        return v if 0 <= v <= 5 else None
    except ValueError:
        return None


def integer(text: str | int | None) -> int | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = _INT.search(str(text))
    return int(m.group(0).replace(",", "")) if m else None


def weight_grams(text: str | None) -> float | None:
    if not text:
        return None
    m = _WEIGHT.search(text)
    if not m:
        return None
    return round(float(m.group(1)) * _TO_G[m.group(2).lower()], 1)


def any_date(text: str | None) -> date | None:
    """'Reviewed in the United States on March 3, 2026' / '2026-03-03' -> date."""
    if not text:
        return None
    m = _DATE.search(text)
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    try:
        return datetime.fromisoformat(str(text).strip().replace("Z", "+00:00")[:19]).date()
    except ValueError:
        return None

"""Per-pair similarity signals for listing-to-product entity resolution
(Stages 2-4). Pure functions, no I/O — see resolution.py for how these
combine into a final decision (Stage 6).
"""

from __future__ import annotations

import difflib
import re

_GENERIC_BRANDS = {"generic", "generic brand", "unbranded", ""}
# Two orders: "3 ea" / "2 pack" (number first) and "Pack of 2" (number after).
# Unit vocabulary widened (see docs/entity_resolution_evaluation_report.md
# false-merge #3): "bottle" was missing entirely, so "2 Bottle" extracted
# no quantity signal at all and a real pack-count mismatch against a
# "1 Box" listing went undetected. "set"/"count"/"ct" added for the same
# reason -- real SellerSprite titles use all of them.
_QTY_BEFORE_RE = re.compile(r"\b(\d+)\s*(?:pcs?|pc|pack|box|ea|piece|bottle|set|count|ct)s?\b", re.IGNORECASE)
_QTY_AFTER_RE = re.compile(r"pack of\s*(\d+)", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def normalize_brand(brand: str | None) -> str | None:
    if not brand:
        return None
    text = brand.strip().lower()
    return "generic" if text in _GENERIC_BRANDS else text


def normalize_title(title: str | None) -> str:
    text = (title or "").lower()
    text = _PUNCT_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def brand_score(brand_a: str | None, brand_b: str | None) -> float:
    """Stage 2: normalized brand/model matching. No model-number field
    exists in this dataset (see docs/data_dictionary.md) — brand is the
    only identifier-adjacent signal available."""
    a, b = normalize_brand(brand_a), normalize_brand(brand_b)
    if a is None or b is None or a == "generic" or b == "generic":
        return 0.6  # unknown -- neither confirms nor rules out a match
    return 1.0 if a == b else 0.3


def title_similarity(title_a: str | None, title_b: str | None) -> float:
    """Stage 3: normalized title similarity."""
    a, b = normalize_title(title_a), normalize_title(title_b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def attribute_score(price_a: float | None, price_b: float | None) -> float:
    """Stage 4: technical attribute similarity. Scoped to price for this
    dataset — package weight/dimensions exist in the raw SellerSprite
    export (docs/data_dictionary.md) but were never brought into the
    normalized `listings` schema. Extending this function to use them is
    a natural follow-up; it isn't required to correctly resolve the
    pilot's known cases (validated in tests/unit/test_resolution.py)."""
    if price_a is None or price_b is None:
        return 0.5  # unknown -- neutral, neither helps nor hurts
    if price_a == 0 and price_b == 0:
        return 1.0
    diff = abs(price_a - price_b) / max(price_a, price_b, 0.01)
    return max(0.0, 1.0 - diff)


def extract_quantities(title: str | None) -> set[str]:
    """All pack/quantity mentions in a title (e.g. '3 ea', 'Pack of 2',
    '1Pack'), covering both word orders. Used to catch pack-size/bundle
    variants, which should never be silently auto-merged even at high
    title similarity — see docs/entity_resolution.md's bundle non-merge
    policy.

    Known limitation: this is presence/overlap detection, not quantity
    arithmetic — "3 ea (Pack of 2)" (2 bundles of 3) extracts {"3", "2"}
    and will NOT be flagged as mismatched against a plain "3 ea" listing
    (which extracts {"3"}, and the two sets overlap on "3"). Composite
    scoring (title/brand/price) is the primary defense for that case, not
    this function — see tests/unit/test_resolution.py."""
    text = title or ""
    return set(_QTY_BEFORE_RE.findall(text)) | set(_QTY_AFTER_RE.findall(text))


def quantity_signals(title_a: str | None, title_b: str | None) -> tuple[set[str], set[str]]:
    return extract_quantities(title_a), extract_quantities(title_b)

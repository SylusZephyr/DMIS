"""Opportunity Score (Tier 2, Milestone 9): a single deterministic 0-100
summary of a product's opportunity signals -- never a second, independent
judgment. Every input is a signal that opportunity/signals.py already
computed and evidence.py already recorded with its own conditions/
values/thresholds; this module only aggregates what's already there and
already visible in `opportunity_signals`.

Only PRODUCT-scoped signal types feed a per-product score
(PRODUCT_IMPROVEMENT, BUNDLE, CUSTOMER_PAIN_POINT). PRICE_SEGMENT,
UNDERREPRESENTED_PRODUCT_TYPE, and COMPETITIVE_CONCENTRATION describe the
category, not one product (product_id is None on those rows, see
signals.py's own docstring) -- a category-level score is a natural future
extension, not built here (Tier 2 explicitly stays additive/minimal).
"""

from __future__ import annotations

PRODUCT_SIGNAL_TYPES = ("PRODUCT_IMPROVEMENT", "BUNDLE", "CUSTOMER_PAIN_POINT")

# LOW/MEDIUM/HIGH -> a 0-100 base weight (signals.py's own strength
# bands: >=2.0x threshold = HIGH, >=1.3x = MEDIUM, else LOW). Not
# recalibrated against real outcome data yet -- an evenly-spaced default,
# the same honest caveat signal_strength itself already carries.
STRENGTH_WEIGHTS = {"LOW": 30.0, "MEDIUM": 60.0, "HIGH": 90.0}


def compute_opportunity_score(signal_records: list[dict]) -> float | None:
    """`signal_records`: opportunity_signals-shaped dicts (signal_type,
    status, signal_strength, confidence) for ONE product -- pass every
    signal row for that product_id, category-level rows are filtered out
    automatically by signal_type.

    Each product-scoped signal contributes:
      - excluded entirely if status == 'insufficient_data' (not evaluated
        yet -- never treated as absent or as a 0, PRINCIPLES.md "None never 0")
      - 0.0 if status == 'signal_absent'
      - STRENGTH_WEIGHTS[signal_strength] * confidence if 'signal_present'
        (confidence in [0, 1] scales the contribution down when the
        finding is backed by little data, same discount signal_strength
        itself doesn't apply)

    The score is the mean of all contributions. Returns None when no
    product-scoped signal was ever evaluated (every one is
    insufficient_data) -- a product with zero evaluable signals has no
    score, not a fabricated default.
    """
    contributions: list[float] = []
    for record in signal_records:
        if record.get("signal_type") not in PRODUCT_SIGNAL_TYPES:
            continue
        status = record.get("status")
        if status == "insufficient_data":
            continue
        if status == "signal_absent":
            contributions.append(0.0)
        elif status == "signal_present":
            weight = STRENGTH_WEIGHTS.get(record.get("signal_strength"), 0.0)
            contributions.append(weight * (record.get("confidence") or 0.0))

    if not contributions:
        return None
    return round(sum(contributions) / len(contributions), 1)

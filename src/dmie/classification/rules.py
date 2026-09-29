"""Deterministic relevance rules for the denture_base leaf category.

Every rule here is grounded in specific ASINs from
data/samples/gold_labels_pilot.xlsx — see docs/classification_guidelines.md
for the reasoning behind each pattern. Rules are deliberately conservative:
they fire only on unambiguous textual signals. Titles that don't match any
rule (including ones suppressed by an escape pattern) are deferred to the
AI stage / human review queue in classifier.py — not force-fit into a rule.

Known limitation: substring matching cannot always distinguish this
leaf category's edge cases from lexically-similar competitors (e.g. a
"DIY Denture Kit" that builds a complete denture vs. one that's really a
tooth-filling-bead product with the same generic phrase in its title).
docs/entity_resolution.md and classification_guidelines.md document this;
run src/dmie/classification/evaluation.py against the gold set to see
exactly which rows the rules get wrong, rather than patching rules to force
100% agreement with hand-labeled data.
"""

import re

RULES_VERSION = "rules_v1"
RULE_CONFIDENCE = 0.95

# If any of these appear, do NOT apply a deterministic IRRELEVANT rule —
# these are exactly the signals classification_guidelines.md documents as
# making a listing AMBIGUOUS rather than a clean NO.
_ESCAPE_PATTERNS = [
    r"reline",
    r"refit",
    r"full denture",
    r"base former",
    r"denture mold",
]

# (pattern, reason_code) — checked in order, first match wins.
_RELEVANT_PATTERNS = [
    (r"denture base (resin|repair|renewal)", "EXACT_MATCH"),
    (r"base plate wax", "EXACT_MATCH"),
    (r"denture base wax", "EXACT_MATCH"),
    (r"denture base plate", "EXACT_MATCH"),
]

_IRRELEVANT_PATTERNS = [
    (r"adhesive", "WRONG_CATEGORY"),
    (r"bonding.*glue", "WRONG_CATEGORY"),
    (r"ultrasonic.*(cleaner|cleaning)", "WRONG_CATEGORY"),
    (r"screwdriver|bit holder", "UNRELATED"),
    (r"moldable false teeth beads|thermal beads|teeth replacement kit|fake tooth|fake teeth", "WRONG_CATEGORY"),
    (r"denture repair kit", "WRONG_CATEGORY"),
    (r"polishing (bur|head)", "ACCESSORY_ONLY"),
]


def apply_rules(title: str) -> tuple[str, str] | None:
    """Returns (relevance_class, reason_code) if a deterministic rule
    fires, else None (meaning: defer to the AI stage / human review)."""
    text = (title or "").lower()

    for pattern, reason in _RELEVANT_PATTERNS:
        if re.search(pattern, text):
            return "RELEVANT", reason

    if any(re.search(pattern, text) for pattern in _ESCAPE_PATTERNS):
        return None

    for pattern, reason in _IRRELEVANT_PATTERNS:
        if re.search(pattern, text):
            return "IRRELEVANT", reason

    return None

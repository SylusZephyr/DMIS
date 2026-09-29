"""Hybrid product-type classifier (M11 Stage 2): deterministic keyword
rules first, semantic AI second, confidence thresholding, human review
queue for what's left — the same hybrid pattern already used by relevance
classification (classifier.py) and entity resolution
(matching/resolution.py).

Classifies ONLY listings already confirmed RELEVANT — product type is a
property of an in-category product, not a way to decide category
membership (PRINCIPLES.md "Listing != Product"). This module does not check
relevance itself; callers are responsible for only calling it on RELEVANT
listings.

The taxonomy (config/taxonomy/{category}_v{version}.yaml) is frozen and
never modified here — see DECISIONS.md "M11 — Taxonomy v1 frozen." The
allowed output space is loaded from that file, never hardcoded as
`if "resin" in title: return "DB_RESIN"` — adding a category or bumping a
taxonomy version means adding config, not editing this module's code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from dmie.ai import call_ai

CLASSIFIER_VERSION = "product_type_hybrid_v1"
PROMPT_VERSION = "product_type_classifier_v1"
AI_MODEL = "claude-sonnet-4-5"
DEFAULT_CATEGORY = "denture_base"
DEFAULT_TAXONOMY_VERSION = 1
UNCERTAIN = "UNCERTAIN"

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "product_type_classifier.md"
_TAXONOMY_DIR = _PROJECT_ROOT / "config" / "taxonomy"
_SIGNALS_PATH = _PROJECT_ROOT / "config" / "product_type_signals.yaml"
_THRESHOLDS_PATH = _PROJECT_ROOT / "config" / "thresholds.yaml"

_WORD_PATTERN_CACHE: dict[str, re.Pattern] = {}


@dataclass
class TaxonomyEntry:
    id: str
    name: str
    definition: str


@dataclass
class Taxonomy:
    category: str
    version: int
    entries: list[TaxonomyEntry]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self.entries)


@dataclass
class ListingContext:
    listing_id: str
    title: str | None
    brand: str | None = None
    bullets: str | None = None
    description: str | None = None


@dataclass
class ProductTypeResult:
    listing_id: str
    taxonomy_version: int
    product_type: str
    confidence: float
    reason: str
    classifier_method: str  # "rules" | "ai" | "unavailable"
    review_status: str


@lru_cache(maxsize=None)
def load_taxonomy(category: str = DEFAULT_CATEGORY, version: int = DEFAULT_TAXONOMY_VERSION) -> Taxonomy:
    """Loads the frozen taxonomy for `category`/`version` — the only
    allowed output space for classification. Never hardcoded in Python.
    Cached: classify_product_type calls this once per RELEVANT listing --
    re-reading + re-parsing the taxonomy file that often is pure
    redundant I/O at real scale, same class of bug found and fixed in
    dmie.matching.resolution._variant_policy. The taxonomy file is frozen
    (DECISIONS.md "Taxonomy v1 frozen") so it never changes within a
    process's lifetime."""
    path = _TAXONOMY_DIR / f"{category}_v{version}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    key = f"{category}_taxonomy_v{version}"
    entries = [
        TaxonomyEntry(id=e["id"], name=e["name"], definition=e["definition"].strip())
        for e in data[key]
    ]
    return Taxonomy(category=data["category"], version=data["version"], entries=entries)


@lru_cache(maxsize=None)
def _load_signals(category: str, version: int) -> dict[str, list[str]]:
    """Keyword signals for the deterministic rules layer — a separate,
    editable config from the frozen taxonomy itself (see
    config/product_type_signals.yaml's header for why). Cached, same
    reasoning as load_taxonomy."""
    data = yaml.safe_load(_SIGNALS_PATH.read_text(encoding="utf-8"))
    entry = data[category]
    if entry["taxonomy_version"] != version:
        raise ValueError(
            f"product_type_signals.yaml is configured for taxonomy_version "
            f"{entry['taxonomy_version']}, not {version} — update the signals "
            f"file for the new taxonomy version before classifying against it."
        )
    return entry["signals"]


@lru_cache(maxsize=None)
def _load_thresholds() -> dict:
    return yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))["product_type"]


def _word_pattern(word: str) -> re.Pattern:
    if word not in _WORD_PATTERN_CACHE:
        _WORD_PATTERN_CACHE[word] = re.compile(r"\b" + re.escape(word) + r"\b")
    return _WORD_PATTERN_CACHE[word]


def apply_rules(title: str, signals: dict[str, list[str]]) -> tuple[str, float, str] | None:
    """Deterministic Stage 1. Scores every taxonomy id in `signals` by how
    many of its keyword signals appear in `title` (whole-word,
    case-insensitive), then returns (product_type, confidence, reason):

    - Exactly one id scores > 0, no other id scores > 0: a clean,
      unambiguous match, at `rules_confidence` (config-driven, 0.95).
    - More than one id scores > 0: the title's vocabulary genuinely
      overlaps more than one type (e.g. "acrylic" appears in both resin
      and reline-kit titles — same material chemistry, different
      purpose). Not decidable by keywords alone: returns the top-scoring
      id at `rules_ambiguous_confidence` (0.50), which is always below
      the automatic-accept threshold, so the caller must defer to AI
      rather than trust this value directly.
    - No id scores > 0: no signal at all — returns None so the caller
      knows there isn't even an ambiguous hint to report.
    """
    text = (title or "").lower()
    thresholds = _load_thresholds()

    scores = {
        type_id: sum(1 for word in words if _word_pattern(word).search(text))
        for type_id, words in signals.items()
    }
    winners = [type_id for type_id, score in scores.items() if score > 0]

    if not winners:
        return None

    if len(winners) == 1:
        return winners[0], thresholds["rules_confidence"], f"rule_match:{winners[0]}"

    max_score = max(scores[t] for t in winners)
    tied = sorted(t for t in winners if scores[t] == max_score)
    return tied[0], thresholds["rules_ambiguous_confidence"], f"rule_ambiguous:{','.join(sorted(winners))}"


def classify_with_ai(listing: ListingContext, taxonomy: Taxonomy) -> dict | None:
    """Calls the configured LLM. Returns None when no AI provider is
    configured (no ANTHROPIC_API_KEY, or the `anthropic` package isn't
    installed), or when the call/parse ultimately failed after retries —
    callers must treat None as "AI unavailable," never a verdict, same
    graceful-degradation pattern as classifier.py::classify_with_ai and
    matching/resolution.py::ai_arbitrate. A single failed AI request
    degrades this one classification, it never raises out of this
    function to crash a batch run (M13)."""
    allowed = "\n".join(f"- {e.id}: {e.name} -- {e.definition}" for e in taxonomy.entries)
    listing_data = json.dumps(
        {
            "title": listing.title,
            "brand": listing.brand,
            "bullets": listing.bullets,
            "description": listing.description,
        },
        indent=2,
        ensure_ascii=False,
    )
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{TAXONOMY_VERSION}", str(taxonomy.version))
        .replace("{ALLOWED_PRODUCT_TYPES}", allowed)
        .replace("{LISTING_DATA}", listing_data)
    )

    outcome = call_ai(prompt, model=AI_MODEL, max_tokens=300)
    return outcome.data if outcome.ok else None


def classify_product_type(
    listing: ListingContext,
    category: str = DEFAULT_CATEGORY,
    taxonomy_version: int = DEFAULT_TAXONOMY_VERSION,
) -> ProductTypeResult:
    taxonomy = load_taxonomy(category, taxonomy_version)
    signals = _load_signals(category, taxonomy_version)
    thresholds = _load_thresholds()

    rule_result = apply_rules(listing.title or "", signals)
    if rule_result is not None:
        product_type, confidence, reason = rule_result
        if confidence >= thresholds["automatic_threshold"]:
            return ProductTypeResult(
                listing_id=listing.listing_id,
                taxonomy_version=taxonomy.version,
                product_type=product_type,
                confidence=confidence,
                reason=reason,
                classifier_method="rules",
                review_status="auto_accepted",
            )

    ai_result = classify_with_ai(listing, taxonomy)
    if ai_result is None:
        # AI unavailable and rules didn't clear the automatic threshold --
        # never guess. If rules found an ambiguous signal, surface it in
        # the reason even though the type itself is withheld.
        reason = rule_result[2] if rule_result is not None else "ai_unavailable"
        return ProductTypeResult(
            listing_id=listing.listing_id,
            taxonomy_version=taxonomy.version,
            product_type=UNCERTAIN,
            confidence=0.0,
            reason=reason,
            classifier_method="unavailable",
            review_status="needs_review",
        )

    confidence = float(ai_result.get("confidence", 0.0))
    product_type = ai_result.get("product_type", UNCERTAIN)
    reason = ai_result.get("reason", "ai_semantic")

    if product_type != UNCERTAIN and product_type not in taxonomy.ids:
        # The model returned something outside the frozen taxonomy -- never
        # trust an invented label, no matter how confident it claims to be
        # (PRINCIPLES.md: controlled taxonomies, not free-form AI invention).
        return ProductTypeResult(
            listing_id=listing.listing_id,
            taxonomy_version=taxonomy.version,
            product_type=UNCERTAIN,
            confidence=0.0,
            reason=f"ai_invalid_label:{product_type}",
            classifier_method="ai",
            review_status="needs_review",
        )

    if confidence >= thresholds["automatic_threshold"]:
        review_status = "auto_accepted"
    elif confidence >= thresholds["sampling_threshold"]:
        review_status = "needs_review"  # kept as stated, flagged -- not forced UNCERTAIN
    else:
        product_type = UNCERTAIN
        review_status = "needs_review"

    return ProductTypeResult(
        listing_id=listing.listing_id,
        taxonomy_version=taxonomy.version,
        product_type=product_type,
        confidence=confidence,
        reason=reason,
        classifier_method="ai",
        review_status=review_status,
    )


# --- Evaluation against manually-labeled examples (M11 Stage 2's explicit
# first validation target: run on the confirmed-relevant listings, compare
# against human labels, inspect mistakes -- not full-dataset classification). ---


@dataclass
class GoldComparisonRow:
    asin: str
    human_type: str
    predicted_type: str
    correct: bool


def evaluate_against_gold(gold: dict[str, str], predictions: dict[str, ProductTypeResult]) -> list[GoldComparisonRow]:
    """`gold`: {asin: human-approved taxonomy id}. `predictions`: {asin:
    ProductTypeResult}. One row per gold ASIN, an unpredicted ASIN counts
    as UNCERTAIN (never silently dropped from the report)."""
    rows = []
    for asin, human_type in sorted(gold.items()):
        result = predictions.get(asin)
        predicted = result.product_type if result is not None else UNCERTAIN
        rows.append(GoldComparisonRow(asin=asin, human_type=human_type, predicted_type=predicted, correct=predicted == human_type))
    return rows


def format_gold_comparison(rows: list[GoldComparisonRow]) -> str:
    lines = [f"{'ASIN':<12}{'Human Type':<16}{'Predicted':<16}{'Correct'}"]
    for r in rows:
        lines.append(f"{r.asin:<12}{r.human_type:<16}{r.predicted_type:<16}{'YES' if r.correct else 'NO'}")
    if rows:
        n_correct = sum(r.correct for r in rows)
        lines.append("")
        lines.append(f"Accuracy: {n_correct}/{len(rows)} ({n_correct / len(rows):.1%})")
    return "\n".join(lines)

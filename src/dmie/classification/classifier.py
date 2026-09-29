"""Hybrid relevance classifier: deterministic rules first, semantic AI
second, confidence thresholding, human review queue for what's left.

See PRINCIPLES.md principle 4 (deterministic calculations) and principle 5
(AI only for semantic judgment) — the rules stage in rules.py is a pure
function; this module's job is orchestration and the AI call, which is
the only non-deterministic part.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from dmie.ai import call_ai
from dmie.classification.rules import RULE_CONFIDENCE, RULES_VERSION, apply_rules

CLASSIFIER_VERSION = "hybrid_v1"
PROMPT_VERSION = "relevance_classifier_v2"
AI_MODEL = "claude-sonnet-4-5"
DEFAULT_CATEGORY = "denture_base"

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "relevance_classifier.md"
_CATEGORIES_PATH = _PROJECT_ROOT / "config" / "categories.yaml"
_THRESHOLDS_PATH = _PROJECT_ROOT / "config" / "thresholds.yaml"

_RELEVANT_BOOL = {"RELEVANT": True, "IRRELEVANT": False, "UNCERTAIN": None}


@dataclass
class ListingContext:
    listing_id: str
    title: str | None
    brand: str | None = None
    bullets: str | None = None
    description: str | None = None
    category: str | None = None
    specifications: str | None = None


@dataclass
class ClassificationResult:
    listing_id: str
    relevant: bool | None
    relevance_class: str
    confidence: float
    reason: str
    product_type: str | None
    classifier_version: str
    prompt_version: str
    review_status: str


@lru_cache(maxsize=None)
def _load_relevance_thresholds() -> dict:
    """3-tier human-review thresholds — from config, empirically derived
    where possible (scripts/calibrate_thresholds.py), not hardcoded
    constants. See docs/human_review_workflow.md. Cached: called once per
    listing classified (up to tens of thousands at real production
    scale) -- re-reading and re-parsing thresholds.yaml that often is
    pure redundant I/O, same class of bug found and fixed in
    dmie.matching.resolution._variant_policy."""
    return yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))["relevance"]


def _tier_for_confidence(confidence: float, thresholds: dict) -> str:
    if confidence >= thresholds["automatic_threshold"]:
        return "auto_accepted"
    if confidence >= thresholds["sampling_threshold"]:
        return "sample_for_qa"
    return "needs_review"


@lru_cache(maxsize=None)
def _load_target_category(category: str) -> str:
    """Category-specific relevance definition, from config, not hardcoded
    here (PRINCIPLES.md principle 9). Cached per category, same reasoning as
    _load_relevance_thresholds."""
    categories = yaml.safe_load(_CATEGORIES_PATH.read_text(encoding="utf-8"))["categories"]
    entry = categories[category]
    header = f"{entry['leaf_category_zh']} / {entry['leaf_category_en']} (Amazon {entry['marketplace']})"
    return f"{header}\n\n{entry['description'].strip()}"


def _build_listing_data(listing: ListingContext) -> str:
    fields = {
        "title": listing.title,
        "brand": listing.brand,
        "category": listing.category,
        "bullets": listing.bullets,
        "description": listing.description,
        "specifications": listing.specifications,
    }
    return json.dumps(fields, indent=2, ensure_ascii=False)


def classify_with_ai(listing: ListingContext, category: str = DEFAULT_CATEGORY) -> dict | None:
    """Calls the configured LLM for a semantic relevance judgment.

    Returns None when no AI provider is configured (no ANTHROPIC_API_KEY,
    or the `anthropic` package isn't installed), or when the call/parse
    ultimately failed after retries (network error, rate limit, timeout,
    an unparseable response — see dmie.ai.client.call_ai) — callers must
    treat None as "AI unavailable", not as a verdict, and route to human
    review. A single failed AI request degrades this one classification,
    it never raises out of this function to crash a batch run (M13).
    """
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{TARGET_CATEGORY}", _load_target_category(category))
        .replace("{LISTING_DATA}", _build_listing_data(listing))
    )

    outcome = call_ai(prompt, model=AI_MODEL, max_tokens=300)
    if not outcome.ok:
        return None
    result = outcome.data

    # Derive `relevant` from `relevance_class` ourselves rather than trust
    # the model's own boolean, so the two fields can never disagree.
    result["relevant"] = _RELEVANT_BOOL.get(result.get("relevance_class"))
    return result


def classify_listing(listing: ListingContext, category: str = DEFAULT_CATEGORY) -> ClassificationResult:
    thresholds = _load_relevance_thresholds()

    # rules.py's patterns are curated specifically for denture_base (see
    # its own module docstring) -- applying them to any other category
    # would be exactly the hardcoded cross-category assumption PRINCIPLES.md
    # principle 9 forbids (e.g. its "adhesive"/"screwdriver" exclusions
    # have no relationship to whether an implants or dental_models
    # listing is relevant). Other categories defer straight to the AI
    # stage, which is already category-aware via _load_target_category.
    rule_result = apply_rules(listing.title or "") if category == DEFAULT_CATEGORY else None
    if rule_result is not None:
        relevance_class, reason = rule_result
        return ClassificationResult(
            listing_id=listing.listing_id,
            relevant=_RELEVANT_BOOL[relevance_class],
            relevance_class=relevance_class,
            confidence=RULE_CONFIDENCE,
            reason=reason,
            product_type=None,
            classifier_version=f"{CLASSIFIER_VERSION}+{RULES_VERSION}",
            prompt_version="n/a",
            review_status=_tier_for_confidence(RULE_CONFIDENCE, thresholds),
        )

    ai_result = classify_with_ai(listing, category=category)
    if ai_result is None:
        return ClassificationResult(
            listing_id=listing.listing_id,
            relevant=None,
            relevance_class="UNCERTAIN",
            confidence=0.0,
            reason="ai_unavailable",
            product_type=None,
            classifier_version=f"{CLASSIFIER_VERSION}+{RULES_VERSION}",
            prompt_version=PROMPT_VERSION,
            review_status="needs_review",
        )

    confidence = float(ai_result.get("confidence", 0.0))
    relevance_class = ai_result.get("relevance_class", "UNCERTAIN")
    tier = _tier_for_confidence(confidence, thresholds)
    if tier == "needs_review":
        # Confidence too low to trust the model's stated class at all --
        # forced to UNCERTAIN, same as the ai_unavailable case above.
        # sample_for_qa keeps the model's answer (it's more likely right
        # than not at that confidence band) but flags it for periodic audit.
        relevance_class = "UNCERTAIN"

    return ClassificationResult(
        listing_id=listing.listing_id,
        relevant=_RELEVANT_BOOL[relevance_class],
        relevance_class=relevance_class,
        confidence=confidence,
        reason=ai_result.get("reason", "ai_semantic"),
        product_type=ai_result.get("product_type"),
        classifier_version=f"{CLASSIFIER_VERSION}+{RULES_VERSION}",
        prompt_version=PROMPT_VERSION,
        review_status=tier,
    )

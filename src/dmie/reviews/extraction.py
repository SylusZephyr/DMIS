"""Stage 1: review extraction, with a hard evidence-verification
guardrail. See config/review_taxonomy.yaml and prompts/review_analysis.md.

PRINCIPLES.md principle 5: AI is for semantic judgment (reading the review),
never for arithmetic — frequency/severity aggregation lives in
aggregation.py as pure Python, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dmie.ai import call_ai
from dmie.reviews.themes import load_taxonomy, validate_taxonomy_path

EXTRACTION_VERSION = "review_extraction_v1"
PROMPT_VERSION = "review_analysis_v1"
AI_MODEL = "claude-sonnet-4-5"

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "review_analysis.md"


@dataclass
class ReviewInsight:
    listing_id: str
    product_id: str | None
    pain_point_category: str | None
    pain_point_subcategory: str | None
    affected_attribute: str | None
    severity: int | None
    customer_complaint: str | None
    evidence_text: str | None
    improvement_opportunity: str | None
    confidence: float
    model_version: str
    prompt_version: str
    status: str  # extracted | no_pain_point | rejected_no_evidence | rejected_invalid_taxonomy | ai_unavailable
    rejection_reason: str | None = None


def _build_prompt(review_text: str) -> str:
    taxonomy = load_taxonomy()
    taxonomy_lines = [f"{category}: {', '.join(subcats)}" for category, subcats in taxonomy.items()]
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{TAXONOMY}", "\n".join(taxonomy_lines)).replace("{REVIEW}", review_text)


def extract_with_ai(review_text: str) -> dict | None:
    """Returns None (not a guess) when no AI provider is configured, or
    when the call/parse ultimately failed after retries (M13) -- same
    graceful-degradation pattern as classifier.py/resolution.py. A single
    failed AI request degrades this one review, it never raises out of
    this function to crash a batch extraction run."""
    prompt = _build_prompt(review_text)
    outcome = call_ai(prompt, model=AI_MODEL, max_tokens=500)
    return outcome.data if outcome.ok else None


def verify_evidence(evidence: str | None, review_text: str) -> bool:
    """Hard guardrail: extracted evidence must be an actual substring of
    the source review (case/whitespace tolerant), never a paraphrase or
    invention. This is the concrete enforcement of 'extract only claims
    supported by the supplied review' — not just a prompt instruction."""
    if not evidence:
        return False

    def normalize(text: str) -> str:
        return " ".join(text.lower().split())

    return normalize(evidence) in normalize(review_text)


def _rejected(listing_id, product_id, ai_result, status, reason) -> ReviewInsight:
    return ReviewInsight(
        listing_id=listing_id, product_id=product_id,
        pain_point_category=ai_result.get("pain_point_category"),
        pain_point_subcategory=ai_result.get("pain_point_subcategory"),
        affected_attribute=ai_result.get("affected_attribute"),
        severity=ai_result.get("severity"),
        customer_complaint=ai_result.get("customer_complaint"),
        evidence_text=ai_result.get("evidence"),
        improvement_opportunity=ai_result.get("potential_improvement"),
        confidence=0.0,
        model_version=EXTRACTION_VERSION, prompt_version=PROMPT_VERSION,
        status=status, rejection_reason=reason,
    )


def extract_insight(listing_id: str, product_id: str | None, review_text: str) -> ReviewInsight:
    ai_result = extract_with_ai(review_text)
    if ai_result is None:
        return ReviewInsight(
            listing_id=listing_id, product_id=product_id,
            pain_point_category=None, pain_point_subcategory=None, affected_attribute=None,
            severity=None, customer_complaint=None, evidence_text=None, improvement_opportunity=None,
            confidence=0.0, model_version=EXTRACTION_VERSION, prompt_version=PROMPT_VERSION,
            status="ai_unavailable", rejection_reason="no AI provider configured",
        )

    if ai_result.get("no_pain_point_found"):
        return ReviewInsight(
            listing_id=listing_id, product_id=product_id,
            pain_point_category=None, pain_point_subcategory=None, affected_attribute=None,
            severity=None, customer_complaint=None, evidence_text=None, improvement_opportunity=None,
            confidence=1.0, model_version=EXTRACTION_VERSION, prompt_version=PROMPT_VERSION,
            status="no_pain_point",
        )

    evidence = ai_result.get("evidence")
    if not verify_evidence(evidence, review_text):
        return _rejected(listing_id, product_id, ai_result, "rejected_no_evidence",
                          "extracted evidence is not a verbatim substring of the review")

    category, subcategory = ai_result.get("pain_point_category"), ai_result.get("pain_point_subcategory")
    if not validate_taxonomy_path(category, subcategory):
        return _rejected(listing_id, product_id, ai_result, "rejected_invalid_taxonomy",
                          f"'{category}/{subcategory}' is not in the controlled taxonomy")

    return ReviewInsight(
        listing_id=listing_id, product_id=product_id,
        pain_point_category=category, pain_point_subcategory=subcategory,
        affected_attribute=ai_result.get("affected_attribute"),
        severity=ai_result.get("severity"),
        customer_complaint=ai_result.get("customer_complaint"),
        evidence_text=evidence,
        improvement_opportunity=ai_result.get("potential_improvement"),
        confidence=float(ai_result.get("confidence", 0.0)),
        model_version=EXTRACTION_VERSION, prompt_version=PROMPT_VERSION,
        status="extracted",
    )

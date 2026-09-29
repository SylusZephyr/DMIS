"""Synthetic example reviews only -- this dataset has no real review text
(see docs/data_dictionary.md #12 and scripts/analyze_reviews.py)."""

from unittest.mock import patch

from dmie.reviews.extraction import extract_insight, verify_evidence

SYNTHETIC_REVIEW = (
    "I bought this wax for my dental lab work. Unfortunately the sheet "
    "cracked into pieces the very first time I tried to shape it, even "
    "handling it gently. Very disappointing for the price."
)


def test_verify_evidence_accepts_verbatim_substring():
    assert verify_evidence("cracked into pieces the very first time", SYNTHETIC_REVIEW) is True


def test_verify_evidence_is_case_and_whitespace_tolerant():
    assert verify_evidence("CRACKED   into pieces", SYNTHETIC_REVIEW) is True


def test_verify_evidence_rejects_paraphrase():
    assert verify_evidence("the product broke easily", SYNTHETIC_REVIEW) is False


def test_verify_evidence_rejects_empty():
    assert verify_evidence(None, SYNTHETIC_REVIEW) is False
    assert verify_evidence("", SYNTHETIC_REVIEW) is False


def test_extract_insight_ai_unavailable_is_flagged_not_guessed():
    with patch("dmie.reviews.extraction.extract_with_ai", return_value=None):
        result = extract_insight("L1", "P1", SYNTHETIC_REVIEW)
    assert result.status == "ai_unavailable"
    assert result.pain_point_category is None
    assert result.confidence == 0.0


def test_extract_insight_valid_ai_output_is_accepted():
    ai_output = {
        "pain_point_category": "QUALITY",
        "pain_point_subcategory": "breakage",
        "affected_attribute": "wax sheet",
        "severity": 3,
        "customer_complaint": "the wax sheet cracked on first use",
        "evidence": "cracked into pieces the very first time I tried to shape it",
        "potential_improvement": "improve wax sheet flexibility/durability",
        "confidence": 0.9,
    }
    with patch("dmie.reviews.extraction.extract_with_ai", return_value=ai_output):
        result = extract_insight("L1", "P1", SYNTHETIC_REVIEW)
    assert result.status == "extracted"
    assert result.pain_point_category == "QUALITY"
    assert result.pain_point_subcategory == "breakage"
    assert result.evidence_text == ai_output["evidence"]
    assert result.confidence == 0.9


def test_extract_insight_rejects_fabricated_evidence():
    """The model claims a quote that isn't actually in the review --
    must be rejected, not stored as a confident insight."""
    ai_output = {
        "pain_point_category": "QUALITY",
        "pain_point_subcategory": "breakage",
        "affected_attribute": "wax sheet",
        "severity": 3,
        "customer_complaint": "fabricated complaint",
        "evidence": "this exact phrase does not appear anywhere in the review",
        "potential_improvement": "n/a",
        "confidence": 0.9,
    }
    with patch("dmie.reviews.extraction.extract_with_ai", return_value=ai_output):
        result = extract_insight("L1", "P1", SYNTHETIC_REVIEW)
    assert result.status == "rejected_no_evidence"
    assert result.confidence == 0.0


def test_extract_insight_rejects_invented_taxonomy_category():
    ai_output = {
        "pain_point_category": "RELIABILITY",  # not in the controlled taxonomy
        "pain_point_subcategory": "breakage",
        "affected_attribute": "wax sheet",
        "severity": 3,
        "customer_complaint": "cracked",
        "evidence": "cracked into pieces the very first time I tried to shape it",
        "potential_improvement": "n/a",
        "confidence": 0.9,
    }
    with patch("dmie.reviews.extraction.extract_with_ai", return_value=ai_output):
        result = extract_insight("L1", "P1", SYNTHETIC_REVIEW)
    assert result.status == "rejected_invalid_taxonomy"


def test_extract_insight_no_pain_point_found():
    with patch("dmie.reviews.extraction.extract_with_ai", return_value={"no_pain_point_found": True}):
        result = extract_insight("L1", "P1", "This product is great, works perfectly!")
    assert result.status == "no_pain_point"
    assert result.pain_point_category is None

from unittest.mock import patch

from dmie.classification.classifier import (
    ListingContext,
    _build_listing_data,
    _load_relevance_thresholds,
    _load_target_category,
    _tier_for_confidence,
    classify_listing,
)


def _ctx(title: str) -> ListingContext:
    return ListingContext(listing_id="L1", title=title)


def test_rule_hit_bypasses_ai_entirely():
    with patch("dmie.classification.classifier.classify_with_ai") as mock_ai:
        result = classify_listing(_ctx("Antinsky Denture Base Resin"))
    mock_ai.assert_not_called()
    assert result.relevance_class == "RELEVANT"
    assert result.relevant is True
    assert result.confidence == 0.95
    assert result.review_status == "auto_accepted"


def test_ai_unavailable_falls_back_to_uncertain_needs_review():
    with patch("dmie.classification.classifier.classify_with_ai", return_value=None):
        result = classify_listing(_ctx("Some ambiguous listing with no rule match"))
    assert result.relevance_class == "UNCERTAIN"
    assert result.relevant is None
    assert result.reason == "ai_unavailable"
    assert result.review_status == "needs_review"


def test_very_confident_ai_result_is_auto_accepted():
    """>= automatic_threshold (0.95) -> auto_accepted."""
    ai_output = {"relevance_class": "RELEVANT", "confidence": 0.97, "reason": "EXACT_MATCH", "product_type": "base_wax"}
    with patch("dmie.classification.classifier.classify_with_ai", return_value=ai_output):
        result = classify_listing(_ctx("Some ambiguous listing with no rule match"))
    assert result.relevance_class == "RELEVANT"
    assert result.relevant is True
    assert result.confidence == 0.97
    assert result.product_type == "base_wax"
    assert result.review_status == "auto_accepted"


def test_mid_confidence_ai_result_is_sampled_for_qa_not_auto_accepted():
    """3-tier routing: sampling_threshold (0.80) <= confidence < automatic_threshold
    (0.95) -> sample_for_qa. The model's stated class is kept (more likely
    right than not at this band), just flagged for periodic audit -- unlike
    needs_review, which forces UNCERTAIN."""
    ai_output = {"relevance_class": "RELEVANT", "confidence": 0.9, "reason": "EXACT_MATCH", "product_type": "base_wax"}
    with patch("dmie.classification.classifier.classify_with_ai", return_value=ai_output):
        result = classify_listing(_ctx("Some ambiguous listing with no rule match"))
    assert result.relevance_class == "RELEVANT"  # kept, not forced to UNCERTAIN
    assert result.confidence == 0.9
    assert result.review_status == "sample_for_qa"


def test_low_confidence_ai_result_is_forced_uncertain():
    """< sampling_threshold (0.80) -> needs_review, relevance_class forced
    to UNCERTAIN since confidence is too low to trust the stated class at all."""
    ai_output = {"relevance_class": "IRRELEVANT", "confidence": 0.4, "reason": "WRONG_CATEGORY"}
    with patch("dmie.classification.classifier.classify_with_ai", return_value=ai_output):
        result = classify_listing(_ctx("Some ambiguous listing with no rule match"))
    assert result.relevance_class == "UNCERTAIN"
    assert result.relevant is None
    assert result.review_status == "needs_review"


def test_structured_output_has_all_required_fields():
    with patch("dmie.classification.classifier.classify_with_ai", return_value=None):
        result = classify_listing(_ctx("Something with no rule match"))
    for field in ("listing_id", "relevant", "relevance_class", "confidence",
                   "reason", "product_type", "classifier_version", "prompt_version"):
        assert hasattr(result, field)


def test_target_category_loaded_from_config_not_hardcoded():
    description = _load_target_category("denture_base")
    assert "denture base" in description.lower()
    assert "adhesive" in description.lower()  # exclusion text made it in


def test_relevance_thresholds_loaded_from_real_config():
    thresholds = _load_relevance_thresholds()
    assert thresholds["automatic_threshold"] == 0.95
    assert thresholds["sampling_threshold"] == 0.80


def test_tier_for_confidence_three_way_split():
    thresholds = {"automatic_threshold": 0.95, "sampling_threshold": 0.80}
    assert _tier_for_confidence(0.97, thresholds) == "auto_accepted"
    assert _tier_for_confidence(0.95, thresholds) == "auto_accepted"  # boundary is inclusive
    assert _tier_for_confidence(0.90, thresholds) == "sample_for_qa"
    assert _tier_for_confidence(0.80, thresholds) == "sample_for_qa"  # boundary is inclusive
    assert _tier_for_confidence(0.79, thresholds) == "needs_review"
    assert _tier_for_confidence(0.0, thresholds) == "needs_review"


def test_listing_data_is_valid_json_with_all_fields():
    import json
    ctx = ListingContext(listing_id="L1", title="Some Title", brand="Acme")
    data = json.loads(_build_listing_data(ctx))
    assert data["title"] == "Some Title"
    assert data["brand"] == "Acme"
    assert data["bullets"] is None

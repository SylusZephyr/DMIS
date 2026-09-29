from dmie.opportunity.evidence import build_signal_record, build_supporting_metrics, build_supporting_review_themes
from dmie.opportunity.signals import OpportunitySignal, SignalCondition


def _product_signal():
    return OpportunitySignal("PRODUCT_IMPROVEMENT", "denture_base", "p1", [
        SignalCondition("high_demand", True, 500.0, 100.0, "top 25% of category"),
        SignalCondition("many_listings", True, 6, 3, ">= 3 listings"),
        SignalCondition("high_complaint_frequency", True, 5, 3, ">= 3 complaints"),
    ], sample_size=5, confidence_reference_n=10)


def test_build_signal_record_has_exactly_the_required_fields():
    record = build_signal_record(_product_signal(), {"total_listing_count": 6}, [])
    for field in ("product_id", "signal_type", "signal_strength", "evidence",
                  "supporting_metrics", "supporting_review_themes", "confidence"):
        assert field in record
    assert record["product_id"] == "p1"
    assert record["signal_type"] == "PRODUCT_IMPROVEMENT"


def test_supporting_metrics_excludes_complaint_condition():
    metrics = build_supporting_metrics(_product_signal())
    names = {m["name"] for m in metrics}
    assert "high_demand" in names
    assert "many_listings" in names
    assert "high_complaint_frequency" not in names  # goes in supporting_review_themes instead


def test_supporting_review_themes_includes_quoted_evidence_not_just_a_count():
    signal = _product_signal()
    review_insights = [
        {"status": "extracted", "pain_point_category": "QUALITY", "pain_point_subcategory": "breakage",
         "severity": 4, "evidence_text": "cracked on first use"},
        {"status": "extracted", "pain_point_category": "QUALITY", "pain_point_subcategory": "breakage",
         "severity": 5, "evidence_text": "snapped in half within a week"},
    ]
    result = build_supporting_review_themes(signal, review_insights)
    assert result["themes"][0]["theme"] == "QUALITY/breakage"
    assert result["themes"][0]["frequency"] == 2
    assert "cracked on first use" in result["themes"][0]["sample_evidence"]


def test_supporting_review_themes_empty_when_no_complaint_condition():
    signal = OpportunitySignal("COMPETITIVE_CONCENTRATION", "denture_base", None, [
        SignalCondition("listing_concentration_hhi", True, 3000.0, 2500.0, "d"),
    ])
    assert build_supporting_review_themes(signal, []) == []


def test_category_level_signal_has_none_product_id_and_evidence():
    signal = OpportunitySignal("PRICE_SEGMENT", "denture_base", None, [
        SignalCondition("empty_price_band[$100-250]", True, 0, 0, "d", direction="low"),
    ])
    record = build_signal_record(signal, {}, [])
    assert record["product_id"] is None
    assert record["evidence"]["product_metrics_snapshot"] is None

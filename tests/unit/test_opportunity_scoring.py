"""Tier 2, Milestone 9 -- Opportunity Score. Every case here mirrors a
real combination opportunity_signals rows can actually have -- see
src/dmie/opportunity/signals.py's status/signal_strength/confidence
semantics, which this module aggregates without recomputing.
"""

from dmie.opportunity.scoring import STRENGTH_WEIGHTS, compute_opportunity_score


def _signal(signal_type="PRODUCT_IMPROVEMENT", status="signal_present", strength=None, confidence=None):
    return {"signal_type": signal_type, "status": status, "signal_strength": strength, "confidence": confidence}


def test_no_signals_returns_none_not_zero():
    assert compute_opportunity_score([]) is None


def test_all_insufficient_data_returns_none():
    records = [
        _signal("PRODUCT_IMPROVEMENT", "insufficient_data"),
        _signal("BUNDLE", "insufficient_data"),
    ]
    assert compute_opportunity_score(records) is None


def test_single_signal_absent_scores_zero():
    records = [_signal("PRODUCT_IMPROVEMENT", "signal_absent")]
    assert compute_opportunity_score(records) == 0.0


def test_single_high_strength_full_confidence_scores_the_high_weight():
    records = [_signal("PRODUCT_IMPROVEMENT", "signal_present", "HIGH", 1.0)]
    assert compute_opportunity_score(records) == STRENGTH_WEIGHTS["HIGH"]


def test_confidence_scales_the_contribution_down():
    records = [_signal("PRODUCT_IMPROVEMENT", "signal_present", "HIGH", 0.5)]
    assert compute_opportunity_score(records) == STRENGTH_WEIGHTS["HIGH"] * 0.5


def test_insufficient_data_signals_are_excluded_not_averaged_as_zero():
    """A product with one confirmed HIGH signal and one not-yet-evaluated
    signal should score as if the unevaluated one weren't there at all --
    not get dragged down by treating it as 0."""
    with_unevaluated = [
        _signal("PRODUCT_IMPROVEMENT", "signal_present", "HIGH", 1.0),
        _signal("BUNDLE", "insufficient_data"),
    ]
    without_unevaluated = [_signal("PRODUCT_IMPROVEMENT", "signal_present", "HIGH", 1.0)]
    assert compute_opportunity_score(with_unevaluated) == compute_opportunity_score(without_unevaluated)


def test_mixed_present_and_absent_signals_average_correctly():
    records = [
        _signal("PRODUCT_IMPROVEMENT", "signal_present", "HIGH", 1.0),  # 90
        _signal("BUNDLE", "signal_absent"),                              # 0
    ]
    assert compute_opportunity_score(records) == 45.0


def test_multiple_customer_pain_point_rows_each_contribute():
    records = [
        _signal("CUSTOMER_PAIN_POINT", "signal_present", "LOW", 1.0),
        _signal("CUSTOMER_PAIN_POINT", "signal_present", "MEDIUM", 1.0),
    ]
    expected = (STRENGTH_WEIGHTS["LOW"] + STRENGTH_WEIGHTS["MEDIUM"]) / 2
    assert compute_opportunity_score(records) == expected


def test_category_scoped_signal_types_are_ignored():
    """PRICE_SEGMENT/UNDERREPRESENTED_PRODUCT_TYPE/COMPETITIVE_CONCENTRATION
    describe the category, not one product (product_id is None on those
    rows) -- they must never leak into a per-product score."""
    records = [
        _signal("PRICE_SEGMENT", "signal_present", "HIGH", 1.0),
        _signal("COMPETITIVE_CONCENTRATION", "signal_present", "HIGH", 1.0),
    ]
    assert compute_opportunity_score(records) is None


def test_score_is_bounded_0_to_100():
    records = [_signal("PRODUCT_IMPROVEMENT", "signal_present", "HIGH", 1.0)] * 5
    score = compute_opportunity_score(records)
    assert 0.0 <= score <= 100.0

"""Synthetic examples only, built to exercise the six named signal types
exactly. Real denture_base data doesn't currently have enough demand/
review coverage to fire PRODUCT_IMPROVEMENT/BUNDLE/CUSTOMER_PAIN_POINT
for real (see PROGRESS.md) -- these tests validate the logic, not real
findings. PRICE_SEGMENT and COMPETITIVE_CONCENTRATION do fire on real
data -- see tests/integration/test_opportunity_pipeline.py."""

from dmie.opportunity.signals import (
    HHI_HIGH_CONCENTRATION_THRESHOLD,
    OpportunitySignal,
    SignalCondition,
    detect_bundle_signal,
    detect_competitive_concentration_signal,
    detect_customer_pain_point_signal,
    detect_price_segment_signal,
    detect_product_improvement_signal,
    detect_underrepresented_product_type_signal,
    has_accessory_activity,
    has_high_complaint_frequency,
    has_limited_bundle_offerings,
    has_many_listings,
    has_low_differentiation,
    is_high_demand,
    looks_like_bundle,
    price_coefficient_of_variation,
)

THRESHOLDS = {
    "demand_percentile": 0.75,
    "min_products_for_percentile": 4,
    "many_listings_threshold": 3,
    "high_complaint_count_threshold": 3,
    "high_complaint_severity_threshold": 3.5,
    "low_differentiation_price_cov_threshold": 0.15,
    "accessory_activity_threshold": 2,
    "limited_bundle_offering_threshold": 0.2,
}

CATEGORY_DEMAND = [500.0, 100.0, 80.0, 60.0, 50.0]  # 500 is clearly top-25%
TIGHT_PRICES = [20.0, 21.0, 19.0, 20.0, 22.0]  # low CoV -- commodity market


# --- individual condition functions ---

def test_is_high_demand_true_for_top_performer():
    product = {"best_listing_observed_monthly_sales": 500.0, "observed_monthly_revenue": None}
    assert is_high_demand(product, CATEGORY_DEMAND, 0.75, 4).met is True


def test_is_high_demand_false_for_bottom_performer():
    product = {"best_listing_observed_monthly_sales": 50.0, "observed_monthly_revenue": None}
    assert is_high_demand(product, CATEGORY_DEMAND, 0.75, 4).met is False


def test_is_high_demand_none_when_product_has_no_data():
    product = {"best_listing_observed_monthly_sales": None, "observed_monthly_revenue": None}
    assert is_high_demand(product, CATEGORY_DEMAND, 0.75, 4).met is None


def test_is_high_demand_none_when_category_sample_too_small():
    product = {"best_listing_observed_monthly_sales": 500.0, "observed_monthly_revenue": None}
    assert is_high_demand(product, [500.0], 0.75, 4).met is None


def test_is_high_demand_zero_sales_is_not_treated_as_missing():
    product = {"best_listing_observed_monthly_sales": 0.0, "observed_monthly_revenue": 500.0}
    cond = is_high_demand(product, [0.0, 1.0, 2.0, 3.0], 0.75, 4)
    assert cond.value == 0.0


def test_has_many_listings():
    assert has_many_listings({"total_listing_count": 5}, 3).met is True
    assert has_many_listings({"total_listing_count": 1}, 3).met is False


def test_has_high_complaint_frequency_by_count():
    assert has_high_complaint_frequency(5, 2.0, 3, 3.5).met is True


def test_has_high_complaint_frequency_none_when_no_insights():
    assert has_high_complaint_frequency(0, None, 3, 3.5).met is None


def test_price_coefficient_of_variation_tight_prices_is_low():
    assert price_coefficient_of_variation(TIGHT_PRICES) < 0.15


def test_has_low_differentiation_true_for_tight_prices():
    cov = price_coefficient_of_variation(TIGHT_PRICES)
    assert has_low_differentiation(cov, 0.15).met is True


def test_has_accessory_activity():
    assert has_accessory_activity(3, 2).met is True
    assert has_accessory_activity(1, 2).met is False


def test_has_limited_bundle_offerings():
    assert has_limited_bundle_offerings(0.1, 0.2).met is True
    assert has_limited_bundle_offerings(0.5, 0.2).met is False


def test_looks_like_bundle_matches_real_bundle_language():
    assert looks_like_bundle("Denture Kit Bundle with Cleaning Tablets") is True
    assert looks_like_bundle("3-in-1 Denture Care Combo") is True


def test_looks_like_bundle_does_not_match_generic_kit():
    assert looks_like_bundle("Denture Repair Kit") is False
    assert looks_like_bundle("Dental Base Plate Wax Sheets Set") is False


# --- OpportunitySignal: status / strength / confidence ---

def test_signal_type_must_be_in_closed_taxonomy():
    import pytest
    with pytest.raises(AssertionError):
        OpportunitySignal("NOT_A_REAL_TYPE", "c1", "p1", [])


def test_insufficient_data_is_distinguishable_from_signal_absent():
    absent = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [
        SignalCondition("a", False, 1, 2, "d"), SignalCondition("b", True, 1, 2, "d"),
    ])
    unknown = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [
        SignalCondition("a", None, None, 2, "d"), SignalCondition("b", True, 1, 2, "d"),
    ])
    assert absent.status == "signal_absent"
    assert unknown.status == "insufficient_data"


def test_signal_strength_is_none_unless_signal_present():
    absent = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", False, 1, 2, "d")])
    unknown = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", None, None, 2, "d")])
    assert absent.signal_strength is None
    assert unknown.signal_strength is None


def test_signal_strength_scales_with_margin_over_threshold():
    low = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", True, 105.0, 100.0, "d")])
    high = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", True, 500.0, 100.0, "d")])
    assert low.signal_strength == "LOW"
    assert high.signal_strength == "HIGH"


def test_confidence_is_zero_when_insufficient_data():
    signal = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", None, None, 2, "d")],
                                sample_size=100, confidence_reference_n=10)
    assert signal.confidence == 0.0


def test_confidence_scales_with_sample_size():
    small = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", True, 5, 2, "d")],
                               sample_size=1, confidence_reference_n=10)
    large = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", True, 5, 2, "d")],
                               sample_size=10, confidence_reference_n=10)
    assert small.confidence == 0.1
    assert large.confidence == 1.0


def test_confidence_and_strength_are_independent_concepts():
    """A signal can have high confidence (lots of data) but low strength
    (barely over threshold), or vice versa."""
    signal = OpportunitySignal("PRODUCT_IMPROVEMENT", "c1", "p1", [SignalCondition("a", True, 101.0, 100.0, "d")],
                                sample_size=50, confidence_reference_n=10)
    assert signal.signal_strength == "LOW"
    assert signal.confidence == 1.0


# --- the 6 named signal types, matching the brief's patterns ---

def test_product_improvement_fires_on_strong_demand_many_listings_high_complaints():
    product = {"category_id": "c1", "product_id": "p1", "best_listing_observed_monthly_sales": 500.0,
               "observed_monthly_revenue": None, "total_listing_count": 6}
    signal = detect_product_improvement_signal(product, CATEGORY_DEMAND, insight_count=4, mean_severity=2.5, thresholds=THRESHOLDS)
    assert signal.signal_type == "PRODUCT_IMPROVEMENT"
    assert signal.status == "signal_present"


def test_bundle_fires_on_core_demand_accessory_activity_limited_bundles():
    product = {"category_id": "c1", "product_id": "p1", "best_listing_observed_monthly_sales": 500.0, "observed_monthly_revenue": None}
    signal = detect_bundle_signal(product, CATEGORY_DEMAND, accessory_listing_count=3, bundle_listing_fraction=0.05, thresholds=THRESHOLDS)
    assert signal.signal_type == "BUNDLE"
    assert signal.status == "signal_present"


def test_customer_pain_point_fires_standalone_without_demand_data():
    """Unlike PRODUCT_IMPROVEMENT, this doesn't need demand/listing-count
    conditions -- just a significant complaint theme on its own."""
    signal = detect_customer_pain_point_signal("c1", "p1", "QUALITY/breakage", frequency=5, mean_severity=3.0, thresholds=THRESHOLDS)
    assert signal.signal_type == "CUSTOMER_PAIN_POINT"
    assert signal.status == "signal_present"
    assert signal.product_id == "p1"


def test_price_segment_fires_on_empty_band_with_enough_category_data():
    signal = detect_price_segment_signal("c1", "$100-250", band_count=0, total_priced_products=11, min_n=4)
    assert signal.signal_type == "PRICE_SEGMENT"
    assert signal.status == "signal_present"
    assert signal.product_id is None  # category-level, not tied to an existing product


def test_price_segment_absent_when_band_has_products():
    signal = detect_price_segment_signal("c1", "<$20", band_count=8, total_priced_products=11, min_n=4)
    assert signal.status == "signal_absent"


def test_price_segment_insufficient_data_with_too_few_priced_products():
    signal = detect_price_segment_signal("c1", "$100-250", band_count=0, total_priced_products=2, min_n=4)
    assert signal.status == "insufficient_data"


def test_underrepresented_product_type_insufficient_when_mostly_unclassified():
    """Real project state: product-type classification hasn't been built
    (Milestone 5), so this must report insufficient_data, not a guess."""
    signal = detect_underrepresented_product_type_signal("c1", {"unclassified": 11})
    assert signal.signal_type == "UNDERREPRESENTED_PRODUCT_TYPE"
    assert signal.status == "insufficient_data"
    assert signal.product_id is None


def test_underrepresented_product_type_evaluates_with_real_type_data():
    signal = detect_underrepresented_product_type_signal("c1", {"wax": 5, "resin": 5, "unclassified": 1})
    assert signal.status != "insufficient_data"


def test_competitive_concentration_fires_above_dojftc_threshold():
    signal = detect_competitive_concentration_signal("c1", HHI_HIGH_CONCENTRATION_THRESHOLD + 100, total_listings=20)
    assert signal.signal_type == "COMPETITIVE_CONCENTRATION"
    assert signal.status == "signal_present"
    assert signal.product_id is None


def test_competitive_concentration_absent_below_threshold():
    """Real pilot value: HHI ~972, well below the 2500 threshold."""
    signal = detect_competitive_concentration_signal("c1", 972.2, total_listings=12)
    assert signal.status == "signal_absent"

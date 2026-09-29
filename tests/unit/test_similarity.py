from dmie.matching.similarity import (
    attribute_score,
    brand_score,
    extract_quantities,
    normalize_brand,
    title_similarity,
)


def test_normalize_brand_treats_generic_as_unknown():
    assert normalize_brand("Generic") == "generic"
    assert normalize_brand("") is None
    assert normalize_brand(None) is None
    assert normalize_brand("Antinsky") == "antinsky"


def test_brand_score_same_specific_brand_is_high():
    assert brand_score("Antinsky", "antinsky") == 1.0


def test_brand_score_different_specific_brands_is_low():
    assert brand_score("Mzcarewr", "XLMCWT") == 0.3


def test_brand_score_generic_or_missing_is_neutral():
    assert brand_score("Generic", "Antinsky") == 0.6
    assert brand_score(None, "Antinsky") == 0.6


def test_title_similarity_identical_is_one():
    assert title_similarity("Denture Base Resin", "Denture Base Resin") == 1.0


def test_title_similarity_empty_is_zero():
    assert title_similarity("", "Denture Base Resin") == 0.0
    assert title_similarity(None, None) == 0.0


def test_attribute_score_close_prices_is_high():
    assert attribute_score(33.99, 33.99) == 1.0
    assert attribute_score(10.0, 10.5) > 0.9


def test_attribute_score_far_apart_prices_is_low():
    assert attribute_score(22.99, 39.99) < 0.6


def test_attribute_score_missing_price_is_neutral():
    assert attribute_score(None, 10.0) == 0.5


def test_extract_quantities_number_before_unit():
    assert extract_quantities("D.O.C. Repair-It Kit 3 ea") == {"3"}


def test_extract_quantities_pack_of_n():
    assert "2" in extract_quantities("Repair Kit (Pack of 2)")


def test_extract_quantities_both_orders_in_one_title():
    qty = extract_quantities("Repair Kit 3 ea (Pack of 2)")
    assert qty == {"3", "2"}


def test_extract_quantities_none_found_is_empty_set():
    assert extract_quantities("Denture Base Resin") == set()


# --- Vocabulary widened after docs/entity_resolution_evaluation_report.md
# false-merge #3: "bottle" wasn't recognized at all, so a real pack-count
# mismatch against a "1 Box" listing went undetected. ---

def test_extract_quantities_recognizes_bottle():
    assert extract_quantities("Kit (White 2 Bottle)") == {"2"}


def test_extract_quantities_recognizes_set():
    assert extract_quantities("Full Denture 3 Set") == {"3"}


def test_extract_quantities_recognizes_count_and_ct():
    assert extract_quantities("Repair Kit 1 Pack (4 Count)") == {"1", "4"}
    assert extract_quantities("Repair Kit (8 ct)") == {"8"}


def test_extract_quantities_bottle_vs_box_are_now_disjoint():
    """The real false-merge case: 'White 1 Box' vs 'White 2 Bottle' used
    to extract {'1'} vs {} (bottle unrecognized), so the pack-variant
    guard (which requires both sides non-empty) never even ran."""
    box = extract_quantities("...Suitable for Missing, Cracked DIY Denture Kit. (White 1 Box)")
    bottle = extract_quantities("...Suitable for Missing, Cracked DIY Denture Kit. (White 2 Bottle)")
    assert box == {"1"}
    assert bottle == {"2"}
    assert box.isdisjoint(bottle)

"""Tests grounded in the 5 manually-reviewed pairs documented in
docs/entity_resolution.md. These are real ASINs from the denture_base
pilot, not synthetic examples."""

from unittest.mock import patch

import pytest

from dmie.matching.resolution import (
    CandidateResult,
    build_product_listings,
    build_products,
    cluster_products,
    exact_identifier_match,
    matching_breakdown,
    resolve_pair,
)

# --- fixtures: real listing data, grounded in docs/entity_resolution.md ---

CONFIRMED_A = {"listing_id": "L1", "title": "Ultrasonic Retainer Cleaner Machine with 4 Modes – 45kHz 180ML Ultrasonic Cleaner for Retainer,Denture,Mouth Guard,Ring,Jewelry,Leak-Proof Detachable Tank & Base, Easy-to-Clean", "brand": "JEDIA", "price": 33.99}
CONFIRMED_B = {"listing_id": "L2", "title": "Ultrasonic Retainer Cleaner Machine with 4 Modes – 45kHz 180ML Ultrasonic Cleaner for Retainer,Denture,Mouth Guard,Ring,Jewelry,Leak-Proof Detachable Tank & Base, Easy-to-Clean", "brand": "JEDIA", "price": 33.99}

DIFFERENT_BRAND_A = {"listing_id": "L3", "title": "Provisional teeth decoration Tooth Tamporary Repair Tooth Kit Fake Tooth High hardness, durable, and reusable. Suitable for Missing, Cracked DIY Denture Kit. (White 1 Box)", "brand": "yuanyuuo", "price": 5.49}
DIFFERENT_BRAND_B = {"listing_id": "L4", "title": "Provisional teeth decoration Tooth Tamporary Repair Tooth Kit Fake Tooth High hardness, durable, and reusable. Suitable for Missing, Cracked DIY Denture Kit. (2 Box)", "brand": "Generic", "price": 8.49}

COLOR_VARIANT_A = {"listing_id": "L5", "title": "DIY Denture kit Convenient and Easy DIY dentures kit at Home Complete Make Your own dentures Kits for top and Bottom of Temporary Repair Missing Teeth pink", "brand": "Asettlekit", "price": 22.99}
COLOR_VARIANT_B = {"listing_id": "L6", "title": "DIY Denture kit Convenient and Easy DIY dentures kit at Home Complete Make Your own dentures Kits for top and Bottom of Temporary Repair Missing Teeth Flesh Colored", "brand": "Asettlekit", "price": 39.99}

BUNDLE_A = {"listing_id": "L7", "title": "D.O.C. Repair-It Advanced Formula Denture Repair Kit 3 ea", "brand": "Doc", "price": 9.88}
BUNDLE_B = {"listing_id": "L8", "title": "D.O.C. Repair-It Advanced Formula Denture Repair Kit 3 ea (Pack of 2)", "brand": "Doc", "price": 16.75}

# Real false-merge #3 from docs/entity_resolution_evaluation_report.md:
# same brand, near-identical title, but "1 Box" vs "2 Bottle" is a real
# pack-count difference. Used to auto-MATCH at 0.927 because "bottle"
# wasn't in extract_quantities' unit vocabulary at all.
BOX_BOTTLE_A = {"listing_id": "B0GVB7J6PV", "title": "Provisional teeth decoration Tooth Tamporary Repair Tooth Kit Fake Tooth High hardness, durable, and reusable. Suitable for Missing, Cracked DIY Denture Kit. (White 1 Box)", "brand": "yuanyuuo", "price": 5.49}
BOX_BOTTLE_B = {"listing_id": "B0GVB48DSX", "title": "Provisional teeth decoration Tooth Tamporary Repair Tooth Kit Fake Tooth High hardness, durable, and reusable. Suitable for Missing, Cracked DIY Denture Kit. (White 2 Bottle)", "brand": "yuanyuuo", "price": 7.99}

# Real false-merge #1: both titles share "12pcs" (an unrelated per-kit
# bur count), which defeated the old full-disjointness guard even though
# they also state a real, different pack size ("1 Piece" vs "2 Pack").
ONE_PIECE_TWO_PACK_A = {"listing_id": "B0F599K6BR", "title": '12pcs Silicone Composite Polishing Heads 3/32" Shaft Resin Base Acrylic Denture Polishing Burs Finishing Kits 2.35mm for Rotary Tools (1 Piece)', "brand": "HOYIKI", "price": 12.99}
ONE_PIECE_TWO_PACK_B = {"listing_id": "B0F598SL5Q", "title": '12pcs Silicone Composite Polishing Heads 3/32" Shaft Resin Base Acrylic Denture Polishing Burs Finishing Kits 2.35mm for Rotary Tools (2 Pack)', "brand": "HOYIKI", "price": 20.99}

# Accepted tradeoff, not a bug: both are legitimately "2 Pack" Dentemp
# kits (a correct match), but one also states "8 Count" (units inside
# the pack) and the other doesn't mention count at all -- the symmetric-
# difference guard now queues this for human confirmation instead of
# auto-matching it. Queuing a correct match costs a quick human glance;
# the alternative (full disjointness) is what let false-merge #1 through.
CONSISTENT_PACK_DIFFERENT_COUNT_A = {"listing_id": "B0F9B9LHPW", "title": "Dentemp Repair Kit - Repair-It Advanced Formula Denture Repair Kit - Repairs Broken Dentures, Mends Cracks and Replace Loose Teeth - 2 Pack (8 Count) - (Packaging May Vary)", "brand": "Dentemp", "price": 15.67}
CONSISTENT_PACK_DIFFERENT_COUNT_B = {"listing_id": "B083QN94W5", "title": "Dentemp Repair Kit - Repair-It Advanced Formula Denture Repair Kit (Pack of 2) - Repairs Broken Dentures, Mends Cracks and Replace Loose Teeth", "brand": "Dentemp", "price": 14.97}


def test_confirmed_identical_listing_is_a_confident_match():
    result = resolve_pair(CONFIRMED_A, CONFIRMED_B, "title_fingerprint_block")
    assert result.decision == "MATCH"
    assert result.match_confidence == 1.0
    assert result.review_status == "auto_accepted"


def test_different_brand_near_identical_title_is_not_auto_merged():
    result = resolve_pair(DIFFERENT_BRAND_A, DIFFERENT_BRAND_B, "title_fingerprint_block")
    assert result.decision == "UNCERTAIN"
    assert result.review_status == "needs_review"


def test_color_variant_with_unexplained_price_gap_is_not_auto_merged():
    result = resolve_pair(COLOR_VARIANT_A, COLOR_VARIANT_B, "brand_block")
    assert result.decision == "UNCERTAIN"
    assert result.review_status == "needs_review"


def test_bundle_pack_variant_is_not_auto_merged():
    """Same brand, high title similarity -- would clear the fuzzy-match
    threshold on brand+title alone, but this is a single-unit vs. pack-of-2
    bundle of the same base product. Per docs/entity_resolution.md, bundle
    vs. single-unit is a market-analysis axis, never auto-merged (default
    category policy: pack_quantity=separate_products)."""
    result = resolve_pair(BUNDLE_A, BUNDLE_B, "brand_block")
    assert result.decision != "MATCH"


def test_box_vs_bottle_pack_variant_is_no_longer_auto_merged():
    """Regression for a real, confirmed false merge
    (docs/entity_resolution_evaluation_report.md false-merge #3): this
    exact pair auto-MATCHed at 0.927 confidence in the live DB because
    'bottle' wasn't in extract_quantities' recognized unit vocabulary --
    '1 Box' extracted {'1'} but '2 Bottle' extracted nothing, so the
    pack-variant guard (which requires both sides non-empty) never ran.
    Now that 'bottle' is recognized, this must be queued, not merged."""
    result = resolve_pair(BOX_BOTTLE_A, BOX_BOTTLE_B, "brand_block")
    assert result.decision != "MATCH"
    assert result.match_method == "possible_pack_variant"
    assert result.review_status == "needs_review"


def test_shared_unrelated_number_no_longer_defeats_the_pack_variant_guard():
    """Regression for a real, confirmed false merge
    (docs/entity_resolution_evaluation_report.md false-merge #1): this
    exact pair auto-MATCHed at 0.911 confidence in the live DB because
    both titles also say '12pcs' (the bur count inside the kit, unrelated
    to the outer '1 Piece'/'2 Pack' difference) -- the old full-
    disjointness check (`q_a.isdisjoint(q_b)`) was defeated by that one
    shared, irrelevant number. Symmetric difference catches it: {'1','12'}
    vs {'2','12'} differ on '1'/'2', which is what actually matters."""
    result = resolve_pair(ONE_PIECE_TWO_PACK_A, ONE_PIECE_TWO_PACK_B, "brand_block")
    assert result.decision != "MATCH"
    assert result.match_method == "possible_pack_variant"
    assert result.review_status == "needs_review"


def test_consistent_pack_size_with_extra_detail_on_one_side_is_now_queued_not_matched():
    """Documents the accepted tradeoff from switching to symmetric
    difference, not a bug: both listings are legitimately '2 Pack'
    Dentemp kits (a correct match) but one also states '8 Count' (a
    sub-detail the other omits). This now gets queued for a quick human
    confirmation instead of auto-matching -- deliberately accepted, since
    queuing a correct match is far cheaper than the alternative (silently
    shipping a wrong one, as in the test above). See DECISIONS.md."""
    result = resolve_pair(CONSISTENT_PACK_DIFFERENT_COUNT_A, CONSISTENT_PACK_DIFFERENT_COUNT_B, "brand_block")
    assert result.decision != "MATCH"
    assert result.match_method == "possible_pack_variant"
    assert result.review_status == "needs_review"


# Disjoint quantities (no shared value at all), unlike BUNDLE_A/BUNDLE_B
# whose "3 ea (Pack of 2)" phrasing overlaps on "3" with plain "3 ea" --
# see similarity.py::extract_quantities' documented limitation.
DISJOINT_QTY_A = {"listing_id": "L11", "title": "Acme Widget Refill 3 Pack", "brand": "Acme", "price": 10.0}
DISJOINT_QTY_B = {"listing_id": "L12", "title": "Acme Widget Refill 5 Pack", "brand": "Acme", "price": 10.0}


def test_variant_policy_is_configurable_per_category_not_hardcoded():
    """Same exact pair, two different category policies -> two different
    outcomes. Proves pack-quantity handling is read from config, not a
    universal rule baked into the function (docs/entity_resolution.md
    "Product Identity Rules")."""
    with patch("dmie.matching.resolution._variant_policy", return_value={"pack_quantity": "separate_products"}):
        separate = resolve_pair(DISJOINT_QTY_A, DISJOINT_QTY_B, "brand_block", category="denture_base")
    with patch("dmie.matching.resolution._variant_policy", return_value={"pack_quantity": "same_product"}):
        same = resolve_pair(DISJOINT_QTY_A, DISJOINT_QTY_B, "brand_block", category="some_other_category")

    assert separate.match_method == "possible_pack_variant"
    assert separate.review_status == "needs_review"
    # With pack_quantity=same_product, the override doesn't fire -- same
    # brand + near-identical title + same price clears the match threshold.
    assert same.match_method != "possible_pack_variant"
    assert same.decision == "MATCH"


def test_variant_policy_reads_real_config_file_for_denture_base():
    """No mocking -- confirms config/categories.yaml's actual denture_base
    entry (pack_quantity: separate_products) produces the documented
    behavior."""
    result = resolve_pair(DISJOINT_QTY_A, DISJOINT_QTY_B, "brand_block", category="denture_base")
    assert result.match_method == "possible_pack_variant"


def test_exact_identifier_match_returns_none_when_no_identifier_field():
    """This dataset has no model/UPC/GTIN field (docs/data_dictionary.md)
    -- the check is real but always defers today."""
    assert exact_identifier_match(CONFIRMED_A, CONFIRMED_B) is None


def test_exact_identifier_match_activates_when_field_present():
    a = {**CONFIRMED_A, "model_number": "ABC123"}
    b = {**CONFIRMED_B, "model_number": "ABC123"}
    assert exact_identifier_match(a, b) is True

    c = {**CONFIRMED_A, "model_number": "ABC123"}
    d = {**CONFIRMED_B, "model_number": "XYZ999"}
    assert exact_identifier_match(c, d) is False


# --- clustering ---

def test_cluster_products_groups_confirmed_matches():
    from dmie.matching.resolution import CandidateResult
    results = [CandidateResult("L1", "L2", "brand_block", "fuzzy_composite", 1.0, "MATCH", "auto_accepted")]
    product_ids = cluster_products(["L1", "L2", "L3"], results)
    assert product_ids["L1"] == product_ids["L2"]
    assert product_ids["L3"] != product_ids["L1"]


def test_every_listing_gets_a_product_id_even_unmatched():
    product_ids = cluster_products(["L1", "L2", "L3"], [])
    assert len(product_ids) == 3
    assert len({v for v in product_ids.values()}) == 3  # all distinct singletons


def test_build_product_listings_has_all_required_fields():
    from dmie.matching.resolution import CandidateResult
    results = [CandidateResult("L1", "L2", "brand_block", "fuzzy_composite", 0.95, "MATCH", "auto_accepted")]
    rows = build_product_listings(["L1", "L2"], results)
    assert len(rows) == 2
    for row in rows:
        for field in ("product_id", "listing_id", "match_method", "match_confidence", "review_status"):
            assert field in row
    assert rows[0]["product_id"] == rows[1]["product_id"]


def test_build_product_listings_singleton_has_confidence_one():
    rows = build_product_listings(["L1"], [])
    assert rows[0]["match_method"] == "singleton"
    assert rows[0]["match_confidence"] == 1.0


# --- Stage 7 AI arbitration (mocked, same pattern as classification) ---

def test_ai_arbitration_unavailable_stays_uncertain():
    with patch("dmie.matching.resolution.ai_arbitrate", return_value=None):
        # force a mid-band composite by using the different-brand pair, which
        # is not caught by the quantity-mismatch override
        result = resolve_pair(DIFFERENT_BRAND_A, DIFFERENT_BRAND_B, "title_fingerprint_block")
    assert result.decision == "UNCERTAIN"
    assert result.review_status == "needs_review"


def test_ai_arbitration_confident_match_is_accepted():
    ai_output = {"decision": "MATCH", "confidence": 0.95, "reason": "same product, different reseller"}
    mid_band_a = {"listing_id": "L9", "title": "Some Widget Product Alpha", "brand": "BrandX", "price": 10.0}
    mid_band_b = {"listing_id": "L10", "title": "Some Widget Product Beta", "brand": "BrandY", "price": 10.5}
    with patch("dmie.matching.resolution.ai_arbitrate", return_value=ai_output):
        result = resolve_pair(mid_band_a, mid_band_b, "title_fingerprint_block")
    assert result.decision == "MATCH"
    assert result.match_method == "ai_arbitration"
    assert result.review_status == "auto_accepted"


# --- Product Matching evaluation report (Milestone 10 addendum) ---

def test_matching_breakdown_hand_computed():
    candidates = (
        [{"match_method": "fuzzy_composite", "review_status": "auto_accepted"}] * 3
        + [{"match_method": "ai_arbitration", "review_status": "auto_accepted"}] * 2
        + [{"match_method": "fuzzy_composite", "review_status": "needs_review"}] * 5
    )
    breakdown = matching_breakdown(candidates)
    assert breakdown["total"] == 10
    assert breakdown["automatic"] == 3
    assert breakdown["ai_assisted"] == 2
    assert breakdown["human_reviewed"] == 5
    assert breakdown["automatic_pct"] == 0.3
    assert breakdown["ai_assisted_pct"] == 0.2
    assert breakdown["human_reviewed_pct"] == 0.5


def test_matching_breakdown_empty_is_none_not_zero():
    breakdown = matching_breakdown([])
    assert breakdown["total"] == 0
    assert breakdown["automatic_pct"] is None  # no data, not "0% automatic"


# --- Product Master (M12) ---

def test_build_products_singleton_has_no_identity_confidence():
    """A singleton was never matched to anything -- there's no merge
    decision to score, so confidence is None (not a fabricated 1.0),
    per PRINCIPLES.md's 'None never 0' principle."""
    listings = [{"listing_id": "L1", "category_id": "denture_base", "title": "Solo Product",
                 "brand": "Acme", "image_url": "img1.jpg", "monthly_sales": None, "product_type": None}]
    product_rows = build_product_listings(["L1"], [])
    products = build_products(listings, product_rows, [])
    assert len(products) == 1
    assert products[0]["confidence"] is None
    assert products[0]["product_name"] == "Solo Product"
    assert products[0]["brand"] == "Acme"


def test_build_products_always_includes_product_family_as_none():
    """Tier 2, Milestone 4: product_family is a real schema field every
    product row must carry (repository.py's INSERT requires the key to
    exist), but deliberately always None -- this pilot's data has no
    real family-level diversity to derive it from yet. See
    docs/product_knowledge_graph.md."""
    listings = [{"listing_id": "L1", "category_id": "denture_base", "title": "Solo Product",
                 "brand": "Acme", "image_url": "img1.jpg", "monthly_sales": None, "product_type": None}]
    product_rows = build_product_listings(["L1"], [])
    products = build_products(listings, product_rows, [])
    assert products[0]["product_family"] is None


def test_build_products_multi_listing_confidence_is_weakest_link():
    """Two listings matched at 0.95: the product's identity confidence is
    that matching decision's confidence, not a fabricated 1.0."""
    listings = [
        {"listing_id": "L1", "category_id": "denture_base", "title": "Widget A", "brand": "Acme",
         "image_url": "img1.jpg", "monthly_sales": 10, "product_type": None},
        {"listing_id": "L2", "category_id": "denture_base", "title": "Widget B", "brand": "Acme",
         "image_url": "img2.jpg", "monthly_sales": 5, "product_type": None},
    ]
    match_results = [CandidateResult("L1", "L2", "block", "fuzzy_composite", 0.95, "MATCH", "auto_accepted")]
    product_rows = build_product_listings(["L1", "L2"], match_results)
    products = build_products(listings, product_rows, match_results)
    assert len(products) == 1
    assert products[0]["confidence"] == 0.95
    # named after the best-selling listing (highest monthly_sales), not L1 by default order
    assert products[0]["product_name"] == "Widget A"


def test_build_products_falls_back_to_lowest_listing_id_when_no_sales_data():
    """best_selling_listing() returns None when no listing has sales data
    -- naming must not silently pick an arbitrary listing; falls back to
    the lowest listing_id, deterministic regardless of input order."""
    listings = [
        {"listing_id": "L2", "category_id": "denture_base", "title": "Second", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": None},
        {"listing_id": "L1", "category_id": "denture_base", "title": "First", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": None},
    ]
    product_rows = build_product_listings(["L1", "L2"], [
        CandidateResult("L1", "L2", "block", "fuzzy_composite", 0.5, "MATCH", "auto_accepted")
    ])
    products = build_products(listings, product_rows, [
        CandidateResult("L1", "L2", "block", "fuzzy_composite", 0.5, "MATCH", "auto_accepted")
    ])
    assert products[0]["product_name"] == "First"


def test_build_products_brand_majority_with_deterministic_tiebreak():
    listings = [
        {"listing_id": "L1", "category_id": "denture_base", "title": "A", "brand": "Zeta",
         "image_url": None, "monthly_sales": 1, "product_type": None},
        {"listing_id": "L2", "category_id": "denture_base", "title": "B", "brand": "Alpha",
         "image_url": None, "monthly_sales": None, "product_type": None},
    ]
    match_results = [CandidateResult("L1", "L2", "block", "fuzzy_composite", 0.9, "MATCH", "auto_accepted")]
    product_rows = build_product_listings(["L1", "L2"], match_results)
    products = build_products(listings, product_rows, match_results)
    # tied 1-1 on count -> alphabetically first, not input order
    assert products[0]["brand"] == "Alpha"


def test_build_products_type_is_none_when_unclassified():
    """No product_type has been classified yet (M11's classifier hasn't
    run) -- must be None, never an invented guess."""
    listings = [{"listing_id": "L1", "category_id": "denture_base", "title": "X", "brand": None,
                 "image_url": None, "monthly_sales": None, "product_type": None}]
    product_rows = build_product_listings(["L1"], [])
    products = build_products(listings, product_rows, [])
    assert products[0]["product_type"] is None
    assert products[0]["product_type_confidence"] is None
    assert products[0]["product_type_conflict"] is False


# --- Product Master type integration (M11 Stage 3) ---

def test_build_products_type_is_majority_vote_with_averaged_winning_confidence():
    """3 listings, 2 agree on DB_WAX_PLATE (confidences 0.95, 0.85), 1
    disagrees (DB_RELINE, 0.6) -- majority wins, and product_type_confidence
    is the mean of only the WINNING side's confidences, not all 3 (a
    dissenting vote's confidence shouldn't dilute or inflate the winner's
    reported confidence)."""
    listings = [
        {"listing_id": "L1", "category_id": "denture_base", "title": "A", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": "DB_WAX_PLATE", "product_type_confidence": 0.95},
        {"listing_id": "L2", "category_id": "denture_base", "title": "B", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": "DB_WAX_PLATE", "product_type_confidence": 0.85},
        {"listing_id": "L3", "category_id": "denture_base", "title": "C", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": "DB_RELINE", "product_type_confidence": 0.6},
    ]
    match_results = [
        CandidateResult("L1", "L2", "block", "fuzzy_composite", 0.9, "MATCH", "auto_accepted"),
        CandidateResult("L2", "L3", "block", "fuzzy_composite", 0.9, "MATCH", "auto_accepted"),
    ]
    product_rows = build_product_listings(["L1", "L2", "L3"], match_results)
    products = build_products(listings, product_rows, match_results)
    assert len(products) == 1
    assert products[0]["product_type"] == "DB_WAX_PLATE"
    assert products[0]["product_type_confidence"] == pytest.approx(0.9)  # mean(0.95, 0.85)
    assert products[0]["product_type_conflict"] is True  # listings disagreed


def test_build_products_no_conflict_when_all_classified_listings_agree():
    listings = [
        {"listing_id": "L1", "category_id": "denture_base", "title": "A", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": "DB_RESIN", "product_type_confidence": 0.95},
        {"listing_id": "L2", "category_id": "denture_base", "title": "B", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": "DB_RESIN", "product_type_confidence": 0.95},
    ]
    match_results = [CandidateResult("L1", "L2", "block", "fuzzy_composite", 0.9, "MATCH", "auto_accepted")]
    product_rows = build_product_listings(["L1", "L2"], match_results)
    products = build_products(listings, product_rows, match_results)
    assert products[0]["product_type"] == "DB_RESIN"
    assert products[0]["product_type_confidence"] == 0.95
    assert products[0]["product_type_conflict"] is False


def test_build_products_type_confidence_ignores_unclassified_listings_in_the_product():
    """A product with one classified and one never-classified listing:
    the unclassified one contributes no vote and no confidence value --
    it must not pull the average down or count as a dissent/conflict."""
    listings = [
        {"listing_id": "L1", "category_id": "denture_base", "title": "A", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": "DB_WAX_PLATE", "product_type_confidence": 0.95},
        {"listing_id": "L2", "category_id": "denture_base", "title": "B", "brand": None,
         "image_url": None, "monthly_sales": None, "product_type": None, "product_type_confidence": None},
    ]
    match_results = [CandidateResult("L1", "L2", "block", "fuzzy_composite", 0.9, "MATCH", "auto_accepted")]
    product_rows = build_product_listings(["L1", "L2"], match_results)
    products = build_products(listings, product_rows, match_results)
    assert products[0]["product_type"] == "DB_WAX_PLATE"
    assert products[0]["product_type_confidence"] == 0.95
    assert products[0]["product_type_conflict"] is False


def test_matching_breakdown_ai_arbitration_pending_review_counts_as_human_reviewed():
    """An ai_arbitration attempt that still ended up needs_review (AI
    itself was uncertain) must count as human_reviewed, not ai_assisted --
    ai_assisted means the AI stage actually resolved it."""
    candidates = [{"match_method": "ai_arbitration", "review_status": "needs_review"}]
    breakdown = matching_breakdown(candidates)
    assert breakdown["ai_assisted"] == 0
    assert breakdown["human_reviewed"] == 1

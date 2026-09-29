"""M11 Stage 2: product-type classification.

Grounded in the 12 listings currently classified RELEVANT for
denture_base (docs/product_taxonomy.md) and the frozen taxonomy
(config/taxonomy/denture_base_v1.yaml, DECISIONS.md "M11 -- Taxonomy v1
frozen"). This is the classifier's first validation target per that
freeze: run it on the confirmed-relevant listings and compare against the
human-approved types -- NOT a run against the full 92-product dataset,
which is deliberately out of scope until this passes review.
"""

from unittest.mock import patch

import pytest

from dmie.classification.product_type_classifier import (
    ListingContext,
    ProductTypeResult,
    UNCERTAIN,
    apply_rules,
    classify_product_type,
    evaluate_against_gold,
    format_gold_comparison,
    load_taxonomy,
)
from dmie.matching.resolution import build_products, CandidateResult, build_product_listings

# --- The 12 listings classified RELEVANT for denture_base, and the
# taxonomy_v1 type the user approved for each (docs/product_taxonomy.md's
# 3 candidates -> DECISIONS.md's frozen mapping). This IS the human label
# set for this category/taxonomy version -- there is no separate hand-typed
# "gold product_type" file yet (the 50-row relevance gold set's
# `product_type` column has always been an empty placeholder -- see
# scripts/build_gold_dataset.py -- since this taxonomy didn't exist until
# now), so this dict is the actual source of truth to evaluate against.
CONFIRMED_RELEVANT_LISTINGS = {
    "B07DYMJ7TQ": "Base Plate Wax Orthodontic Dental Wax Sheets 20PCS, Red Utility Bite Wax Denture Casting Wax Sheet Supply for Modelling|Filling|Lab Equipment - 12 Months Warranty",
    "B094YBT6VD": "10pcs Red Base Plate Wax Sheets, 2.0mm Utility Bite Wax for Jewelry Carving, Denture Casting, and Modeling,for Crafting Rings, Earrings, Bracelets, and Lab Equipment",
    "B09JL2CYKR": "Dental Base Plate Wax 18 PCS, Denture Red Utility Bite Casting Sheets for Orthodontic Modeling Filling Laboratory Supply",
    "B0CDM8JCLH": "250g Dental Base Plate Wax Molding Casting Wax Sheet Denture Material Red Utility Wax Sheets for Dentist or Jewelry Lab Dentist (1 Box)",
    "B0CXMQ7DFZ": "General USE 20PCS 270g Medium Soft Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture",
    "B0CXTC26TV": "Antinsky Denture Base Resin",
    "B0DRBMXKZR": "Dental Base Plate Wax 20pcs Red Denture Base Plate Casting Modling Wax Sheet, Dental Denture Materials for Modeling Filling Lab Dentist Auxiliary Material",
    "B0F26TYZQD": "General USE Large 20PCS 480g Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture",
    "B0F7LKY5V7": "Dental Base Plate Wax Sheets 20 Pcs 240g | For Denture Modeling, For Orthodontic Work, Dental Lab Supplies",
    "B0FMK8XB26": "Hard Denture Reline Kit – Long-Lasting Denture Base Repair & Fit Adjustment, Acrylic-Based, Self-Curing, Translucent Pink – Includes Powder, Liquid, Primer & Tools",
    "B0G81P76D8": "Self-Curing Hard Denture Reline Kit for Home Use, Complete Denture Base Renewal and Fit Adjustment Set, Pink, Includes Powder, Liquid, Bonding Solution and Accessories",
    "B0H6RLQ6YZ": "General USE 20PCS 270g Medium Soft Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture",
}

HUMAN_APPROVED_TYPE = {
    "B07DYMJ7TQ": "DB_WAX_PLATE", "B094YBT6VD": "DB_WAX_PLATE", "B09JL2CYKR": "DB_WAX_PLATE",
    "B0CDM8JCLH": "DB_WAX_PLATE", "B0CXMQ7DFZ": "DB_WAX_PLATE", "B0DRBMXKZR": "DB_WAX_PLATE",
    "B0F26TYZQD": "DB_WAX_PLATE", "B0F7LKY5V7": "DB_WAX_PLATE", "B0H6RLQ6YZ": "DB_WAX_PLATE",
    "B0FMK8XB26": "DB_RELINE", "B0G81P76D8": "DB_RELINE",
    "B0CXTC26TV": "DB_RESIN",
}

# Two additional hand-reviewed YES listings from the 50-row relevance gold
# set (data/samples/gold_labels_pilot.xlsx) that are NOT among the 12
# above (they resolve to UNCERTAIN in the live DB today because relevance
# classification's AI stage is unconfigured -- see
# docs/classification_guidelines.md's note on B0DYJMP4ZJ/B01I3ZIV2M).
# Their type here is grounded directly in that same doc's own words, not
# invented: "[B01I3ZIV2M] is the same material class as base resin" and
# B0DYJMP4ZJ is described among the "Reline... kits" EXACT_MATCH examples.
# Included to widen the DB_RESIN evaluation beyond a single example.
EXTENDED_GOLD_LISTINGS = {
    "B01I3ZIV2M": "Dental Acrylic Tooth Lang Jet Denture Repair Liquid 236 ml (8 oz.) Bottle 1404",
    "B0DYJMP4ZJ": "Silicone Reline Denture Set, Silicone Denture Set, Silicone Reline Kit for Dentures, Denture Repair Kit, Soft Silicone Denture Reline Kit (1 PCS)",
}
EXTENDED_HUMAN_APPROVED_TYPE = {"B01I3ZIV2M": "DB_RESIN", "B0DYJMP4ZJ": "DB_RELINE"}


def _ctx(asin: str, title: str) -> ListingContext:
    return ListingContext(listing_id=asin, title=title)


def _classify_all(titles: dict[str, str]) -> dict[str, ProductTypeResult]:
    return {asin: classify_product_type(_ctx(asin, title)) for asin, title in titles.items()}


# --- Taxonomy loader: never hardcoded, always read from the frozen config ---

def test_load_taxonomy_reads_the_frozen_config_not_hardcoded_ids():
    taxonomy = load_taxonomy("denture_base", 1)
    assert taxonomy.version == 1
    assert taxonomy.category == "denture_base"
    assert set(taxonomy.ids) == {"DB_RESIN", "DB_WAX_PLATE", "DB_RELINE"}


# --- Deterministic rules layer: the 3 worked examples from the M11 Stage 2 brief ---

@pytest.mark.parametrize("title,expected_type", [
    ("Heat Cure Denture Base Resin", "DB_RESIN"),
    ("Hard Denture Reline Kit", "DB_RELINE"),
    ("Denture Wax Plate", "DB_WAX_PLATE"),
])
def test_rules_layer_worked_examples(title, expected_type):
    from dmie.classification.product_type_classifier import _load_signals
    signals = _load_signals("denture_base", 1)
    result = apply_rules(title, signals)
    assert result is not None
    product_type, confidence, reason = result
    assert product_type == expected_type
    assert confidence == 0.95


def test_rules_layer_does_not_hardcode_taxonomy_ids_in_python():
    """The rules function takes the signal map as a parameter -- it has no
    knowledge of DB_RESIN/DB_WAX_PLATE/DB_RELINE as literals. Feeding it a
    completely different, made-up taxonomy proves this."""
    fake_signals = {"FOO_BAR": ["gizmo"]}
    result = apply_rules("A totally normal gizmo", fake_signals)
    assert result == ("FOO_BAR", 0.95, "rule_match:FOO_BAR")


def test_rules_layer_never_returns_a_value_outside_the_given_signals():
    from dmie.classification.product_type_classifier import _load_signals
    signals = _load_signals("denture_base", 1)
    for title in CONFIRMED_RELEVANT_LISTINGS.values():
        result = apply_rules(title, signals)
        if result is not None:
            assert result[0] in signals


# --- Ambiguous vocabulary: rules must defer, never guess with confidence ---

def test_ambiguous_title_is_not_auto_accepted_by_rules_alone():
    """B0FMK8XB26: 'Acrylic-Based' (a DB_RESIN signal) alongside 'reline'/
    'repair'/'adjustment' (DB_RELINE signals) in the same title -- a real
    vocabulary overlap (resin and reline kits share the same acrylic
    chemistry), not a bug. Rules must not silently pick one."""
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        result = classify_product_type(_ctx("B0FMK8XB26", CONFIRMED_RELEVANT_LISTINGS["B0FMK8XB26"]))
    assert result.product_type == UNCERTAIN
    assert result.classifier_method == "unavailable"
    assert "rule_ambiguous" in result.reason
    assert result.review_status == "needs_review"


# --- Confident rule match bypasses AI entirely ---

def test_confident_rule_match_bypasses_ai_entirely():
    with patch("dmie.classification.product_type_classifier.classify_with_ai") as mock_ai:
        result = classify_product_type(_ctx("B0CXTC26TV", "Antinsky Denture Base Resin"))
    mock_ai.assert_not_called()
    assert result.product_type == "DB_RESIN"
    assert result.confidence == 0.95
    assert result.classifier_method == "rules"
    assert result.review_status == "auto_accepted"


# --- AI fallback: unavailable / accept / review / reject tiers ---

def test_ai_unavailable_forces_uncertain_never_a_guess():
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        result = classify_product_type(_ctx("L1", "Some listing with no keyword signal at all"))
    assert result.product_type == UNCERTAIN
    assert result.confidence == 0.0
    assert result.reason == "ai_unavailable"
    assert result.classifier_method == "unavailable"
    assert result.review_status == "needs_review"


def test_confident_ai_result_is_auto_accepted():
    ai_output = {"product_type": "DB_RELINE", "confidence": 0.9, "reason": "reline kit for repairing an existing denture"}
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=ai_output):
        result = classify_product_type(_ctx("B0FMK8XB26", CONFIRMED_RELEVANT_LISTINGS["B0FMK8XB26"]))
    assert result.product_type == "DB_RELINE"
    assert result.confidence == 0.9
    assert result.classifier_method == "ai"
    assert result.review_status == "auto_accepted"


def test_mid_confidence_ai_result_is_flagged_but_type_preserved():
    """sampling_threshold (0.60) <= confidence < automatic_threshold (0.85)
    -> needs_review, but the stated type is kept, not discarded -- the
    tri-state pattern (uncertainty is preserved, not just binary accept/
    reject)."""
    ai_output = {"product_type": "DB_RESIN", "confidence": 0.65, "reason": "plausible but not certain"}
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=ai_output):
        result = classify_product_type(_ctx("L1", "some ambiguous listing"))
    assert result.product_type == "DB_RESIN"
    assert result.review_status == "needs_review"
    assert result.classifier_method == "ai"


def test_low_confidence_ai_result_is_forced_to_uncertain():
    ai_output = {"product_type": "DB_RESIN", "confidence": 0.3, "reason": "not sure at all"}
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=ai_output):
        result = classify_product_type(_ctx("L1", "some very ambiguous listing"))
    assert result.product_type == UNCERTAIN
    assert result.review_status == "needs_review"


def test_ai_returning_a_label_outside_the_taxonomy_is_rejected_never_trusted():
    """Never output 'Other' or any invented category, no matter how
    confident the model claims to be -- controlled taxonomy, not free-form
    AI invention (PRINCIPLES.md)."""
    ai_output = {"product_type": "Other", "confidence": 0.99, "reason": "doesn't fit anything"}
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=ai_output):
        result = classify_product_type(_ctx("L1", "some weird listing"))
    assert result.product_type == UNCERTAIN
    assert result.confidence == 0.0
    assert "ai_invalid_label" in result.reason
    assert result.review_status == "needs_review"


def test_ai_explicit_uncertain_is_a_valid_output_not_an_error():
    ai_output = {"product_type": "UNCERTAIN", "confidence": 0.9, "reason": "genuinely does not fit any of the three types"}
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=ai_output):
        result = classify_product_type(_ctx("L1", "some weird listing"))
    assert result.product_type == UNCERTAIN
    assert result.review_status == "auto_accepted"  # confidently, correctly unclassifiable


# --- Structured output fields (requirement 6) ---

def test_result_has_all_required_structured_fields():
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        result = classify_product_type(_ctx("B0CXTC26TV", "Antinsky Denture Base Resin"))
    assert result.listing_id == "B0CXTC26TV"
    assert result.taxonomy_version == 1
    assert result.product_type == "DB_RESIN"
    assert isinstance(result.confidence, float)
    assert isinstance(result.reason, str)
    assert result.classifier_method in ("rules", "ai", "unavailable")
    assert result.review_status in ("auto_accepted", "needs_review")


# --- Reproducibility ---

def test_classification_is_reproducible_across_repeated_calls():
    results = [classify_product_type(_ctx("B0CXTC26TV", "Antinsky Denture Base Resin")) for _ in range(5)]
    assert len({(r.product_type, r.confidence, r.reason) for r in results}) == 1


# --- First validation target: the 12 confirmed-relevant listings vs. the
# human-approved taxonomy_v1 mapping. AI unavailable in this environment,
# same honest constraint the rest of the pipeline has always run under. ---

def test_evaluation_on_the_12_confirmed_relevant_listings():
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        predictions = _classify_all(CONFIRMED_RELEVANT_LISTINGS)

    rows = evaluate_against_gold(HUMAN_APPROVED_TYPE, predictions)
    print("\n" + format_gold_comparison(rows))

    correct = {r.asin for r in rows if r.correct}
    incorrect = {r.asin: r for r in rows if not r.correct}

    # 11/12 must be exact, confident rule matches -- the taxonomy's own
    # vocabulary is unambiguous for everything except the one real overlap
    # case documented below.
    assert len(correct) == 11
    # The one miss must be exactly B0FMK8XB26, and for the documented,
    # legitimate reason (ambiguous rule signal + AI unavailable) -- not a
    # silent wrong guess.
    assert set(incorrect) == {"B0FMK8XB26"}
    missed = predictions["B0FMK8XB26"]
    assert missed.product_type == UNCERTAIN
    assert "rule_ambiguous" in missed.reason


def test_evaluation_on_the_extended_gold_set_from_relevance_review():
    """Widens the DB_RESIN evaluation beyond a single listing using 2 more
    hand-reviewed YES listings from the relevance gold set (see module
    docstring). Same honest outcome: the genuinely ambiguous acrylic
    listing correctly defers rather than guesses."""
    all_titles = {**CONFIRMED_RELEVANT_LISTINGS, **EXTENDED_GOLD_LISTINGS}
    all_human_types = {**HUMAN_APPROVED_TYPE, **EXTENDED_HUMAN_APPROVED_TYPE}

    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        predictions = _classify_all(all_titles)

    rows = evaluate_against_gold(all_human_types, predictions)
    print("\n" + format_gold_comparison(rows))

    incorrect = {r.asin for r in rows if not r.correct}
    # B01I3ZIV2M shares B0FMK8XB26's exact ambiguity (acrylic + repair) --
    # same legitimate, documented miss, not a new failure.
    assert incorrect == {"B0FMK8XB26", "B01I3ZIV2M"}
    assert predictions["B01I3ZIV2M"].product_type == UNCERTAIN
    assert "rule_ambiguous" in predictions["B01I3ZIV2M"].reason
    # The other reline example, with no competing resin vocabulary, must
    # still resolve cleanly.
    assert predictions["B0DYJMP4ZJ"].product_type == "DB_RELINE"
    assert predictions["B0DYJMP4ZJ"].classifier_method == "rules"


# --- Product Master integration: classifications must flow into
# build_products' per-product type (mode vote across a product's
# listings), not require any product-master schema change (M12's
# build_products already reads listing_classification-style dicts). ---

def test_classifications_flow_into_the_product_master_via_build_products():
    # B0CXMQ7DFZ and B0H6RLQ6YZ are the same product (identical listings,
    # already merged by entity resolution -- see M12) -- both DB_WAX_PLATE.
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        predictions = _classify_all(CONFIRMED_RELEVANT_LISTINGS)

    listings = [
        {
            "listing_id": asin, "category_id": "denture_base", "title": title,
            "brand": "SomeBrand", "image_url": None, "monthly_sales": None,
            "product_type": predictions[asin].product_type if predictions[asin].product_type != UNCERTAIN else None,
        }
        for asin, title in CONFIRMED_RELEVANT_LISTINGS.items()
    ]
    match_results = [
        CandidateResult("B0CXMQ7DFZ", "B0H6RLQ6YZ", "block", "fuzzy_composite", 0.9, "MATCH", "auto_accepted"),
    ]
    listing_ids = list(CONFIRMED_RELEVANT_LISTINGS)
    product_rows = build_product_listings(listing_ids, match_results)
    products = build_products(listings, product_rows, match_results)

    by_id = {p["product_id"]: p for p in products}
    # Find the product both B0CXMQ7DFZ and B0H6RLQ6YZ ended up in.
    merged_pid = next(r["product_id"] for r in product_rows if r["listing_id"] == "B0CXMQ7DFZ")
    assert next(r["product_id"] for r in product_rows if r["listing_id"] == "B0H6RLQ6YZ") == merged_pid
    assert by_id[merged_pid]["product_type"] == "DB_WAX_PLATE"

    # The one listing rules+AI couldn't resolve (B0FMK8XB26, a singleton
    # product) must surface as None on the Product Master -- never a
    # guessed or fabricated type ("None never 0").
    reline_singleton_pid = next(r["product_id"] for r in product_rows if r["listing_id"] == "B0FMK8XB26")
    assert by_id[reline_singleton_pid]["product_type"] is None

    # Every DB_WAX_PLATE-predicted singleton product must carry that type
    # through untouched.
    resin_pid = next(r["product_id"] for r in product_rows if r["listing_id"] == "B0CXTC26TV")
    assert by_id[resin_pid]["product_type"] == "DB_RESIN"

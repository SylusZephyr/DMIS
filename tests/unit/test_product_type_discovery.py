"""Grounded in the real 12 RELEVANT denture_base listings (see
docs/product_taxonomy.md) plus synthetic regression cases."""

from dmie.classification.product_type_discovery import discover_candidate_types, tokenize_significant_words

REAL_RELEVANT_LISTINGS = [
    {"listing_id": "B0CXMQ7DFZ", "title": "General USE 20PCS 270g Medium Soft Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture"},
    {"listing_id": "B0H6RLQ6YZ", "title": "General USE 20PCS 270g Medium Soft Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture"},
    {"listing_id": "B09JL2CYKR", "title": "Dental Base Plate Wax 18 PCS, Denture Red Utility Bite Casting Sheets for Orthodontic Modeling Filling Laboratory Supply"},
    {"listing_id": "B0CXTC26TV", "title": "Antinsky Denture Base Resin"},
    {"listing_id": "B0CDM8JCLH", "title": "250g Dental Base Plate Wax Molding Casting Wax Sheet Denture Material Red Utility Wax Sheets for Dentist or Jewelry Lab Dentist (1 Box)"},
    {"listing_id": "B0DRBMXKZR", "title": "Dental Base Plate Wax 20pcs Red Denture Base Plate Casting Modling Wax Sheet, Dental Denture Materials for Modeling Filling Lab Dentist Auxiliary Material"},
    {"listing_id": "B0G81P76D8", "title": "Self-Curing Hard Denture Reline Kit for Home Use, Complete Denture Base Renewal and Fit Adjustment Set, Pink, Includes Powder, Liquid, Bonding Solution and Accessories"},
    {"listing_id": "B0F26TYZQD", "title": "General USE Large 20PCS 480g Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture"},
    {"listing_id": "B0FMK8XB26", "title": "Hard Denture Reline Kit – Long-Lasting Denture Base Repair & Fit Adjustment, Acrylic-Based, Self-Curing, Translucent Pink – Includes Powder, Liquid, Primer & Tools"},
    {"listing_id": "B094YBT6VD", "title": "10pcs Red Base Plate Wax Sheets, 2.0mm Utility Bite Wax for Jewelry Carving, Denture Casting, and Modeling,for Crafting Rings, Earrings, Bracelets, and Lab Equipment"},
    {"listing_id": "B0F7LKY5V7", "title": "Dental Base Plate Wax Sheets 20 Pcs 240g | For Denture Modeling, For Orthodontic Work, Dental Lab Supplies"},
    {"listing_id": "B07DYMJ7TQ", "title": "Base Plate Wax Orthodontic Dental Wax Sheets 20PCS, Red Utility Bite Wax Denture Casting Wax Sheet Supply for Modelling|Filling|Lab Equipment - 12 Months Warranty"},
]


def test_tokenize_ignores_generic_stopwords_and_short_words():
    words = tokenize_significant_words("General USE 20PCS Base Plate Wax Sheet for Denture")
    assert "wax" in words
    assert "base" in words
    assert "denture" not in words  # generic domain umbrella word
    assert "for" not in words


def test_discover_finds_the_three_real_clusters():
    """Grounded regression test: this exact discovery run produced these
    exact 3 clusters on the real data (docs/product_taxonomy.md)."""
    candidates = discover_candidate_types(REAL_RELEVANT_LISTINGS)
    sizes = sorted(c.size for c in candidates)
    assert sizes == [1, 2, 9]

    wax_cluster = next(c for c in candidates if c.size == 9)
    assert "wax" in wax_cluster.connecting_words

    reline_cluster = next(c for c in candidates if c.size == 2)
    assert set(reline_cluster.listing_ids) == {"B0G81P76D8", "B0FMK8XB26"}
    # The two titles overlap on many words ("hard", "reline", "adjustment",
    # "includes", "powder", "pink"...), all with an identical count -- the
    # auto-suggested label is a starting point for human review (which is
    # exactly why one exists), not a semantic guarantee. What must hold is
    # that it's a real shared word, not a fallback/empty placeholder.
    assert reline_cluster.connecting_words != ["unclustered"]
    assert len(reline_cluster.connecting_words) > 0

    singleton = next(c for c in candidates if c.size == 1)
    assert singleton.listing_ids == ["B0CXTC26TV"]


def test_discover_upper_bound_prevents_category_word_from_merging_everything():
    """Regression: without an upper prevalence bound, 'base' (present in
    ~all 12 RELEVANT listings, since that's what makes them relevant in
    the first place) bridged every cluster into one useless supercluster
    of all 12. This is exactly why max_prevalence exists."""
    candidates = discover_candidate_types(REAL_RELEVANT_LISTINGS)
    assert len(candidates) > 1
    assert not any(c.size == len(REAL_RELEVANT_LISTINGS) for c in candidates)


def test_discover_no_upper_bound_reproduces_the_bug():
    """Documents the bug directly: max_prevalence=1.0 (no upper bound)
    does merge everything into one cluster on this real data."""
    candidates = discover_candidate_types(REAL_RELEVANT_LISTINGS, max_prevalence=1.0)
    assert len(candidates) == 1
    assert candidates[0].size == len(REAL_RELEVANT_LISTINGS)


def test_discover_generalizes_to_a_different_vocabulary():
    """Not category-specific: the same code applied to a synthetic
    micromotor-flavored population should surface brushless/brushed
    clusters without any hardcoded knowledge of those words."""
    # Deliberately no incidental shared word (e.g. a spec value like "RPM")
    # across the brushless/brushed groups beyond "micromotor"/"handpiece"
    # themselves (both 100% prevalent, so both correctly excluded) --
    # otherwise that word would bridge the two groups into one, which is
    # a real, separate limitation of this heuristic, not what this test
    # is checking.
    listings = [
        {"listing_id": "M1", "title": "Dental Lab Brushless Micromotor Handpiece"},
        {"listing_id": "M2", "title": "Brushless Micromotor Handpiece for Dental Lab"},
        {"listing_id": "M3", "title": "Brushed Micromotor Handpiece Dental Lab"},
        {"listing_id": "M4", "title": "Brushed Micromotor Handpiece for Dental Lab"},
        {"listing_id": "M5", "title": "Dental Micromotor Handpiece Replacement Cable"},
    ]
    candidates = discover_candidate_types(listings)
    brushless = next(c for c in candidates if "brushless" in c.connecting_words)
    brushed = next(c for c in candidates if "brushed" in c.connecting_words)
    assert set(brushless.listing_ids) == {"M1", "M2"}
    assert set(brushed.listing_ids) == {"M3", "M4"}


def test_discover_handles_empty_input():
    assert discover_candidate_types([]) == []


def test_discover_single_listing_is_its_own_singleton():
    candidates = discover_candidate_types([{"listing_id": "L1", "title": "Some Unique Product"}])
    assert len(candidates) == 1
    assert candidates[0].listing_ids == ["L1"]


def test_discover_is_deterministic_across_hash_seeds():
    """Regression for the exact bug this module was found to have: an
    earlier version fed Counter.update() an unsorted set intersection, so
    the auto-suggested name's tie-break depended on Python's per-process
    hash randomization. A same-process test can't catch this class of bug
    by construction (PYTHONHASHSEED is fixed for the process's lifetime,
    same pattern as matching/candidates.py's title_fingerprint fix in
    Milestone 6) -- this spawns real subprocesses with different seeds."""
    import json
    import subprocess
    import sys

    script = (
        "import json\n"
        "from dmie.classification.product_type_discovery import discover_candidate_types\n"
        f"listings = {REAL_RELEVANT_LISTINGS!r}\n"
        "candidates = discover_candidate_types(listings)\n"
        "print(json.dumps([[c.suggested_name, sorted(c.listing_ids)] for c in candidates]))\n"
    )

    import os

    outputs = []
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True, env=env,
        )
        outputs.append(json.loads(result.stdout))

    assert outputs[0] == outputs[1] == outputs[2]

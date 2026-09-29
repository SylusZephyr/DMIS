from dmie.matching.candidates import generate_candidate_pairs, title_fingerprint


def test_title_fingerprint_ignores_stopwords_and_short_words():
    fp = title_fingerprint("Antinsky Denture Base Resin")
    assert "denture" not in fp  # stopword
    assert "antinsky" in fp or "resin" in fp


def test_title_fingerprint_empty_title_is_empty():
    assert title_fingerprint("") == ()
    assert title_fingerprint(None) == ()


def test_title_fingerprint_is_deterministic_on_length_ties():
    """Regression test: an earlier version sorted candidate words by
    -len(w) only, so ties among equal-length words were broken by set
    iteration order -- which is hash-randomized per Python process, not
    reproducible. 'yellow', 'orange', 'purple', 'silver' are all 6 chars;
    the alphabetical tiebreak must always pick the same 3."""
    title = "yellow orange purple silver widget"
    result = title_fingerprint(title, n=3)
    assert result == title_fingerprint(title, n=3)  # stable within a run
    assert result == ("orange", "purple", "silver")  # alphabetically-first 3 of the tied words


def test_blocking_is_far_cheaper_than_all_pairs():
    listings = [
        {"listing_id": "1", "title": "Antinsky Denture Base Resin", "brand": "Antinsky"},
        {"listing_id": "2", "title": "Unrelated Screwdriver Bit Holder", "brand": "Magoog"},
        {"listing_id": "3", "title": "Some Other Dental Product", "brand": "Acme"},
    ]
    pairs = generate_candidate_pairs(listings)
    all_pairs_count = len(listings) * (len(listings) - 1) // 2
    assert len(pairs) <= all_pairs_count


def test_same_brand_listings_become_candidates():
    listings = [
        {"listing_id": "1", "title": "Widget A", "brand": "Acme"},
        {"listing_id": "2", "title": "Widget B", "brand": "Acme"},
    ]
    pairs = generate_candidate_pairs(listings)
    assert len(pairs) == 1
    a, b, method = pairs[0]
    assert method == "brand_block"


def test_generic_brand_does_not_block_unrelated_listings_together():
    listings = [
        {"listing_id": "1", "title": "Completely Different Wax Product", "brand": "Generic"},
        {"listing_id": "2", "title": "Totally Unrelated Screwdriver Item", "brand": "Generic"},
    ]
    pairs = generate_candidate_pairs(listings)
    assert pairs == []  # generic brand alone shouldn't block two unrelated titles together


def test_near_identical_titles_different_brands_become_candidates_via_title_block():
    """Grounded in real ASINs B0GVB7J6PV / B0GW5CNJ6F (docs/entity_resolution.md)
    -- near-identical title, different brand, wouldn't be caught by brand
    blocking alone."""
    listings = [
        {
            "listing_id": "1", "brand": "yuanyuuo",
            "title": "Provisional teeth decoration Tooth Tamporary Repair Tooth Kit Fake Tooth "
                      "High hardness, durable, and reusable. Suitable for Missing, Cracked DIY "
                      "Denture Kit. (White 1 Box)",
        },
        {
            "listing_id": "2", "brand": "Generic",
            "title": "Provisional teeth decoration Tooth Tamporary Repair Tooth Kit Fake Tooth "
                      "High hardness, durable, and reusable. Suitable for Missing, Cracked DIY "
                      "Denture Kit. (2 Box)",
        },
    ]
    pairs = generate_candidate_pairs(listings)
    assert len(pairs) == 1
    _, _, method = pairs[0]
    assert method == "title_fingerprint_block"


def test_no_duplicate_pairs_when_both_blocks_fire():
    listings = [
        {"listing_id": "1", "title": "Acme Denture Reline Kit", "brand": "Acme"},
        {"listing_id": "2", "title": "Acme Denture Reline Kit Deluxe", "brand": "Acme"},
    ]
    pairs = generate_candidate_pairs(listings)
    assert len(pairs) == 1  # same pair found by both brand and title block, deduplicated

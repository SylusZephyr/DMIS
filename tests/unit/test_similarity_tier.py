"""Lightweight ML/similarity tier (architecture directive's rules ->
ML/similarity -> LLM cascade): pure TF-IDF + cosine similarity, tested
directly -- no AI, no external ML dependency.
"""


from dmie.classification.similarity_tier import build_similarity_index, classify_by_similarity


def test_near_identical_title_matches_with_high_confidence():
    titles = ["Dental Typodont Teeth Model with Removable Teeth", "Dental Base Plate Wax Sheet Casting"]
    labels = ["DM_TYPODONT", "DM_LAB_SUPPLY"]
    index = build_similarity_index(titles, labels)

    result = classify_by_similarity("Dental Typodont Teeth Model Removable Teeth Practice", index)
    assert result.status == "ok"
    assert result.label == "DM_TYPODONT"
    assert result.confidence > 0.6


def test_completely_unrelated_vocabulary_defers_rather_than_guesses():
    titles = ["Dental Typodont Teeth Model with Removable Teeth"]
    labels = ["DM_TYPODONT"]
    index = build_similarity_index(titles, labels)

    result = classify_by_similarity("Kitchen Blender Stainless Steel 1000W Motor", index)
    assert result.status == "no_confident_match"
    assert result.label is None


def test_empty_reference_index_never_crashes_or_guesses():
    index = build_similarity_index([], [])
    result = classify_by_similarity("Anything", index)
    assert result.status == "no_confident_match"
    assert result.label is None


def test_min_confidence_is_a_real_configurable_tradeoff():
    titles = ["Dental Implant Mandible Sinus Lift Training Model"]
    labels = ["DM_IMPLANT_MODEL"]
    index = build_similarity_index(titles, labels)

    loose = classify_by_similarity("Dental Sinus Training Kit", index, min_confidence=0.2)
    strict = classify_by_similarity("Dental Sinus Training Kit", index, min_confidence=0.99)
    assert loose.confidence == strict.confidence  # same real similarity score
    assert loose.status == "ok"
    assert strict.status == "no_confident_match"  # same score, different threshold -> different decision


def test_nearest_title_is_returned_for_auditability():
    titles = ["Dental Typodont Teeth Model", "Dental Base Plate Wax Sheet"]
    labels = ["DM_TYPODONT", "DM_LAB_SUPPLY"]
    index = build_similarity_index(titles, labels)

    result = classify_by_similarity("Dental Typodont Model Removable", index, reference_titles=titles)
    assert result.nearest_title == "Dental Typodont Teeth Model"

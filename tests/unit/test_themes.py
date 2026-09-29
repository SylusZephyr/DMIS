from dmie.reviews.themes import load_taxonomy, normalize_theme_key, validate_taxonomy_path


def test_load_taxonomy_has_all_six_categories():
    taxonomy = load_taxonomy()
    assert set(taxonomy.keys()) == {
        "QUALITY", "PERFORMANCE", "COMPATIBILITY", "USABILITY", "PACKAGING", "VALUE",
    }


def test_load_taxonomy_quality_subcategories():
    taxonomy = load_taxonomy()
    assert set(taxonomy["QUALITY"]) == {"durability", "breakage", "material", "manufacturing"}


def test_validate_taxonomy_path_accepts_valid_pair():
    assert validate_taxonomy_path("QUALITY", "durability") is True


def test_validate_taxonomy_path_rejects_invalid_subcategory():
    assert validate_taxonomy_path("QUALITY", "not_a_real_subcategory") is False


def test_validate_taxonomy_path_rejects_invalid_category():
    assert validate_taxonomy_path("NOT_A_CATEGORY", "durability") is False


def test_validate_taxonomy_path_rejects_mismatched_pair():
    """'durability' is real, but not under PACKAGING -- catches the model
    inventing plausible-looking but wrong combinations."""
    assert validate_taxonomy_path("PACKAGING", "durability") is False


def test_validate_taxonomy_path_rejects_none():
    assert validate_taxonomy_path(None, None) is False
    assert validate_taxonomy_path("QUALITY", None) is False


def test_normalize_theme_key_format():
    assert normalize_theme_key("QUALITY", "durability") == "QUALITY/durability"

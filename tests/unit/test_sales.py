from dmie.market.sales import annualize, best_selling_listing, sales_distribution


def test_annualize_multiplies_by_12():
    assert annualize(100.0) == 1200.0


def test_annualize_none_stays_none():
    assert annualize(None) is None


def test_annualize_zero_is_zero_not_none():
    assert annualize(0.0) == 0.0


def test_best_selling_listing_picks_highest_sales():
    listings = [
        {"listing_id": "A", "monthly_sales": 50.0},
        {"listing_id": "B", "monthly_sales": 200.0},
        {"listing_id": "C", "monthly_sales": 100.0},
    ]
    best = best_selling_listing(listings)
    assert best["listing_id"] == "B"


def test_best_selling_listing_ignores_nulls():
    listings = [
        {"listing_id": "A", "monthly_sales": None},
        {"listing_id": "B", "monthly_sales": 50.0},
    ]
    assert best_selling_listing(listings)["listing_id"] == "B"


def test_best_selling_listing_none_when_no_data_at_all():
    listings = [{"listing_id": "A", "monthly_sales": None}, {"listing_id": "B", "monthly_sales": None}]
    assert best_selling_listing(listings) is None


def test_best_selling_listing_tie_break_is_deterministic():
    listings = [
        {"listing_id": "B0H6RLQ6YZ", "monthly_sales": 50.0},
        {"listing_id": "B0CXMQ7DFZ", "monthly_sales": 50.0},
    ]
    result1 = best_selling_listing(listings)
    result2 = best_selling_listing(list(reversed(listings)))
    assert result1["listing_id"] == result2["listing_id"]


def test_sales_distribution_empty_when_no_data():
    dist = sales_distribution([None, None])
    assert dist["count"] == 0
    assert dist["mean"] is None


def test_sales_distribution_basic_stats():
    dist = sales_distribution([50.0, 100.0, None, 200.0])
    assert dist["count"] == 3
    assert dist["min"] == 50.0
    assert dist["max"] == 200.0
    assert dist["median"] == 100.0
    assert dist["mean"] == 350.0 / 3

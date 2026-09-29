from dmie.market.aggregation import compute_category_metrics, compute_product_metrics

# Grounded in the real product Pf598530a9a (B0CXMQ7DFZ / B0H6RLQ6YZ,
# the exact-duplicate-title wax pair from Milestone 6) plus one singleton.
LISTINGS = [
    {"listing_id": "B0CXMQ7DFZ", "price": 8.99, "monthly_sales": 50.0, "monthly_revenue": 449.0,
     "rating": 3.9, "review_count": None, "product_id": "P1", "product_type": None},
    {"listing_id": "B0H6RLQ6YZ", "price": None, "monthly_sales": 50.0, "monthly_revenue": None,
     "rating": None, "review_count": None, "product_id": "P1", "product_type": None},
    {"listing_id": "B0CXTC26TV", "price": 59.0, "monthly_sales": None, "monthly_revenue": None,
     "rating": 3.9, "review_count": None, "product_id": "P2", "product_type": "resin"},
]


def test_compute_product_metrics_returns_one_row_per_product():
    metrics = compute_product_metrics("denture_base", LISTINGS)
    assert {m.product_id for m in metrics} == {"P1", "P2"}


def test_multi_listing_product_aggregates_correctly():
    metrics = {m.product_id: m for m in compute_product_metrics("denture_base", LISTINGS)}
    p1 = metrics["P1"]
    assert p1.total_listing_count == 2
    assert p1.listings_with_price_data == 1  # only B0CXMQ7DFZ has a price
    assert p1.listings_with_sales_data == 2  # both have monthly_sales
    assert p1.best_listing_observed_monthly_sales == 50.0
    assert p1.best_listing_annualized_observed_sales == 600.0
    assert p1.observed_monthly_revenue == 449.0  # sum ignoring the None
    assert p1.annualized_observed_revenue == 5388.0
    assert p1.min_price == 8.99 and p1.max_price == 8.99 and p1.median_price == 8.99
    assert p1.representative_price == 8.99  # best listing's own price
    assert p1.rating == 3.9  # average of the single non-null rating
    assert p1.review_count is None  # both listings have no review-count data


def test_singleton_product_has_no_best_listing_when_no_sales_data():
    metrics = {m.product_id: m for m in compute_product_metrics("denture_base", LISTINGS)}
    p2 = metrics["P2"]
    assert p2.total_listing_count == 1
    assert p2.best_listing_id is None  # no sales data at all -- not guessed
    assert p2.best_listing_observed_monthly_sales is None
    assert p2.representative_price == 59.0  # falls back to median (== its only price)


def test_compute_category_metrics_aggregates_across_products():
    product_metrics = compute_product_metrics("denture_base", LISTINGS)
    category = compute_category_metrics("denture_base", LISTINGS, product_metrics)
    assert category.total_product_count == 2
    assert category.total_listing_count == 3
    assert category.total_observed_monthly_sales == 100.0  # 50 + 50, P2 has none
    assert category.annualized_observed_sales == 1200.0
    assert category.total_observed_monthly_revenue == 449.0
    assert category.price_distribution["count"] == 2  # only 2 of 3 listings have a price


def test_category_product_type_distribution_reflects_per_product_type():
    product_metrics = compute_product_metrics("denture_base", LISTINGS)
    category = compute_category_metrics("denture_base", LISTINGS, product_metrics)
    # P1's listings have no product_type -> unclassified; P2 has "resin"
    assert category.product_type_distribution == {"unclassified": 1, "resin": 1}


def test_category_metrics_with_no_listings_at_all():
    category = compute_category_metrics("empty_category", [], [])
    assert category.total_product_count == 0
    assert category.total_listing_count == 0
    assert category.total_observed_monthly_sales is None
    assert category.listing_concentration_hhi is None

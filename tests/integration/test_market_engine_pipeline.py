"""End-to-end: compute market metrics from the real denture_base data in
an isolated in-memory DB, and check the known multi-listing product's
numbers (grounded in the real B0CXMQ7DFZ/B0H6RLQ6YZ pair, see
tests/unit/test_aggregation.py's synthetic version of the same case).
"""

import duckdb
import pytest

from dmie.cleaning.normalize import make_listing_id
from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import (
    replace_category_market_metrics,
    replace_product_market_metrics,
    update_best_listing_flags,
)
from dmie.market.aggregation import compute_category_metrics, compute_product_metrics, fetch_relevant_listings

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"
CATEGORY_ID = "denture_base"


@pytest.fixture(scope="module")
def market():
    prod_con = get_connection()
    try:
        listings = fetch_relevant_listings(prod_con, CATEGORY_ID)
    finally:
        prod_con.close()

    product_metrics = compute_product_metrics(CATEGORY_ID, listings)
    category_metrics = compute_category_metrics(CATEGORY_ID, listings, product_metrics)

    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    # listings + product_listings need rows for update_best_listing_flags
    # to affect -- it scopes its clear via listings.category_id since
    # product_listings has no category_id of its own.
    con.executemany(
        "INSERT INTO listings (listing_id, category_id) VALUES (?, ?)",
        [[listing["listing_id"], CATEGORY_ID] for listing in listings],
    )
    con.executemany(
        "INSERT INTO product_listings (product_id, listing_id, match_method, match_confidence, review_status) "
        "VALUES (?, ?, 'test', 1.0, 'auto_accepted')",
        [[m.product_id, listing["listing_id"]] for m in product_metrics for listing in listings if listing["product_id"] == m.product_id],
    )
    replace_product_market_metrics(con, CATEGORY_ID, product_metrics)
    replace_category_market_metrics(con, CATEGORY_ID, [category_metrics])
    update_best_listing_flags(con, CATEGORY_ID, product_metrics)

    return con, listings, product_metrics, category_metrics


def test_relevant_population_matches_classification(market):
    _, listings, _, _ = market
    # sanity: every fetched listing actually has a resolved product_id
    assert all(listing.get("product_id") for listing in listings)


def test_known_multi_listing_product_metrics(market):
    _, _, product_metrics, _ = market
    by_id = {m.product_id: m for m in product_metrics}
    wax_product_id = None
    for m in product_metrics:
        if m.best_listing_id in (make_listing_id("B0CXMQ7DFZ"), make_listing_id("B0H6RLQ6YZ")):
            wax_product_id = m.product_id
            break
    assert wax_product_id is not None, "the known wax duplicate-listing product wasn't found"
    metrics = by_id[wax_product_id]
    assert metrics.total_listing_count == 2
    assert metrics.observed_monthly_revenue == 449.0
    assert metrics.annualized_observed_revenue == 5388.0
    assert metrics.best_listing_observed_monthly_sales == 50.0


def test_category_metrics_are_internally_consistent(market):
    _, listings, product_metrics, category_metrics = market
    assert category_metrics.total_listing_count == len(listings)
    assert category_metrics.total_product_count == len(product_metrics)
    assert sum(m.total_listing_count for m in product_metrics) == len(listings)


def test_best_listing_flags_written_to_product_listings(market):
    con, _, product_metrics, _ = market
    for m in product_metrics:
        if m.best_listing_id is None:
            continue
        flag = con.execute(
            "SELECT is_best_listing FROM product_listings WHERE listing_id = ?", [m.best_listing_id]
        ).fetchone()[0]
        assert flag is True


def test_no_metric_silently_treats_missing_as_zero(market):
    """Every product with zero sales-data coverage must report None, not 0."""
    _, _, product_metrics, _ = market
    for m in product_metrics:
        if m.listings_with_sales_data == 0:
            assert m.best_listing_observed_monthly_sales is None
            assert m.best_listing_annualized_observed_sales is None


def test_fetch_relevant_listings_reads_real_product_type_classifications(market):
    """Regression for a confirmed bug (full_system_audit_report.md Bug #1):
    fetch_relevant_listings used to read product_type from
    listing_classification, which has been permanently NULL since M11
    Stage 3 moved real classifier output to
    listing_product_type_classification. This silently made
    category_market_metrics.product_type_distribution report
    {"unclassified": N} forever, regardless of real classification state.
    The live DB has real classify_product_types.py output for
    denture_base -- at least one listing here must show it."""
    _, listings, _, category_metrics = market
    classified = {listing["product_type"] for listing in listings if listing.get("product_type")}
    assert classified, "no real product_type came through fetch_relevant_listings -- the bug is back"
    assert classified <= {"DB_RESIN", "DB_WAX_PLATE", "DB_RELINE"}  # never an invented value
    assert category_metrics.product_type_distribution != {"unclassified": len(listings)}

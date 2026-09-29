"""Compute product-level and category-level market metrics.

Population: only listings classified relevance_class = 'RELEVANT'
(Milestone 5). See docs/market_metrics.md for every formula.

Usage: python scripts/calculate_market.py [category_id]
Defaults to the denture_base pilot category.
"""

import sys

from dmie.database.connection import get_connection
from dmie.database.repository import (
    replace_category_market_metrics,
    replace_product_market_metrics,
    update_best_listing_flags,
)
from dmie.market.aggregation import compute_category_metrics, compute_product_metrics, fetch_relevant_listings


def run(category_id: str) -> None:
    con = get_connection()
    try:
        listings = fetch_relevant_listings(con, category_id)
        product_metrics = compute_product_metrics(category_id, listings)
        category_metrics = compute_category_metrics(category_id, listings, product_metrics)

        replace_product_market_metrics(con, category_id, product_metrics)
        replace_category_market_metrics(con, category_id, [category_metrics])
        update_best_listing_flags(con, category_id, product_metrics)
    finally:
        con.close()

    print(f"category: {category_id}")
    print(f"relevant listings: {len(listings)}")
    print(f"products: {category_metrics.total_product_count}")
    print(f"total_observed_monthly_sales: {category_metrics.total_observed_monthly_sales}")
    print(f"total_observed_monthly_revenue: {category_metrics.total_observed_monthly_revenue}")
    print(f"listing_concentration_hhi: {category_metrics.listing_concentration_hhi}")
    print(f"price_distribution: {category_metrics.price_distribution}")


if __name__ == "__main__":
    category = sys.argv[1] if len(sys.argv) > 1 else "denture_base"
    run(category)

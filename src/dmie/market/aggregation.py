"""Orchestrates product-level and category-level market metrics.

Population scope: only listings classified relevance_class = 'RELEVANT'
(Milestone 5) are included. UNCERTAIN and IRRELEVANT listings are excluded
by design — including unverified listings would contaminate every metric
below with the exact category contamination the project set out to
purify (docs/data_dictionary.md). See docs/market_metrics.md.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import duckdb

from dmie.market.competition import listing_concentration, product_type_distribution
from dmie.market.pricing import price_distribution, price_stats, representative_price
from dmie.market.revenue import total_observed_monthly_revenue
from dmie.market.sales import annualize, best_selling_listing, sales_distribution

_LISTING_COLUMNS = [
    "listing_id", "price", "monthly_sales", "monthly_revenue",
    "rating", "review_count", "product_id", "product_type",
]


@dataclass
class ProductMetrics:
    product_id: str
    category_id: str | None
    total_listing_count: int
    listings_with_price_data: int
    listings_with_sales_data: int
    best_listing_id: str | None
    best_listing_observed_monthly_sales: float | None
    best_listing_annualized_observed_sales: float | None
    observed_monthly_revenue: float | None
    annualized_observed_revenue: float | None
    min_price: float | None
    max_price: float | None
    median_price: float | None
    representative_price: float | None
    rating: float | None
    review_count: int | None


@dataclass
class CategoryMetrics:
    category_id: str
    total_product_count: int
    total_listing_count: int
    total_observed_monthly_sales: float | None
    annualized_observed_sales: float | None
    total_observed_monthly_revenue: float | None
    annualized_observed_revenue: float | None
    price_distribution: dict
    sales_distribution: dict
    listing_concentration_hhi: float | None
    product_type_distribution: dict


def fetch_relevant_listings(con: duckdb.DuckDBPyConnection, category_id: str) -> list[dict]:
    """product_type comes from listing_product_type_classification (M11
    Stage 2), NOT listing_classification.product_type -- that column has
    been permanently NULL since Stage 3 moved real classifier output to
    its own table specifically to avoid colliding with
    listing_classification's relevance-decision columns (see
    DECISIONS.md "M11 Stage 3"). This function independently needed the
    same fix scripts/resolve_products.py already got -- see DECISIONS.md
    "Bug fix: category_market_metrics.product_type_distribution was
    stale" for how this was found. UNCERTAIN is not a real classified
    type -- mapped to NULL, same as an unclassified listing."""
    rows = con.execute(
        """
        SELECT l.listing_id, l.price, l.monthly_sales, l.monthly_revenue,
               l.rating, l.review_count, pl.product_id,
               CASE WHEN ptc.product_type = 'UNCERTAIN' THEN NULL ELSE ptc.product_type END AS product_type
        FROM listings l
        JOIN listing_classification lc ON lc.listing_id = l.listing_id
        JOIN product_listings pl ON pl.listing_id = l.listing_id
        LEFT JOIN listing_product_type_classification ptc ON ptc.listing_id = l.listing_id
        WHERE lc.relevance_class = 'RELEVANT' AND l.category_id = ?
        """,
        [category_id],
    ).fetchall()
    return [dict(zip(_LISTING_COLUMNS, row)) for row in rows]


def _product_type_for_group(group: list[dict]) -> str | None:
    """The most common non-null product_type among a product's listings,
    or None if none of them have one classified yet."""
    types = [listing["product_type"] for listing in group if listing.get("product_type")]
    if not types:
        return None
    return Counter(types).most_common(1)[0][0]


def compute_product_metrics(category_id: str, listings: list[dict]) -> list[ProductMetrics]:
    by_product: dict[str, list[dict]] = defaultdict(list)
    for listing in listings:
        by_product[listing["product_id"]].append(listing)

    results = []
    for product_id, group in by_product.items():
        best = best_selling_listing(group)
        best_sales = best["monthly_sales"] if best else None
        stats = price_stats([listing["price"] for listing in group])
        monthly_revenue = total_observed_monthly_revenue(group)
        ratings = [listing["rating"] for listing in group if listing.get("rating") is not None]
        review_counts = [listing["review_count"] for listing in group if listing.get("review_count") is not None]

        results.append(ProductMetrics(
            product_id=product_id,
            category_id=category_id,
            total_listing_count=len(group),
            listings_with_price_data=stats["count"],
            listings_with_sales_data=sum(1 for listing in group if listing.get("monthly_sales") is not None),
            best_listing_id=best["listing_id"] if best else None,
            best_listing_observed_monthly_sales=best_sales,
            best_listing_annualized_observed_sales=annualize(best_sales),
            observed_monthly_revenue=monthly_revenue,
            annualized_observed_revenue=annualize(monthly_revenue),
            min_price=stats["min_price"],
            max_price=stats["max_price"],
            median_price=stats["median_price"],
            representative_price=representative_price(best, stats["median_price"]),
            rating=(sum(ratings) / len(ratings)) if ratings else None,
            review_count=(sum(review_counts) if review_counts else None),
        ))
    return results


def compute_category_metrics(
    category_id: str, listings: list[dict], product_metrics: list[ProductMetrics]
) -> CategoryMetrics:
    sales_values = [listing["monthly_sales"] for listing in listings if listing.get("monthly_sales") is not None]
    revenue_values = [listing["monthly_revenue"] for listing in listings if listing.get("monthly_revenue") is not None]
    total_monthly_sales = sum(sales_values) if sales_values else None
    total_monthly_revenue = sum(revenue_values) if revenue_values else None

    product_types = [_product_type_for_group(
        [listing for listing in listings if listing["product_id"] == pm.product_id]
    ) for pm in product_metrics]

    return CategoryMetrics(
        category_id=category_id,
        total_product_count=len(product_metrics),
        total_listing_count=len(listings),
        total_observed_monthly_sales=total_monthly_sales,
        annualized_observed_sales=annualize(total_monthly_sales),
        total_observed_monthly_revenue=total_monthly_revenue,
        annualized_observed_revenue=annualize(total_monthly_revenue),
        price_distribution=price_distribution([listing["price"] for listing in listings]),
        sales_distribution=sales_distribution(sales_values),
        listing_concentration_hhi=listing_concentration(
            [pm.total_listing_count for pm in product_metrics]
        )["hhi"],
        product_type_distribution=product_type_distribution(product_types),
    )

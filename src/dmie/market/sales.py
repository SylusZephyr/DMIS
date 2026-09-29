"""Deterministic sales calculations. Pure Python — no LLM arithmetic, per
PRINCIPLES.md principle 4 and this milestone's explicit instruction.

Field names say "observed", not "market size" or "sales" alone.
SellerSprite-derived numbers are observations of what SellerSprite could
see for the listings we've resolved as relevant, not a claim of total
Amazon market volume. See docs/market_metrics.md.
"""

from __future__ import annotations

import statistics

MONTHS_PER_YEAR = 12


def annualize(monthly_value: float | None) -> float | None:
    """monthly_value * 12. Assumes the observed month is representative of
    a typical month — a documented caveat (docs/market_metrics.md), not
    hidden math. Returns None (not 0) when the input is None."""
    if monthly_value is None:
        return None
    return monthly_value * MONTHS_PER_YEAR


def best_selling_listing(listings: list[dict]) -> dict | None:
    """The listing with the highest non-null observed_monthly_sales among
    a product's listings (PRINCIPLES.md's definition of best-selling listing).
    Returns None if no listing in the group has any sales data at all —
    never guesses using price, rating, or any other proxy."""
    candidates = [listing for listing in listings if listing.get("monthly_sales") is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda listing: (listing["monthly_sales"], listing["listing_id"]))


def sales_distribution(sales_values: list[float | None]) -> dict:
    """count/min/max/median/mean of observed monthly sales, no bucket
    bands. Unlike price_distribution, this is deliberately not banded:
    with the current pilot's sales-data coverage (a small minority of
    listings report monthly_sales at all — see docs/data_dictionary.md),
    fixed bands would suggest a granularity the data doesn't support.
    Add bands once real coverage improves."""
    values = [v for v in sales_values if v is not None]
    if not values:
        return {"count": 0, "min": None, "max": None, "median": None, "mean": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "median": statistics.median(values),
        "mean": statistics.mean(values),
    }

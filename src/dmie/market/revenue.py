"""Deterministic revenue calculations. See docs/market_metrics.md."""

from __future__ import annotations

from dmie.market.sales import annualize


def total_observed_monthly_revenue(listings: list[dict]) -> float | None:
    """Sum of monthly_revenue across a product's listings, ignoring
    listings with no revenue data. None (not 0) when NO listing in the
    group has revenue data — "we don't know" is not the same as "$0",
    and would otherwise silently understate every product's revenue."""
    values = [listing["monthly_revenue"] for listing in listings if listing.get("monthly_revenue") is not None]
    return sum(values) if values else None


def annualized_observed_revenue(monthly_revenue: float | None) -> float | None:
    return annualize(monthly_revenue)

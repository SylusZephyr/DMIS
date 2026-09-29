"""Deterministic pricing calculations. See docs/market_metrics.md."""

from __future__ import annotations

import statistics

# (low inclusive, high exclusive, label)
PRICE_BANDS = [
    (0, 20, "<$20"),
    (20, 50, "$20-50"),
    (50, 100, "$50-100"),
    (100, 250, "$100-250"),
    (250, float("inf"), "$250+"),
]


def price_stats(prices: list[float | None]) -> dict:
    values = [p for p in prices if p is not None]
    if not values:
        return {"min_price": None, "max_price": None, "median_price": None, "count": 0}
    return {
        "min_price": min(values),
        "max_price": max(values),
        "median_price": statistics.median(values),
        "count": len(values),
    }


def representative_price(best_listing: dict | None, median_price: float | None) -> float | None:
    """The best-selling listing's price when a best-seller is
    determinable (the price most buyers of this product actually paid,
    weighted toward its dominant listing) — falls back to median price
    when no listing has sales data to establish a best-seller."""
    if best_listing is not None and best_listing.get("price") is not None:
        return best_listing["price"]
    return median_price


def price_band(price: float) -> str:
    for lo, hi, label in PRICE_BANDS:
        if lo <= price < hi:
            return label
    return "unknown"


def price_distribution(prices: list[float | None]) -> dict:
    values = [p for p in prices if p is not None]
    if not values:
        return {"count": 0, "min": None, "max": None, "median": None, "mean": None, "bands": {}}
    bands: dict[str, int] = {label: 0 for _, _, label in PRICE_BANDS}
    for p in values:
        bands[price_band(p)] += 1
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "bands": bands,
    }

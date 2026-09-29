"""Deterministic category-level concentration/distribution calculations.
See docs/market_metrics.md."""

from __future__ import annotations

from collections import Counter


def herfindahl_hirschman_index(counts: list[int]) -> float | None:
    """Standard HHI (0-10000 scale) over listing counts per product.
    0 = every product has one listing, 10000 = a single product has
    every listing. None when there's nothing to measure (empty/all-zero
    input), not 0 — 0 would falsely read as "perfectly unconcentrated"."""
    total = sum(counts)
    if total == 0:
        return None
    return sum((count / total) ** 2 for count in counts) * 10000


def listing_concentration(listing_counts_per_product: list[int]) -> dict:
    total = sum(listing_counts_per_product)
    top = max(listing_counts_per_product) if listing_counts_per_product else 0
    return {
        "hhi": herfindahl_hirschman_index(listing_counts_per_product),
        "top_product_listing_share": (top / total) if total else None,
    }


def product_type_distribution(product_types: list[str | None]) -> dict:
    """Distribution of product_type across products. Missing values are
    counted explicitly as 'unclassified', never dropped silently —
    product_type classification hasn't been built yet (see Milestone 5's
    PROGRESS.md), so this will legitimately show 'unclassified' for
    everything until it is."""
    counter = Counter(product_type if product_type else "unclassified" for product_type in product_types)
    return dict(counter)

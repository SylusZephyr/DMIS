"""Stage 4 -- Product entity resolution (100 listings != 100 products).

Wraps ``dmie.engine.dedup.resolve_products`` (identity graph, spec/price/
brand vetoes, anti-chaining union-find, Product Master with best listing).
"""

from __future__ import annotations

from dmie.engine.dedup import DedupResult, resolve_products


DIRECT_LIMIT = 5_000  # up to here the exact v1 implementation runs unchanged


def resolve(listings, direct_limit: int = DIRECT_LIMIT) -> DedupResult:
    if len(listings) <= direct_limit:
        return resolve_products(listings)
    from dip.pipeline.product_resolution.fast import resolve_at_scale
    return resolve_at_scale(listings)

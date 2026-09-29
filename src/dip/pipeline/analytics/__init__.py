"""Stage 6 -- Market analytics: capacity, competition, price structure.

Per-market math wraps ``dmie.engine.market.compute_market`` (product-level,
never listing-level; observed totals with coverage). Cross-market analytics
run as DuckDB SQL over the curated Parquet zone so they scale with the lake.
"""

from __future__ import annotations

import pandas as pd

from dmie.engine.market import MarketResult, compute_market
from dip.storage import lake


def market_capacity(products: pd.DataFrame, listings: pd.DataFrame, segments: pd.DataFrame) -> MarketResult:
    return compute_market(products, listings, segments)


def universe_totals() -> pd.DataFrame:
    """One row per market from the lake: products, listings, revenue, sales coverage."""
    if not lake.has_curated("products"):
        return pd.DataFrame(columns=["market", "products", "listings", "monthly_revenue", "sales_coverage"])
    return lake.query(f"""
        SELECT market, count(*) AS products, sum(listing_count) AS listings,
               sum(monthly_revenue) AS monthly_revenue, avg(CASE WHEN monthly_sales IS NULL THEN 0 ELSE 1 END) AS sales_coverage,
               avg(opportunity_score) AS avg_opportunity
        FROM read_parquet('{lake.curated_path("products")}', hive_partitioning=true, union_by_name=true)
        GROUP BY market ORDER BY monthly_revenue DESC NULLS LAST""")


def brand_leaderboard(market: str | None = None, limit: int = 20) -> pd.DataFrame:
    if not lake.has_curated("products", market):
        return pd.DataFrame()
    return lake.query(f"""
        SELECT brand, count(*) AS products, sum(monthly_revenue) AS monthly_revenue, sum(monthly_sales) AS monthly_sales,
               avg(rating) AS avg_rating, count(DISTINCT market) AS markets
        FROM read_parquet('{lake.curated_path("products", market)}', hive_partitioning=true, union_by_name=true)
        WHERE brand IS NOT NULL GROUP BY brand ORDER BY monthly_revenue DESC NULLS LAST LIMIT {int(limit)}""")

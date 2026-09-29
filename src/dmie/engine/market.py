"""Market Capacity Engine (Module 6).

Deterministic, product-level (never listing-level) market metrics:

* demand   -- monthly / annual unit sales
* revenue  -- monthly / annual revenue
* competition -- products, listings, sellers, brands, brand HHI, top-brand share
* market size -- total category revenue, segment share
* price analysis -- low / middle / premium tiers from category quantiles

Missing sales are *unknown*, not zero: every total is "observed" and is
reported with its coverage (share of products that had a sales value).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dmie.engine.config import section


@dataclass
class MarketResult:
    products: pd.DataFrame     # Product Master + demand/revenue/price_tier columns
    segments: pd.DataFrame     # per-segment capacity + competition
    category: dict             # whole-market summary


def hhi(shares: pd.Series) -> float | None:
    """Herfindahl-Hirschman Index on a 0..10,000 scale."""
    total = shares.sum()
    if not total or total <= 0:
        return None
    s = shares / total * 100
    return float((s ** 2).sum())


def concentration_label(h: float | None) -> str:
    if h is None:
        return "unknown"
    if h < 1500:
        return "fragmented"
    if h < 2500:
        return "moderately concentrated"
    return "highly concentrated"


def price_tiers(prices: pd.Series, cuts: list[float]) -> tuple[pd.Series, tuple[float, float] | None]:
    known = prices.dropna()
    if len(known) < 3:
        return pd.Series([None] * len(prices), index=prices.index, dtype="object"), None
    lo, hi = known.quantile(cuts[0]), known.quantile(cuts[1])
    tier = np.select([prices <= lo, prices <= hi, prices > hi], ["low", "middle", "premium"], default=None)
    tier = pd.Series(tier, index=prices.index, dtype="object").where(prices.notna(), None)
    return tier, (float(lo), float(hi))


def _brand_share_basis(g: pd.DataFrame) -> tuple[pd.Series, str]:
    if g["monthly_revenue"].notna().any():
        return g.groupby("brand")["monthly_revenue"].sum(), "revenue"
    if g["monthly_sales"].notna().any():
        return g.groupby("brand")["monthly_sales"].sum(), "sales"
    return g.groupby("brand").size().astype(float), "product_count"


def _competition(g: pd.DataFrame, listings: pd.DataFrame | None) -> dict:
    shares, basis = _brand_share_basis(g.assign(brand=g["brand"].fillna("(unknown)")))
    h = hhi(shares)
    top = shares.sort_values(ascending=False)
    total = top.sum()
    sellers = None
    if listings is not None and listings["seller"].notna().any():
        sellers = int(listings["seller"].dropna().nunique())
    return {
        "brands": int(g["brand"].dropna().nunique()),
        "sellers": sellers,
        "brand_hhi": round(h, 1) if h is not None else None,
        "concentration": concentration_label(h),
        "top_brand": top.index[0] if len(top) else None,
        "top_brand_share": float(top.iloc[0] / total) if len(top) and total else None,
        "top3_brand_share": float(top.iloc[:3].sum() / total) if len(top) and total else None,
        "share_basis": basis,
    }


def compute_market(products: pd.DataFrame, listings: pd.DataFrame | None = None,
                   segments: pd.DataFrame | None = None) -> MarketResult:
    cfg = section("market")
    months = cfg.get("months_per_year", 12)
    p = products.copy()
    p["monthly_sales"] = p["total_sales"]
    p["annual_sales"] = p["monthly_sales"] * months
    p["monthly_revenue"] = p["revenue"]
    p["annual_revenue"] = p["monthly_revenue"] * months
    p["price"] = p["price_median"]
    p["price_tier"], cuts = price_tiers(p["price"], cfg.get("price_tiers", [0.33, 0.66]))
    total_rev = p["monthly_revenue"].sum(min_count=1)

    seg_rows = []
    for seg, g in p.groupby("segment_id", dropna=False):
        lst = listings[listings["segment_id"] == seg] if listings is not None and "segment_id" in listings else None
        rev = g["monthly_revenue"].sum(min_count=1)
        sales = g["monthly_sales"].sum(min_count=1)
        tiers = {}
        for t in ("low", "middle", "premium"):
            gt = g[g["price_tier"] == t]
            tiers[f"{t}_products"] = int(len(gt))
            tiers[f"{t}_revenue"] = float(gt["monthly_revenue"].sum()) if gt["monthly_revenue"].notna().any() else None
        row = {
            "segment_id": seg,
            "segment_label": g["product_type"].dropna().mode().iloc[0] if g["product_type"].notna().any() else seg,
            "family_id": g["family_id"].dropna().mode().iloc[0] if "family_id" in g and g["family_id"].notna().any() else None,
            "products": int(len(g)),
            "listings": int(g["listing_count"].sum()),
            "monthly_sales": None if pd.isna(sales) else float(sales),
            "annual_sales": None if pd.isna(sales) else float(sales * months),
            "monthly_revenue": None if pd.isna(rev) else float(rev),
            "annual_revenue": None if pd.isna(rev) else float(rev * months),
            "revenue_share": float(rev / total_rev) if pd.notna(rev) and total_rev else None,
            "sales_coverage": float(g["monthly_sales"].notna().mean()),
            "price_min": float(g["price"].min()) if g["price"].notna().any() else None,
            "price_median": float(g["price"].median()) if g["price"].notna().any() else None,
            "price_max": float(g["price"].max()) if g["price"].notna().any() else None,
            "price_iqr": float(g["price"].quantile(0.75) - g["price"].quantile(0.25)) if g["price"].notna().sum() >= 2 else None,
            "avg_rating": float(g["rating"].mean()) if g["rating"].notna().any() else None,
            "median_reviews": float(g["reviews"].median()) if g["reviews"].notna().any() else None,
            **_competition(g, lst),
            **tiers,
        }
        seg_rows.append(row)
    seg_df = pd.DataFrame(seg_rows)
    if segments is not None and len(seg_df):
        extra = segments[["segment_id", "family_label", "top_terms", "dominant_specs"]]
        seg_df = seg_df.merge(extra, on="segment_id", how="left")
    if len(seg_df):
        seg_df = seg_df.sort_values("monthly_revenue", ascending=False, na_position="last").reset_index(drop=True)

    comp = _competition(p, listings)
    sales_total = p["monthly_sales"].sum(min_count=1)
    category = {
        "products": int(len(p)),
        "listings": int(p["listing_count"].sum()) if len(p) else 0,
        "segments": int(len(seg_df)),
        "monthly_sales": None if pd.isna(sales_total) else float(sales_total),
        "annual_sales": None if pd.isna(sales_total) else float(sales_total * months),
        "monthly_revenue": None if pd.isna(total_rev) else float(total_rev),
        "annual_revenue": None if pd.isna(total_rev) else float(total_rev * months),
        "sales_coverage": float(p["monthly_sales"].notna().mean()) if len(p) else 0.0,
        "price_min": float(p["price"].min()) if p["price"].notna().any() else None,
        "price_median": float(p["price"].median()) if p["price"].notna().any() else None,
        "price_max": float(p["price"].max()) if p["price"].notna().any() else None,
        "price_tier_cuts": cuts,
        "avg_rating": float(p["rating"].mean()) if p["rating"].notna().any() else None,
        **comp,
        "revenue_note": "observed totals over products with a sales value; missing sales are not imputed",
    }
    return MarketResult(p, seg_df, category)

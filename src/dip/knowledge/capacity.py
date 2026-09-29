"""Market capacity and leaf intelligence (spec 21-26, 37-38, 41).

For the category, every segment and every taxonomy node:

* counts      -- canonical products, listings, brands, sellers (listings are never counted as products)
* demand      -- modeled units and revenue (demand model; interval combined across products as
                 sqrt of the sum of squared half-widths, i.e. assuming independent products -- stated in
                 ``interval_method``), the source's own revenue estimates summed separately (kind
                 ``estimated``, a floor when badges are ranges), and ``offline_adjusted`` left empty until
                 offline evidence exists (spec 23)
* price       -- min, p25, median, mean, p75, max; entry / mass / premium tier boundaries (spec 22)
* quality     -- review-weighted rating where review counts exist, else the plain mean (stated)
* confidence  -- median Dental Confidence and median data confidence of the products
* competition -- brand shares on modeled revenue: HHI, top-3/5/10 share, long tail (spec 38)
* top listing -- the configured best-listing rule (spec 21)

Revenue is aggregated at canonical-product level before any higher level (spec 26), so one product
sold through 20 listings is counted once.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import numpy as np
import pandas as pd

from dip.knowledge import config, landed_cost


def _cfg() -> dict:
    return config()["capacity"]


def _col(df: pd.DataFrame, col: str) -> pd.Series:
    """A numeric column as a Series (all-NaN when the column is absent)."""
    return pd.to_numeric(df[col], errors="coerce") if col in df else pd.Series(np.nan, index=df.index, dtype=float)


def best_listings(listings: pd.DataFrame) -> pd.DataFrame:
    """Per product: the best-selling listing and the metric that decided it (spec 21)."""
    c = _cfg()
    L = listings.copy()
    rows = []
    for pid, g in L.groupby("product_id", sort=False):
        chosen, basis = None, None
        for m in c["best_listing_metrics"]:
            if m in g and pd.to_numeric(g[m], errors="coerce").notna().any():
                v = pd.to_numeric(g[m], errors="coerce")
                top = g[v == v.max()]
                for tb in c.get("best_listing_tie_breakers", []):
                    if len(top) > 1 and tb in top and pd.to_numeric(top[tb], errors="coerce").notna().any():
                        t = pd.to_numeric(top[tb], errors="coerce")
                        top = top[t == t.max()]
                chosen, basis = top.iloc[0], m
                break
        if chosen is None:
            chosen, basis = g.iloc[0], "no sales evidence (first listing)"
        rows.append({"product_id": pid, "best_listing": str(chosen["id"]), "best_listing_basis": basis})
    return pd.DataFrame(rows)


def _combine_interval(est: pd.Series, lo: pd.Series | None, hi: pd.Series | None) -> tuple[float | None, float | None, float | None]:
    e = pd.to_numeric(est, errors="coerce")
    if not e.notna().any():
        return None, None, None
    total = float(e.sum())
    if lo is None or hi is None:
        return total, None, None
    lo_, hi_ = pd.to_numeric(lo, errors="coerce"), pd.to_numeric(hi, errors="coerce")
    down = float(np.sqrt(((e - lo_).clip(lower=0) ** 2).sum()))
    up = float(np.sqrt(((hi_ - e).clip(lower=0) ** 2).sum()))
    return total, max(total - down, 0.0), total + up


def _concentration(P: pd.DataFrame) -> dict:
    rev = _col(P, "revenue_est")
    if "brand" not in P or not rev.notna().any() or rev.sum() <= 0:
        return {"hhi": None, **{f"top{k}_share": None for k in _cfg()["concentration_top"]}, "long_tail_share": None, "top_brand": None}
    by = rev.groupby(P["brand"].fillna("(no brand)")).sum().sort_values(ascending=False)
    share = by / by.sum()
    out = {"hhi": round(float(((share * 100) ** 2).sum()), 1), "top_brand": str(share.index[0])}
    ks = _cfg()["concentration_top"]
    for k in ks:
        out[f"top{k}_share"] = round(float(share.iloc[:k].sum()), 4)
    out["long_tail_share"] = round(float(share.iloc[max(ks):].sum()), 4) if len(share) > max(ks) else 0.0
    return out


JOINT = "joint simulation (the same draws as the market interval)"
INDEPENDENT = "sqrt of summed squared half-widths of product intervals (independent products)"


def intel(P: pd.DataFrame, L: pd.DataFrame, market: str | None = None, joint: dict | None = None) -> dict:
    """Leaf intelligence for one set of canonical products and their listings. ``joint``: {"revenue": (est, lo, hi),
    "units": (...)} from the metrics engine's joint simulation (MetricsOutput.interval_for) -- used whenever given,
    so a scope never has a second interval; the independence approximation is only the fallback."""
    tiers = _cfg()["price_tiers"]
    price = _col(P, "price_median").dropna()
    units, u_lo, u_hi = _combine_interval(P.get("units_est", pd.Series(dtype=float)), P.get("units_lo"), P.get("units_hi"))
    rev, r_lo, r_hi = _combine_interval(P.get("revenue_est", pd.Series(dtype=float)), P.get("revenue_lo"), P.get("revenue_hi"))
    method = INDEPENDENT
    if joint and joint.get("revenue"):
        rev, r_lo, r_hi = joint["revenue"]
        if joint.get("units"):
            units, u_lo, u_hi = joint["units"]
        method = JOINT
    src_rev, rating, reviews = _col(L, "revenue"), _col(L, "rating"), _col(L, "reviews")
    if reviews.notna().any() and rating.notna().any():
        m = rating.notna() & reviews.notna() & (reviews > 0)
        avg_rating = float(np.average(rating[m], weights=reviews[m])) if m.any() else float(rating.mean())
        rating_basis = "review-weighted"
    else:
        avg_rating = float(rating.mean()) if rating.notna().any() else None
        rating_basis = "mean of listings (no review counts)"
    dc, data_c = _col(P, "dental_confidence"), _col(P, "confidence_score")
    return {
        "products": int(len(P)), "listings": int(len(L)),
        "brands": int(L["brand"].dropna().nunique()) if "brand" in L else None,
        "sellers": int(L["seller"].dropna().nunique()) if "seller" in L and L["seller"].notna().any() else None,
        "units_est": units, "units_lo": u_lo, "units_hi": u_hi,
        "revenue_est": rev, "revenue_lo": r_lo, "revenue_hi": r_hi,
        "interval_method": method,
        "revenue_source_estimate": float(src_rev.sum()) if src_rev.notna().any() else None,
        "revenue_source_coverage": round(float(src_rev.notna().mean()), 3) if len(src_rev) else None,
        "offline_adjusted_revenue": None,
        "price_min": float(price.min()) if len(price) else None, "price_p25": float(price.quantile(.25)) if len(price) else None,
        "price_median": float(price.median()) if len(price) else None, "price_mean": float(price.mean()) if len(price) else None,
        "price_p75": float(price.quantile(.75)) if len(price) else None, "price_max": float(price.max()) if len(price) else None,
        "tier_entry_below": float(price.quantile(tiers[0])) if len(price) >= 3 else None,
        "tier_premium_from": float(price.quantile(tiers[1])) if len(price) >= 3 else None,
        "avg_rating": round(avg_rating, 3) if avg_rating is not None else None, "rating_basis": rating_basis,
        "reviews": float(reviews.sum()) if reviews.notna().any() else None,
        "dental_confidence": float(dc.median()) if dc.notna().any() else None,
        "data_confidence": float(data_c.median()) if data_c.notna().any() else None,
        **_concentration(P),
        **landed_cost.summarise(L, market),
    }


def build(products: pd.DataFrame, listings: pd.DataFrame, segments: pd.DataFrame, nodes: pd.DataFrame, market: str,
          interval_for: Callable[[str, str, list], dict | None] | None = None) -> pd.DataFrame:
    """One capacity row per scope: category, each segment, each taxonomy node. ``interval_for(scope, scope_id,
    product_ids)`` supplies the engine's joint interval for each scope (see ``intel``)."""
    def joint(scope: str, sid: str, P: pd.DataFrame) -> dict | None:
        return interval_for(scope, sid, P["product_id"].astype(str).tolist()) if interval_for else None

    rows = [{"scope": "category", "scope_id": market, "label": market,
             **intel(products, listings, market, joint("category", market, products))}]
    for sid, P in products.groupby("segment_id", sort=False):
        lab = segments.loc[segments["segment_id"] == sid, "segment_label"]
        rows.append({"scope": "segment", "scope_id": str(sid), "label": str(lab.iat[0]) if len(lab) else str(sid),
                     **intel(P, listings[listings["product_id"].isin(P["product_id"])], market, joint("segment", str(sid), P))})
    if len(nodes):
        for key, label, ids in zip(nodes["node_key"], nodes["label"], nodes["product_ids"]):
            P = products[products["product_id"].astype(str).isin(json.loads(ids))]
            rows.append({"scope": "taxonomy", "scope_id": key, "label": str(label),
                         **intel(P, listings[listings["product_id"].isin(P["product_id"])], market, joint("taxonomy", key, P))})
    return pd.DataFrame(rows)


def observations(cap: pd.DataFrame, dataset_id: str, observed_at: str) -> pd.DataFrame:
    """Provenance rows for taxonomy-node capacity (segments and the category already have theirs)."""
    from dip.knowledge.provenance import OBS_COLUMNS
    t = cap[cap["scope"] == "taxonomy"]
    rows = []
    for r in t.itertuples():
        for metric, v, lo, hi, unit, kind, method in [
            ("units_est", r.units_est, r.units_lo, r.units_hi, "units/month", "modeled", "sum of canonical-product estimates; " + r.interval_method),
            ("revenue_est", r.revenue_est, r.revenue_lo, r.revenue_hi, "USD/month", "modeled", "sum of canonical-product estimates; " + r.interval_method),
            ("revenue_source", r.revenue_source_estimate, None, None, "USD/month", "estimated", "sum of the source's own revenue estimates (listed values only)"),
            ("products", r.products, None, None, "count", "derived", "canonical products in the node"),
            ("price_median", r.price_median, r.price_p25, r.price_p75, "USD", "derived", "median of product median prices (lo/hi = p25/p75)"),
        ]:
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            rows.append({"entity_type": "taxonomy", "entity_id": r.scope_id, "metric": metric, "value": float(v),
                         "lo": None if lo is None else float(lo), "hi": None if hi is None else float(hi), "unit": unit,
                         "kind": kind, "source": "platform", "source_record_id": None, "dataset_id": dataset_id,
                         "observed_at": observed_at, "method": method, "confidence": None})
    return pd.DataFrame(rows, columns=OBS_COLUMNS)

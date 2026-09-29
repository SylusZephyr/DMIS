"""Markets, segments, products, product detail, galaxy layout, records."""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from dip.api.deprecation import gone, responses
from dip.api.util import clean
from dip.cache import response_cache
from dip.auth import Principal, assert_market, require, visible_markets
from dip.metrics.facts import market_revenue
from dip.storage import business as b
from dip.storage import lake
from dip.storage.graph import get_graph_store
from dip.storage.vectors import get_vector_store

router = APIRouter(tags=["catalog"])
PRODUCT_SORTS = {"opportunity": "opportunity_score DESC NULLS LAST", "revenue": "monthly_revenue DESC NULLS LAST",
                 "sales": "monthly_sales DESC NULLS LAST", "price": "price ASC NULLS LAST",
                 "listings": "listing_count DESC", "rating": "rating DESC NULLS LAST",
                 "revenue_est": "revenue_est DESC NULLS LAST", "units_est": "units_est DESC NULLS LAST"}
PRODUCT_LIST_COLS = ["product_id", "title", "brand", "product_type", "segment_id", "model_label", "image", "price",
                     "price_min", "price_max", "monthly_sales", "monthly_revenue", "annual_revenue", "rating", "reviews",
                     "listing_count", "best_listing", "price_tier", "opportunity_score", "sales_coverage"]
# added by the enterprise layer; listed only when the market was processed with it
OPTIONAL_LIST_COLS = ["confidence_score", "confidence_level", "variant_label", "model_basis",
                      # metrics engine v3
                      "units_est", "units_lo", "units_hi", "units_floor", "units_ceiling", "revenue_est", "revenue_lo",
                      "revenue_hi", "unit_margin", "margin_rate", "is_entrant", "opp_segment", "opp_price_band", "opp_quality_room"]


def _market(name: str) -> b.Market:
    with b.session() as s:
        m = s.get(b.Market, name)
        if m is None:
            raise HTTPException(404, f"market '{name}' not found")
        return m


class TopSegment(BaseModel):
    model_config = ConfigDict(extra="allow")
    segment_id: str
    segment_label: str | None = None
    opportunity_score: float | None = None      # the one opportunity score (explainable engine)
    opportunity_index: float | None = None      # metrics-v3 index, an input to it
    opportunity_level: str | None = None
    revenue_est: float | None = None
    ranked_by: str | None = None


class MarketRecommendation(BaseModel):
    model_config = ConfigDict(extra="allow")
    segment_id: str
    segment_label: str | None = None
    features: list[str] = []
    price_range: list[float] | str | None = None
    expected_units: float | None = None
    expected_units_lo: float | None = None
    expected_units_hi: float | None = None
    basis: str | None = None


class MarketRow(BaseModel):
    """One market in the market list. Declared fields are typed for the frontend; unknown fields pass through."""
    model_config = ConfigDict(extra="allow", protected_namespaces=())      # allows the field name model_validated
    name: str
    display_name: str | None = None                  # config/categories.yaml leaf_category_en
    display_name_zh: str | None = None               # leaf_category_zh
    industry_branch: str | None = None
    updated_at: datetime | None = None
    products: int | None = None
    listings: int | None = None
    segments: int | None = None
    monthly_revenue: float | None = None
    annual_revenue: float | None = None
    sales_coverage: float | None = None
    concentration: str | None = None
    top_opportunity: float | None = None             # older opportunity index (metrics v2)
    top_opportunity_score: float | None = None       # explainable opportunity engine (knowledge layer)
    top_opportunity_label: str | None = None
    horizon: str | None = None
    marketplace: str | None = None
    trend: str | None = None
    confidence: float | None = None
    revenue_headline: float | None = None           # what the data certainly shows (observed floor): the market size shown
    revenue_basis: str | None = None
    model_validated: bool | None = None             # whether revenue_est/lo/hi passed hold-out validation
    revenue_est: float | None = None
    revenue_lo: float | None = None
    revenue_hi: float | None = None
    evidence_grade: str | None = None
    hhi: float | None = None
    top_segment: TopSegment | None = None
    recommendation: MarketRecommendation | None = None


def _category_def(name: str, category) -> dict:
    """The market's category definition (config/categories.yaml), by its category key or its own name."""
    from dip.pipeline.scope import definitions

    defs = definitions()
    return (defs.get(category) if isinstance(category, str) else None) or defs.get(name) or {}


@router.get("/markets", response_model=list[MarketRow])
def markets(principal: Principal = Depends(require("markets", "read"))):
    with b.session() as s:
        rows = s.query(b.Market).order_by(b.Market.updated_at.desc()).all()
        out = []
        for m in rows:
            su = m.summary or {}
            c = su.get("category", {})
            d = _category_def(m.name, c)
            out.append({"name": m.name, "display_name": d.get("leaf_category_en"), "display_name_zh": d.get("leaf_category_zh"),
                        "industry_branch": m.industry_branch, "updated_at": m.updated_at,
                        "products": c.get("products"), "listings": c.get("listings"), "segments": c.get("segments"),
                        "monthly_revenue": c.get("monthly_revenue"), "annual_revenue": c.get("annual_revenue"),
                        "sales_coverage": c.get("sales_coverage"), "concentration": c.get("concentration"),
                        "top_opportunity": su.get("opportunity", {}).get("top_score"),
                        "top_opportunity_score": (su.get("opportunity_engine") or {}).get("top_score"),
                        "top_opportunity_label": (su.get("opportunity_engine") or {}).get("top_label"),
                        "horizon": (su.get("horizons") or {}).get("12_month"), "marketplace": su.get("marketplace"),
                        "trend": (su.get("trend") or {}).get("trend"),
                        "confidence": (su.get("confidence") or {}).get("confidence_score"),
                        # metrics engine v3 (one reader: dip.metrics.facts)
                        "revenue_headline": market_revenue(su)["headline"], "revenue_basis": market_revenue(su)["basis"],
                        "model_validated": market_revenue(su)["model_validated"],
                        "revenue_est": ((su.get("metrics_v3") or {}).get("revenue_month") or {}).get("estimate"),
                        "revenue_lo": ((su.get("metrics_v3") or {}).get("revenue_month") or {}).get("low"),
                        "revenue_hi": ((su.get("metrics_v3") or {}).get("revenue_month") or {}).get("high"),
                        "evidence_grade": (su.get("metrics_v3") or {}).get("evidence_grade"),
                        "hhi": (su.get("metrics_v3") or {}).get("hhi"),
                        "top_segment": (su.get("metrics_v3") or {}).get("top_segment"),
                        "recommendation": su.get("recommendation")})
        return clean(visible_markets(principal, out))


@router.get("/markets/{market}", dependencies=[Depends(require("markets", "read"))])
def market(market: str):
    return clean(_market(market).summary)


@router.get("/markets/{market}/segments", dependencies=[Depends(require("markets", "read"))])
def segments(market: str):
    _market(market)
    df = lake.decode_json(lake.read_curated("segments", market, order="opportunity_score DESC NULLS LAST"), ("top_terms",))
    return clean(df)


@router.get("/markets/{market}/products", dependencies=[Depends(require("markets", "read"))])
def products(market: str, sort: str = "opportunity", segment: str | None = None, q: str | None = None,
             limit: int = Query(100, le=5000), offset: int = 0):
    _market(market)
    where, params = [], []
    if segment:
        where.append("segment_id = ?"); params.append(segment)
    if q:
        where.append("(lower(title) LIKE ? OR lower(brand) LIKE ?)"); params += [f"%{q.lower()}%"] * 2
    total = lake.query(f"SELECT count(*) AS n FROM read_parquet('{lake.curated_path('products', market)}')"
                       + (" WHERE " + " AND ".join(where) if where else ""), params)["n"].iloc[0]
    have = lake.curated_columns("products", market)
    cols = PRODUCT_LIST_COLS + [c for c in OPTIONAL_LIST_COLS if c in have]
    df = lake.read_curated("products", market, columns=cols, where=" AND ".join(where) or None,
                           order=PRODUCT_SORTS.get(sort, PRODUCT_SORTS["opportunity"]), limit=limit, offset=offset, params=params)
    return {"total": int(total), "items": clean(df)}


def _find_product(product_id: str) -> tuple[str, pd.Series]:
    df = lake.read_curated("products", None, where="product_id = ?", params=[product_id])
    if df.empty:
        raise HTTPException(404, f"product '{product_id}' not found")
    row = lake.decode_json(df, ("attributes", "listing_ids")).iloc[0]
    return row["market"], row


@router.get("/products/{product_id}")
def product_detail(product_id: str, principal: Principal = Depends(require("markets", "read"))):
    market, p = _find_product(product_id)
    assert_market(principal, market)
    listings = lake.read_curated("listings", market, where="product_id = ?", params=[product_id],
                                 columns=None, order="sales DESC NULLS LAST")
    keep = [c for c in ["id", "title", "brand", "price", "sales", "revenue", "rating", "reviews", "image", "url", "seller",
                        "is_best_listing", "data_confidence", "relevance_score", "launch_date"] if c in listings]
    seg = lake.read_curated("segments", market, where="segment_id = ?", params=[p["segment_id"]])
    seg_row = clean(seg.iloc[0].to_dict()) if len(seg) else {}
    # competitors: same segment, closest price, plus graph COMPETES_WITH
    have = lake.curated_columns("products", market)
    peers = lake.read_curated("products", market, columns=PRODUCT_LIST_COLS + [c for c in OPTIONAL_LIST_COLS if c in have], where="segment_id = ? AND product_id <> ?",
                              params=[p["segment_id"], product_id])
    if len(peers) and pd.notna(p.get("price")):
        peers = peers.assign(_d=(peers["price"] - p["price"]).abs()).sort_values(["_d"]).drop(columns="_d")
    matches = lake.read_curated("supplier_matches", market, where="segment_id = ?", params=[p["segment_id"]],
                                order="match_score DESC")
    suppliers = []
    if len(matches):
        with b.session() as s:
            for _, m in matches.head(10).iterrows():
                sup = s.get(b.Supplier, m["supplier_id"])
                if sup:
                    suppliers.append({**b.row_dict(sup), "match_score": float(m["match_score"])})
    pain = lake.read_curated("pain", market, where="scope IN (?, ?)", params=[f"product:{product_id}", f"segment:{p['segment_id']}"])
    complaints = {r["scope"].split(":")[0]: json.loads(r["payload"]) for _, r in pain.iterrows()} if len(pain) else {}
    try:
        similar = get_vector_store().similar(product_id, limit=8)
    except Exception:
        similar = []
    factors = {k: seg_row.get(f"f_{k}") for k in ("demand", "growth", "customer_pain", "competition_gap", "supplier", "difficulty")}
    return clean({
        "market": market, "product": p.to_dict(), "listings": listings[keep] if len(listings) else [],
        "segment": seg_row, "competitors": peers.head(8), "suppliers": suppliers, "customer_pain": complaints,
        "opportunity": {"score": p.get("opportunity_score"), "segment_score": seg_row.get("opportunity_score"),
                        "level": seg_row.get("opportunity_level"), "coverage": seg_row.get("coverage"), "factors": factors,
                        "drivers": seg_row.get("opportunity_drivers")},
        "similar": similar,
    })


def _in_scope(principal: Principal, hits: list[dict]) -> list[dict]:
    scope = principal.market_scope()
    return hits if scope is None else [h for h in hits if h.get("market") in scope]


@router.get("/products/{product_id}/similar")
def similar(product_id: str, limit: int = 10, same_market: bool = False,
            principal: Principal = Depends(require("markets", "read"))):
    own = _find_product(product_id)[0]
    assert_market(principal, own)
    market = own if same_market else None
    return clean(_in_scope(principal, get_vector_store().similar(product_id, limit=limit, market=market)))


@router.get("/search/products")
def search_products(q: str, limit: int = 20, market: str | None = None,
                    principal: Principal = Depends(require("markets", "read"))):
    """Semantic product search over the vector index (all markets unless given)."""
    return clean(_in_scope(principal, get_vector_store().similar_to_text(q, limit=limit, market=market)))


@router.get("/markets/{market}/galaxy", deprecated=True, responses=responses("GET /markets/{market}/galaxy"))
def galaxy(market: str):
    """Retired (410): use GET /markets/{market}/galaxy-v3."""
    gone("GET /markets/{market}/galaxy")


@router.get("/markets/{market}/galaxy-v3", dependencies=[Depends(require("markets", "read"))])
@response_cache
def galaxy_v3(market: str, limit: int = Query(3000, le=50000)):
    """Product Galaxy on metrics v3: every product with the traits it can be coloured by (segment,
    sub-category, brand, within-segment price quartile, opportunity index, margin, entrant) and sized by
    (estimated revenue / units), plus typed links: SIMILAR (graph similarity, with score), BRAND (each
    product to its brand's largest product in the market) and SEGMENT (to the segment hub, drawn client-side)."""
    _market(market)
    have = lake.curated_columns("products", market)
    want = ["product_id", "title", "brand", "segment_id", "image", "price", "gx", "gy", "gz", "listing_count", "model_label",
            "units_est", "units_lo", "units_hi", "revenue_est", "revenue_lo", "revenue_hi", "opportunity_score", "margin_rate",
            "unit_margin", "is_entrant", "rating", "monthly_sales", "monthly_revenue"]
    df = lake.read_curated("products", market, columns=[c for c in want if c in have],
                           order=("revenue_est DESC NULLS LAST" if "revenue_est" in have else "monthly_revenue DESC NULLS LAST"), limit=limit)
    seg_have = lake.curated_columns("segments", market)
    segs = lake.read_curated("segments", market, columns=[c for c in ["segment_id", "segment_label", "family_label", "revenue_est",
                                                                        "opportunity_score", "opportunity_index", "opportunity_level",
                                                                        "listings"] if c in seg_have])
    df = df.merge(segs[["segment_id", "segment_label"] + (["family_label"] if "family_label" in segs else [])], on="segment_id", how="left")
    price = pd.to_numeric(df["price"], errors="coerce")
    # within-segment price quartile (1 = cheapest); segments with < 4 priced products get none
    q = price.groupby(df["segment_id"]).rank(pct=True, method="average")
    n = price.groupby(df["segment_id"]).transform("count")
    df["price_quartile"] = np.where((n >= 4) & q.notna(), np.ceil(q * 4).clip(1, 4), np.nan)
    ids = set(df["product_id"])
    edges = []
    g = get_graph_store()
    for pid in df["product_id"].head(1500):
        nb = g.neighbourhood(f"product:{pid}", 1, kinds=["Product"], limit=20)
        for e in nb["edges"]:
            if e["rel"] == "PRODUCT_SIMILAR_TO":
                a, c = e["source"].split(":", 1)[1], e["target"].split(":", 1)[1]
                if a in ids and c in ids and a < c:
                    edges.append({"source": a, "target": c, "type": "similar", "score": e["props"].get("score")})
    size_col = "revenue_est" if "revenue_est" in df else "monthly_revenue"
    for brand, grp in df[df["brand"].notna()].groupby("brand"):
        if len(grp) < 2:
            continue
        hub = grp.sort_values(size_col, ascending=False)["product_id"].iat[0]
        edges += [{"source": hub, "target": p, "type": "brand", "score": None} for p in grp["product_id"] if p != hub]
    edges = list({(e["source"], e["target"], e["type"]): e for e in edges}.values())
    counts = pd.Series([e["type"] for e in edges]).value_counts().to_dict() if edges else {}
    # level of detail: the largest products by revenue are drawn; say how much of the market that is
    tot = lake.query(f"SELECT COUNT(*) AS n, SUM({size_col}) AS r FROM read_parquet('{lake.curated_path('products', market)}')").iloc[0]
    shown_rev = float(pd.to_numeric(df[size_col], errors="coerce").sum())
    lod = {"shown": int(len(df)), "total": int(tot["n"]), "limit": limit,
           "revenue_share_shown": (shown_rev / float(tot["r"])) if tot["r"] else None}
    return clean({"products": df, "segments": segs, "edges": edges, "edge_counts": counts, "lod": lod,
                  "basis": {"size": size_col, "layout": "title-embedding layout from processing (gx, gy, gz)"}})


@router.get("/markets/{market}/records", dependencies=[Depends(require("markets", "read"))])
def records(market: str, status: str = "excluded", limit: int = Query(500, le=10000), offset: int = 0):
    _market(market)
    cols = ["id", "title", "brand", "price", "sales", "category", "data_confidence", "relevance_score", "relevance_status",
            "relevance_explanation", "excluded_reason", "product_id"]
    where = {"excluded": "excluded_reason IS NOT NULL", "uncertain": "relevance_status = 'uncertain'",
             "included": "excluded_reason IS NULL"}.get(status)
    df = lake.read_curated("records", market, columns=cols, where=where, order="relevance_score ASC", limit=limit, offset=offset)
    return clean(lake.decode_json(df, ("relevance_explanation",)))


@router.get("/markets/{market}/forecast", dependencies=[Depends(require("markets", "read"))])
def forecast(market: str):
    _market(market)
    return clean(lake.read_curated("forecasts", market))


@router.get("/markets/{market}/pain", dependencies=[Depends(require("markets", "read"))])
def pain(market: str, scope: str = "__market__"):
    _market(market)
    df = lake.read_curated("pain", market, where="scope = ?", params=[scope])
    return clean(json.loads(df["payload"].iloc[0])) if len(df) else {"status": "no_review_data"}

"""Market Universe, knowledge-graph explorer, geography, shopping mode,
simulation, relevance feedback and the optional analyst."""

from __future__ import annotations

import re

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from dip.api.deprecation import gone, responses
from dip.api.util import clean
from dip.cache import response_cache
from dip.auth import Principal, require
from dip.geo import resolve
from dip.metrics.facts import market_revenue
from dip.pipeline.analytics import brand_leaderboard, universe_totals
from dip.pipeline.universe import branches
from dip.settings import get_settings
from dip.storage import business as b
from dip.storage import lake
from dip.storage.graph import get_graph_store

router = APIRouter(tags=["intelligence"])


def _first(*vals):
    return next((v for v in vals if v is not None), None)


@router.get("/universe")
@response_cache
def universe(principal: Principal = Depends(require("markets", "read"))):
    """Dental Industry -> branches -> categories (markets) -> segments.
    size = estimated monthly revenue (metrics v3), color = opportunity index, glow = entrant revenue share."""
    cfg = branches()
    with b.session() as s:
        mk = {m.name: m for m in s.query(b.Market).all() if principal.may_see_market(m.name)}
    tree = {"id": "industry:dental", "label": cfg.get("industry", "Dental Industry"), "kind": "Industry", "children": []}
    for br in cfg["branches"]:
        node = {"id": f"industry:dental/{br}", "label": br, "kind": "Branch", "children": []}
        for name, m in mk.items():
            if m.industry_branch != br:
                continue
            su = m.summary or {}
            c = su.get("category", {})
            v3 = su.get("metrics_v3") or {}
            rm = v3.get("revenue_month") or {}
            have = lake.curated_columns("segments", name) if lake.has_curated("segments", name) else set()
            is_v3 = {"revenue_est", "opportunity_index"} <= have
            cols = (["segment_id", "segment_label", "revenue_est", "revenue_lo", "revenue_hi", "opportunity_score", "opportunity_index", "entrant_revenue_share",
                     "listings"] if is_v3 else ["segment_id", "segment_label", "monthly_revenue", "opportunity_score", "products", "f_growth"])
            segs = lake.read_curated("segments", name, columns=[x for x in cols if x in have]) if have else pd.DataFrame()
            f = lambda r, k: None if k not in r or pd.isna(r[k]) else float(r[k])  # noqa: E731
            node["children"].append({
                "id": f"category:{name}", "label": name, "kind": "Category", "market": name,
                # metrics v3: estimated revenue with its interval; the one opportunity score (explainable engine, index
                # as fallback for segments it cannot score); v2 values only for markets processed before v3
                "value": rm.get("estimate", c.get("monthly_revenue")), "value_lo": rm.get("low"), "value_hi": rm.get("high"),
                "evidence_grade": v3.get("evidence_grade"),
                "opportunity": _first((v3.get("top_segment") or {}).get("opportunity_score"),
                                      (v3.get("top_segment") or {}).get("opportunity_index"), (su.get("opportunity") or {}).get("top_score")),
                "growth": None if is_v3 else (su.get("forecast") or {}).get("growth_rate"),
                "growth_label": None if is_v3 else (su.get("horizons") or {}).get("12_month"),
                "momentum": (su.get("launch_cohort") or {}).get("new_listing_revenue_share"),
                "products": c.get("products"),
                "children": [{"id": f"segment:{name}/{r['segment_id']}", "label": r["segment_label"], "kind": "Segment", "market": name,
                              "segment_id": r["segment_id"],
                              "value": f(r, "revenue_est") if is_v3 else f(r, "monthly_revenue"),
                              "value_lo": f(r, "revenue_lo"), "value_hi": f(r, "revenue_hi"),
                              "opportunity": _first(f(r, "opportunity_score"), f(r, "opportunity_index")),
                              "growth": None if is_v3 else f(r, "f_growth"), "momentum": f(r, "entrant_revenue_share"),
                              "products": f(r, "listings") if is_v3 else f(r, "products")} for r in segs.to_dict("records")] if len(segs) else [],
            })
        node["value"] = sum((ch["value"] or 0) for ch in node["children"]) or None
        node["opportunity"] = max((ch["opportunity"] or 0 for ch in node["children"]), default=None) or None
        tree["children"].append(node)
    tree["value"] = sum((ch["value"] or 0) for ch in tree["children"]) or None
    return clean(tree)


@router.get("/overview")
@response_cache
def overview(principal: Principal = Depends(require("markets", "read"))):
    """Command-center KPIs across every market (a product manager's own markets only)."""
    tot = universe_totals()
    scope = principal.market_scope()
    if scope is not None and len(tot):
        tot = tot[tot["market"].isin(scope)]
    with b.session() as s:
        n_sup = s.query(b.Supplier).count()
        n_emp = s.query(b.Employee).count()
        n_ds = s.query(b.Dataset).count()
        records = sum(d.raw_rows for d in s.query(b.Dataset).all())
    return clean({
        "markets": int(len(tot)), "products": int(tot["products"].sum()) if len(tot) else 0,
        "listings": int(tot["listings"].sum()) if len(tot) else 0,
        "monthly_revenue": float(tot["monthly_revenue"].sum()) if len(tot) else None,
        "datasets": n_ds, "records_ingested": records, "suppliers": n_sup, "employees": n_emp,
        "graph": get_graph_store().stats(), "by_market": tot,
        "top_brands": brand_leaderboard(limit=10) if scope is None else _scoped_brands(scope),
        **_overview_v3(scope),
    })


def _overview_v3(scope: set[str] | None) -> dict:
    """Estimated revenue (sum of each market's v3 estimate; the interval of a sum is not the sum of intervals,
    so only the certain floor is summed alongside) and leading brands by estimated revenue."""
    with b.session() as s:
        ms = [m for m in s.query(b.Market).all() if scope is None or m.name in scope]
        v3 = {m.name: (m.summary or {}).get("metrics_v3") or {} for m in ms}
    est = [x.get("revenue_month", {}).get("estimate") for x in v3.values() if x.get("revenue_month")]
    floor = [x.get("revenue_month", {}).get("floor") for x in v3.values() if x.get("revenue_month")]
    brands = []
    for m in v3:
        br = lake.read_curated("brands", m, columns=["brand", "revenue_est", "revenue_lo", "revenue_hi", "share_est", "position"],
                               order="revenue_est DESC", limit=10)
        brands += [{**r, "market": m} for r in br.to_dict("records")]
    brands.sort(key=lambda r: -(r.get("revenue_est") or 0))
    validated = [x.get("revenue_month", {}).get("model_validated") for x in v3.values() if x.get("revenue_month")]
    return {"revenue_est": float(sum(e for e in est if e is not None)) if est else None,
            "revenue_floor": float(sum(f for f in floor if f is not None)) if floor else None,
            # the headline is what the data certainly shows (as on each market page); the estimate sits beside it
            "revenue_headline": float(sum(f for f in floor if f is not None)) if floor else None,
            "models_validated": sum(1 for v in validated if v is True), "models_not_validated": sum(1 for v in validated if v is False),
            "markets_with_v3": len(est), "top_brands_v3": brands[:10]}


def _graph_scope(principal: Principal, nodes: list[dict], edges: list[dict] | None = None):
    """Scoped users (product managers, other organizations) see only nodes of their markets
    (plus shared industry nodes)."""
    scope = principal.market_scope()
    if scope is None:
        return nodes, edges
    # industry nodes are shared by every market (their stored market is whichever wrote them last)
    keep = [n for n in nodes if n.get("kind") == "Industry" or n.get("market") in scope
            or n.get("market") in (None, "__global__")]
    ids = {n["id"] for n in keep}
    return keep, (None if edges is None else [e for e in edges if e["source"] in ids and e["target"] in ids])


@router.get("/graph/explore")
def graph_explore(node: str = "industry:dental", depth: int = Query(2, ge=1, le=4), kinds: str | None = None,
                  limit: int = Query(600, le=5000), principal: Principal = Depends(require("markets", "read"))):
    kk = [k for k in (kinds or "").split(",") if k] or None
    g = get_graph_store().neighbourhood(node, depth, kk, limit)
    g["nodes"], g["edges"] = _graph_scope(principal, g["nodes"], g["edges"])
    return clean(g)


@router.get("/graph/search")
def graph_search(q: str, kind: str | None = None, limit: int = 20, principal: Principal = Depends(require("markets", "read"))):
    ql = q.lower().strip()
    out = [n for n in get_graph_store().nodes(kind=kind, limit=200000) if ql in str(n["label"]).lower()]
    # exact > prefix > whole word > substring; then the higher-level kinds; then shorter labels
    order = {k: i for i, k in enumerate(["Category", "ProductFamily", "Segment", "Feature", "Recommendation", "Brand", "Supplier",
                                         "Momentum", "Trend", "CustomerProblem", "Product", "Country", "Seller", "Listing"])}

    def rank(n):
        lab = str(n["label"]).lower()
        m = 0 if lab == ql else 1 if lab.startswith(ql) else 2 if re.search(rf"\b{re.escape(ql)}\b", lab) else 3
        return (m, order.get(n.get("kind"), 99), len(lab))
    out.sort(key=rank)
    return clean(_graph_scope(principal, out)[0][:limit])


def _scoped_brands(scope: set[str], limit: int = 10) -> pd.DataFrame:
    frames = [brand_leaderboard(m, limit=200) for m in scope]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).groupby("brand", as_index=False).agg(
        products=("products", "sum"), monthly_revenue=("monthly_revenue", "sum"), monthly_sales=("monthly_sales", "sum"),
        avg_rating=("avg_rating", "mean"), markets=("markets", "sum"))
    return df.sort_values("monthly_revenue", ascending=False, na_position="last").head(limit)


@router.get("/graph/path")
def graph_path(source: str, target: str, max_len: int = Query(6, ge=1, le=10),
               principal: Principal = Depends(require("markets", "read"))):
    """How two nodes are connected: the shortest path (ties to stronger evidence), each hop with its evidence."""
    g = get_graph_store().find_path(source, target, max_len)
    nodes, edges = _graph_scope(principal, g.get("nodes", []), g.get("edges", []))
    if len(nodes) != len(g.get("nodes", [])):                 # a hop runs through a market outside the user's scope
        return {"nodes": [], "edges": [], "note": "no connection within your markets"}
    return clean({"nodes": nodes, "edges": edges, "note": g.get("note")})


@router.get("/graph/why")
def graph_why(source: str, target: str, principal: Principal = Depends(require("markets", "read"))):
    """Every direct edge between two nodes (either direction) with its evidence."""
    nb = get_graph_store().neighbourhood(source, 1, None, 5000)
    nodes, edges = _graph_scope(principal, nb["nodes"], nb["edges"])
    direct = [e for e in edges if {e["source"], e["target"]} == {source, target}]
    return clean({"edges": direct, "nodes": [n for n in nodes if n["id"] in (source, target)]})


@router.get("/graph/stats", dependencies=[Depends(require("markets", "read"))])
def graph_stats():
    return get_graph_store().stats()


@router.get("/geo")
@response_cache
def geo(principal: Principal = Depends(require("markets", "read"))):
    """Countries from real fields only: supplier countries and dataset marketplaces."""
    countries: dict[str, dict] = {}

    def slot(name):
        c = resolve(name)
        if c is None:
            return None
        return countries.setdefault(c["iso2"], {**c, "suppliers": 0, "supplier_score": [], "markets": [],
                                                "market_revenue": 0.0, "opportunity": None})
    unresolved = set()
    with b.session() as s:
        scope = principal.market_scope()
        for sup in s.query(b.Supplier).all():
            if (sup.org_id or b.DEFAULT_ORG) != principal.org:
                continue
            c = slot(sup.country)
            if c is None:
                if sup.country:
                    unresolved.add(sup.country)
                continue
            c["suppliers"] += 1
            if sup.score is not None:
                c["supplier_score"].append(sup.score)
        for m in s.query(b.Market).all():
            if scope is not None and m.name not in scope:
                continue
            su = m.summary or {}
            c = slot(su.get("marketplace"))
            if c is None:
                continue
            c["markets"].append(m.name)
            c["market_revenue"] += market_revenue(su)["headline"] or 0          # observed floor, as on the market page
            top = (su.get("opportunity") or {}).get("top_score")
            if top is not None:
                c["opportunity"] = max(c["opportunity"] or 0, top)
    for c in countries.values():
        sc = c.pop("supplier_score")
        c["avg_supplier_score"] = round(sum(sc) / len(sc), 1) if sc else None
    return clean({"countries": list(countries.values()), "unresolved_countries": sorted(unresolved)})


@router.post("/shopping/recommend", deprecated=True, responses=responses("POST /shopping/recommend"))
def shopping():
    """Retired (410): use POST /shopping/recommend-v3."""
    gone("POST /shopping/recommend")


@router.post("/simulate", deprecated=True, responses=responses("POST /simulate"))
def simulate():
    """Retired (410): use POST /launch/simulate."""
    gone("POST /simulate")


class Feedback(BaseModel):
    record_id: str
    text: str
    is_dental: bool
    market: str | None = None
    note: str | None = None


@router.post("/relevance/feedback", dependencies=[Depends(require("datasets", "write"))])
def relevance_feedback(fb: Feedback):
    """Human correction: overrides that listing on future runs and trains the local model."""
    from dmie.engine import store as core_store

    con = core_store.connect(get_settings().analytics_path)
    try:
        core_store.add_feedback(con, "dental", fb.record_id, fb.text.lower(), fb.is_dental, fb.market, "platform-ui", fb.note)
        n = len(core_store.read_feedback(con, "dental"))
    finally:
        con.close()
    return {"saved": True, "labels_total": n, "note": "applied on the next processing run of this market"}


@router.post("/ask", deprecated=True, responses=responses("POST /ask"))
def ask():
    """Retired (410): use POST /analyst/ask-v3."""
    gone("POST /ask")

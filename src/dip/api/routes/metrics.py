"""Metrics engine v3 API: every metric with value, interval, n, unit, formula id and caveat, plus
"explain" views that show how a segment's or product's numbers were derived."""

from __future__ import annotations

import json
import re

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dip.api.routes.catalog import _find_product, _market
from dip.api.util import clean
from dip.auth import Principal, assert_market, require
from dip.metrics import config
from dip.settings import PROJECT_ROOT
from dip.storage import lake

router = APIRouter(tags=["metrics v3"])

SEGMENT_COLS = ["segment_id", "segment_label", "family_label", "listings_v3", "revenue_est", "revenue_lo", "revenue_hi",
                "revenue_floor", "units_est", "hhi_est", "top_brand_est", "entrants", "entrant_success_rate",
                "entrant_revenue_share", "margin_rate_median", "quality_gap_share", "rating_bar", "unbadged_share",
                "listings_per_1k_units", "entrant_units_expected", "entrant_value_kind", "entrant_value_expected",
                "entrant_value_lo", "entrant_value_hi", "entrant_target_prob", "opportunity_index", "opportunity_level",
                "opportunity_coverage", "opp_demand", "opp_entry", "opp_margin", "opp_competition", "opp_quality_gap",
                "opp_saturation", "opportunity_score", "opportunity_status", "top_terms", "trend_label", "subcategory", "segment_description",
                "segment_description_zh", "dominant_specs", "price_median", "avg_rating"]


def _rows(market: str, where: str | None = None, params: list | None = None):
    return lake.read_curated("metrics", market, where=where, params=params)


@router.get("/markets/{market}/metrics")
def market_metrics(market: str, scope: str | None = None, scope_id: str | None = None, metric: str | None = None,
                   principal: Principal = Depends(require("markets", "read"))):
    m = _market(market)
    assert_market(principal, market)
    conds, params = [], []
    for col, val in (("scope", scope), ("scope_id", scope_id), ("metric", metric)):
        if val is not None:
            conds.append(f"{col} = ?")
            params.append(val)
    rows = _rows(market, " AND ".join(conds) or None, params)
    return clean({"market": market, "summary": (m.summary or {}).get("metrics_v3"), "rows": rows})


@router.get("/markets/{market}/segments-v3")
def segments_v3(market: str, principal: Principal = Depends(require("markets", "read"))):
    _market(market)
    assert_market(principal, market)
    segs = lake.read_curated("segments", market, order="opportunity_score DESC NULLS LAST")
    return clean(segs[[c for c in SEGMENT_COLS if c in segs]])


@router.get("/markets/{market}/segments/{segment_id}/explain")
def segment_explain(market: str, segment_id: str, principal: Principal = Depends(require("markets", "read"))):
    m = _market(market)
    assert_market(principal, market)
    seg = lake.read_curated("segments", market, where="segment_id = ?", params=[segment_id])
    if seg.empty:
        raise HTTPException(404, "segment not found")
    rows = _rows(market, "scope = 'segment' AND scope_id = ?", [segment_id])
    listings = lake.read_curated("listings", market, where="segment_id = ?", params=[segment_id],
                                 order="revenue_est DESC NULLS LAST", limit=25)
    keep = [c for c in ("id", "title", "brand", "price", "rating", "sales", "sales_observation", "units_floor", "units_est",
                        "units_lo", "units_hi", "units_ceiling", "revenue_est", "unit_margin", "margin_rate", "is_entrant",
                        "age_days", "price_band") if c in listings]
    from dip.metrics.bands import price_bands

    band_src = lake.read_curated("listings", market, columns=[c for c in ("price", "price_band", "units_est", "revenue_est")
                                                               if c in lake.curated_columns("listings", market)],
                                 where="segment_id = ?", params=[segment_id])
    brands = lake.read_curated("segment_brands", market, where="segment_id = ?", params=[segment_id],
                               order="share_est DESC") if lake.has_curated("segment_brands", market) else pd.DataFrame()
    mom = lake.read_curated("momentum", market, where="scope_id = ?", params=[segment_id]) \
        if lake.has_curated("momentum", market) else pd.DataFrame()
    comps = config()["opportunity"]["components"]
    return clean({"market": market, "segment": seg.iloc[0].to_dict(), "metrics": rows,
                  "brands": brands.drop(columns=["market"], errors="ignore").to_dict("records"),
                  "momentum": mom.drop(columns=["market"], errors="ignore").iloc[0].to_dict() if len(mom) else None,
                  "price_bands": price_bands(band_src),
                  "opportunity_components": {k: {**v, "value": seg.iloc[0].get(f"opp_{k}")} for k, v in comps.items()},
                  "market_summary": (m.summary or {}).get("metrics_v3"), "top_listings": listings[keep]})


@router.get("/products/{product_id}/explain")
def product_explain(product_id: str, principal: Principal = Depends(require("markets", "read"))):
    market, p = _find_product(product_id)
    assert_market(principal, market)
    listings = lake.read_curated("listings", market, where="product_id = ?", params=[product_id])
    keep = [c for c in ("id", "title", "price", "rating", "sales", "sales_observation", "units_floor", "units_est", "units_lo",
                        "units_hi", "units_ceiling", "revenue_est", "unit_cost", "unit_cost_issue", "fulfilment_fee",
                        "unit_margin", "margin_rate", "is_entrant", "age_days", "price_band", "price_band_lift") if c in listings]
    seg_rows = _rows(market, "scope = 'segment' AND scope_id = ?", [p["segment_id"]])
    pw = config()["opportunity"]["product"]
    from dip.metrics.bands import POSITION_METRICS, position

    pcols = lake.curated_columns("products", market)
    peers = lake.read_curated("products", market, columns=[c for c in ("product_id", "title", *POSITION_METRICS) if c in pcols],
                              where="segment_id = ?", params=[p["segment_id"]])
    pos = position(p.to_dict(), peers)
    cap = config()["segment_detail"]["max_peers_plotted"]
    pos["points"] = peers.sort_values("revenue_est", ascending=False).head(cap).to_dict("records") if "revenue_est" in peers \
        else peers.head(cap).to_dict("records")
    return clean({"market": market, "product": p.to_dict(), "listings": listings[keep], "segment_metrics": seg_rows,
                  "position": pos,
                  "components": {"segment": {"weight": pw["segment_weight"], "value": p.get("opp_segment")},
                                 "price_band": {"weight": pw["price_band_weight"], "value": p.get("opp_price_band")},
                                 "quality_room": {"weight": pw["quality_room_weight"], "value": p.get("opp_quality_room")}}})


@router.get("/methodology")
def methodology():
    """docs/METHODOLOGY.md, split into sections addressable by formula id (e.g. M2.1)."""
    text = (PROJECT_ROOT / "docs" / "METHODOLOGY.md").read_text(encoding="utf-8")
    sections, current, buf = [], None, []
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            if current:
                sections.append({**current, "body": "\n".join(buf).strip()})
            ids = re.findall(r"\bM\d+\.\d+\b", line)
            current, buf = {"title": line.lstrip("# ").strip(), "level": line.count("#", 0, 4), "ids": ids}, []
        else:
            buf.append(line)
    if current:
        sections.append({**current, "body": "\n".join(buf).strip()})
    return {"markdown": text, "sections": sections}


# ---------------------------------------------------------------- category scope (5.3)
class ScopeIn(BaseModel):
    category: str
    decision: str                      # in | out | auto
    note: str | None = None


@router.get("/markets/{market}/scope")
def market_scope(market: str, principal: Principal = Depends(require("markets", "read"))):
    """Every marketplace sub-category in the market: listings, evidence, classifier and final decision."""
    _market(market)
    assert_market(principal, market)
    from dip.pipeline import scope

    rec = lake.read_curated("records", market)
    rec = rec[[c for c in ("category", "title", "revenue") if c in rec]]
    table = scope.classify(rec, market) if len(rec) else pd.DataFrame()
    counts = table.groupby("decision")["listings"].sum().to_dict() if len(table) else {}
    defin = scope.definitions().get(market) or {}
    return clean({"market": market, "has_definition": bool(defin), "counts": counts,
                  "boundary": defin.get("boundary", "review"), "impact": scope.impact(rec, table) if len(table) else {},
                  "sub_categories": table, "note": "decisions take effect on the next processing run of this market"})


@router.post("/markets/{market}/scope")
def set_scope(market: str, body: ScopeIn, principal: Principal = Depends(require("datasets", "write"))):
    _market(market)
    assert_market(principal, market)
    if body.decision not in ("in", "out", "auto"):
        raise HTTPException(409, "decision must be in, out or auto")
    from dip import audit
    from dip.storage import business as b

    with b.session() as s:
        s.query(b.ScopeDecision).filter_by(market_name=market, category=body.category).delete()
        if body.decision != "auto":
            s.add(b.ScopeDecision(market_name=market, category=body.category, decision=body.decision, note=body.note,
                                  decided_by=principal.email or "local user"))
    audit.record("scope.decide", principal, "markets", market, body.model_dump())
    return {"saved": True, "note": "re-process the market to apply"}


# ---------------------------------------------------------------- gaps + recommendations (5.4)
@router.get("/markets/{market}/gaps")
def market_gaps(market: str, segment_id: str | None = None, only_gaps: bool = False,
                principal: Principal = Depends(require("markets", "read"))):
    _market(market)
    assert_market(principal, market)
    g = lake.read_curated("gaps", market, where="segment_id = ?" if segment_id else None, params=[segment_id] if segment_id else None)
    if len(g) and only_gaps:
        g = g[g["is_gap"]]
    if len(g):
        g = g.sort_values(["is_gap", "lift_lo"], ascending=False)
    return clean(g)


@router.get("/markets/{market}/recommendations")
def market_recommendations(market: str, principal: Principal = Depends(require("markets", "read"))):
    _market(market)
    assert_market(principal, market)
    return clean(lake.read_curated("recommendations", market))


# ---------------------------------------------------------------- snapshots for the time control (Phase 6)
@router.get("/markets/{market}/snapshots")
def market_snapshots(market: str, principal: Principal = Depends(require("markets", "read"))):
    """Every upload of the market with the period it describes (snapshot date, else upload date)."""
    _market(market)
    assert_market(principal, market)
    from dip.intelligence.history import dataset_period
    from dip.storage import business as b

    with b.session() as s:
        ds = s.query(b.Dataset).filter(b.Dataset.market_name == market, b.Dataset.duplicate_of.is_(None)).order_by(b.Dataset.created_at).all()
        rows = [{"dataset_id": d.id, "period": str(dataset_period(d).date()), "declared": bool(d.snapshot_date),
                 "source": d.source_name, "rows": d.accepted_rows} for d in ds]
    return sorted(rows, key=lambda r: r["period"])


@router.get("/markets/{market}/snapshots/{dataset_id}/matrix")
def snapshot_matrix(market: str, dataset_id: str, principal: Principal = Depends(require("markets", "read"))):
    """Price x observed-sales points of one historical upload (listing level, relevant and accepted rows).
    The latest snapshot's v3 estimates come from /markets/{m}/products; older snapshots show what that
    upload observed (floor values for badge data)."""
    _market(market)
    assert_market(principal, market)
    from dip.storage import business as b

    with b.session() as s:
        d = s.get(b.Dataset, dataset_id)
        if d is None or d.market_name != market:
            raise HTTPException(404, "snapshot not found")
    if not lake.std_path(dataset_id).exists():
        return []
    df = lake.read_std([dataset_id], ["id", "title", "brand", "price", "sales", "revenue", "rating", "accepted", "is_relevant"])
    df = df[df["accepted"].fillna(False).astype(bool) & df["is_relevant"].fillna(False).astype(bool)]
    cols = [c for c in ("id", "title", "brand", "price", "sales", "revenue", "rating", "segment_id", "product_id") if c in df]
    return clean(df[cols])


# ---------------------------------------------------------------- competitor intelligence on v3 (Phase 6)
@router.get("/markets/{market}/competitors-v3")
def competitors_v3(market: str, limit: int = 100, principal: Principal = Depends(require("markets", "read"))):
    """Brand shares with intervals, rank intervals, positions and signals; launch cohorts; launch momentum
    tests; share changes between the two latest snapshots (significance-tested)."""
    from dip.metrics import brands as brands_mod

    m = _market(market)
    assert_market(principal, market)
    v3 = (m.summary or {}).get("metrics_v3") or {}
    br = lake.read_curated("brands", market, order="share_est DESC", limit=limit)
    if len(br) and "signals" in br:
        br["signals"] = br["signals"].map(lambda v: json.loads(v) if isinstance(v, str) else (v or []))
    changes, change_info = brands_mod.changes(lake.read_curated("brand_history", market), config()["engine"]["interval"])
    return clean({"market": market, "info": v3.get("brands"), "as_of": v3.get("as_of"),
                  "evidence_grade": v3.get("evidence_grade"), "brands": br,
                  "cohorts": lake.read_curated("cohorts", market), "momentum": lake.read_curated("momentum", market),
                  "changes": changes, "change_info": change_info})


# ---------------------------------------------------------------- launch simulator on v3 (Phase 6)
class LaunchIn(BaseModel):
    title: str
    price: float
    specs: str = ""
    market: str | None = None
    segment_id: str | None = None
    unit_cost: float | None = None
    fulfilment_fee: float | None = None
    referral_fee: float | None = None
    fixed_monthly_cost: float = 0.0
    launch_cost: float | None = None
    rating: float | None = None
    age_days: int | None = None


class ScenarioIn(BaseModel):
    name: str | None = None
    price: float
    title: str | None = None
    specs: str | None = None
    unit_cost: float | None = None
    rating: float | None = None


class CompareIn(LaunchIn):
    price: float = 0.0
    scenarios: list[ScenarioIn]


def _launch_market(req: LaunchIn, principal: Principal) -> tuple[str, dict, dict]:
    from dip.intelligence.launch import choose_market
    from dip.metrics import launch as launch_mod

    market = req.market
    if not market:
        visible = principal.market_scope()
        market, _ = choose_market(f"{req.title} {req.specs}")
        if market and visible is not None and market not in visible:
            raise HTTPException(403, f"the closest market '{market}' is not assigned to you; pass one of yours")
    if not market:
        raise HTTPException(409, "process at least one market first")
    m = _market(market)
    assert_market(principal, market)
    data = launch_mod.load(market)
    if data is None:
        raise HTTPException(409, f"market '{market}' has no fitted demand model yet -- re-process it")
    return market, data, (m.summary or {}).get("metrics_v3") or {}


@router.post("/launch/simulate")
def launch_simulate(req: LaunchIn, principal: Principal = Depends(require("markets", "read"))):
    """Posterior-predictive launch simulation: units, revenue, profit distributions, price curve,
    empirical entrant check, gaps covered, risks, comparables."""
    from dip.metrics import launch as launch_mod

    if req.price <= 0:
        raise HTTPException(400, "price must be positive")
    market, data, summary = _launch_market(req, principal)
    return clean(launch_mod.strip(launch_mod.simulate(req.model_dump(), data, market, summary)))


@router.post("/launch/compare-v3")
def launch_compare_v3(req: CompareIn, principal: Principal = Depends(require("markets", "read"))):
    from dip.metrics import launch as launch_mod

    if not 2 <= len(req.scenarios) <= 6:
        raise HTTPException(400, "give 2 to 6 scenarios")
    if any(s.price <= 0 for s in req.scenarios):
        raise HTTPException(400, "every scenario needs a positive price")
    market, data, summary = _launch_market(req, principal)
    base = req.model_dump(exclude={"scenarios"})
    scen = [{k: v for k, v in s.model_dump().items() if v is not None} for s in req.scenarios]
    return clean(launch_mod.compare(base, scen, data, market, summary))


# ---------------------------------------------------------------- shopping mode on v3 (Phase 6)
class ShopIn(BaseModel):
    need: str
    market: str | None = None
    budget_max: float | None = None
    budget_min: float | None = None
    min_rating: float | None = None
    profile: str | None = None            # default | beginner | professional | budget
    weights: dict[str, float] | None = None
    limit: int = 12
    extract_requirements: bool = True     # read attribute, budget and rating requirements from the need text


@router.post("/shopping/recommend-v3")
def shopping_v3(q: ShopIn, principal: Principal = Depends(require("shopping", "read"))):
    """Multi-criteria recommendation: requirement coverage, Bayesian rating, proven demand (lower bound),
    price; Pareto fronts with the dominating product named; profile-weighted utility."""
    from dip.metrics.shopping import recommend

    if q.profile and q.profile not in config()["shopping"]["profiles"]:
        raise HTTPException(400, f"profile must be one of {sorted(config()['shopping']['profiles'])}")
    if q.market:
        _market(q.market)
    prods = lake.read_curated("products", q.market)
    scope = principal.market_scope()
    if len(prods) and scope is not None and "market" in prods:
        prods = prods[prods["market"].isin(scope)]
    parsed = None
    if q.extract_requirements:
        from dip.knowledge import needs
        mk = [q.market] if q.market else sorted(prods["market"].dropna().unique().tolist()) if "market" in prods else [None]
        parsed = {"requirements": [], "budget_min": None, "budget_max": None, "min_rating": None}
        for m in mk or [None]:
            p = needs.parse(q.need, m)
            for r in p["requirements"]:
                if not any(x["attribute"] == r["attribute"] for x in parsed["requirements"]):
                    parsed["requirements"].append(r)
            for k in ("budget_min", "budget_max", "min_rating"):
                parsed[k] = parsed[k] if parsed[k] is not None else p[k]
    bmax = q.budget_max if q.budget_max is not None else (parsed or {}).get("budget_max")   # explicit fields win
    bmin = q.budget_min if q.budget_min is not None else (parsed or {}).get("budget_min")
    rmin = q.min_rating if q.min_rating is not None else (parsed or {}).get("min_rating")
    out = recommend(prods, q.need, bmax, bmin, rmin, q.profile, q.weights, min(q.limit, 50),
                    requirements=(parsed or {}).get("requirements"))
    out["parsed"] = parsed
    return clean(out)


# ---------------------------------------------------------------- growth and forecast on v3
@router.get("/markets/{market}/forecast-v3")
def forecast_v3(market: str, principal: Principal = Depends(require("markets", "read"))):
    """Growth per month with its interval, a trend test, and forecasts with prediction intervals, from the v3
    revenue estimate of every snapshot (market and segments)."""
    from dip.metrics.forecast import market_forecast

    _market(market)
    assert_market(principal, market)
    return clean(market_forecast(lake.read_curated("revenue_history", market)))


# ---------------------------------------------------------------- opportunity board (what to sell, across markets)
@router.get("/opportunities-v3")
def opportunities_v3(market: str | None = None, principal: Principal = Depends(require("markets", "read"))):
    """Recommended product concepts of every visible market, each with its launch simulation, growth trend,
    competition and entry evidence; ordered by opportunity index."""
    from dip.metrics.board import board, version_of
    from dip.storage import business as b

    scope = principal.market_scope()
    with b.session() as s:
        ms = s.query(b.Market).all()
        items = [(m.name, (m.summary or {}).get("metrics_v3"), version_of(m)) for m in ms
                 if (scope is None or m.name in scope) and (market is None or m.name == market)]
    if market is not None and not items:
        _market(market)
        assert_market(principal, market)
    return clean({"items": board(items), "markets": len(items)})


# ---------------------------------------------------------------- truth harness
@router.get("/markets/{market}/integrity")
def market_integrity(market: str, principal: Principal = Depends(require("markets", "read"))):
    """Which displayed numbers of this market disagree with each other, break their own bounds, or rest on evidence
    that failed validation (``pass`` / ``warn`` / ``fail`` per check, with the values compared)."""
    from dip.metrics.integrity import check_market

    _market(market)
    assert_market(principal, market)
    return clean(check_market(market))

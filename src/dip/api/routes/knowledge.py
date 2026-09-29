"""Product knowledge API: Dental Confidence, applications, attributes, and "why does the system believe this?"
(spec 13-14, 80-81). Every route declares a typed response model."""

from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, field_validator

from dip.api.routes.catalog import _market
from dip.api.util import clean
from dip.auth import Principal, assert_market, require
from dip.storage import lake

router = APIRouter(tags=["knowledge"])

ENTITY_TYPES = ("listing", "product", "segment", "category", "taxonomy")


class Observation(BaseModel):
    metric: str
    value: float | None = None
    lo: float | None = None
    hi: float | None = None
    unit: str | None = None
    kind: str                     # observed | estimated | modeled | derived
    source: str | None = None
    source_record_id: str | None = None
    dataset_id: str | None = None
    observed_at: str | None = None
    method: str | None = None
    confidence: float | None = None


class Evidence(BaseModel):
    entity_type: str
    entity_id: str
    evidence_type: str
    subject: str | None = None
    source: str | None = None
    source_url: str | None = None
    text_excerpt: str | None = None
    captured_at: str | None = None
    confidence: float | None = None


class WhyResponse(BaseModel):
    market: str
    entity_type: str
    entity_id: str
    metric: str | None = None
    current: list[Observation]
    history: list[Observation]
    evidence: list[Evidence]
    kinds_legend: dict[str, str]


class KnowledgeSummary(BaseModel):
    market: str
    schema_name: str | None = None
    records: int = 0
    dental_band: dict[str, int] = {}
    applications: dict[str, int] = {}
    roles: dict[str, int] = {}
    attribute_coverage: float | None = None
    needs_llm: int = 0
    observations_by_kind: dict[str, int] = {}


class ListingKnowledge(BaseModel):
    id: str
    record_id: str | None = None
    product_id: str | None = None
    title: str | None = None
    brand: str | None = None
    price: float | None = None
    dental_confidence: float | None = None
    dental_band: str | None = None
    dental_verified: bool | None = None
    dental_components: dict | None = None
    primary_application: str | None = None
    applications: list[str] = []
    component_role: str | None = None
    component_role_reason: str | None = None
    configuration_key: str | None = None
    attributes: dict = {}
    attribute_coverage: float | None = None
    attribute_conflicts: list[str] = []
    dental_evidence: dict | None = None


class ListingKnowledgePage(BaseModel):
    market: str
    total: int
    rows: list[ListingKnowledge]


KINDS = {"observed": "read from the source as-is", "estimated": "an external estimate taken as-is (e.g. SellerSprite sales)",
         "modeled": "computed by the platform's statistical model, with an interval where available",
         "derived": "arithmetic over other values (median, sum, count)"}


def _j(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _obs(df: pd.DataFrame) -> list[dict]:
    if not len(df):
        return []
    return clean(df.drop(columns=[c for c in ("entity_type", "entity_id") if c in df]))


@router.get("/markets/{market}/why", response_model=WhyResponse)
def why(market: str, entity_type: str = Query(..., pattern="^(listing|product|segment|category|taxonomy)$"), entity_id: str = Query(...),
        metric: str | None = None, principal: Principal = Depends(require("markets", "read"))):
    """Provenance of an entity's values: the current observations (value, interval, kind, source, method),
    their history across uploads, and the evidence records behind the entity's classification."""
    _market(market)
    assert_market(principal, market)
    cond, params = "entity_type = ? AND entity_id = ?", [entity_type, entity_id]
    if metric:
        cond += " AND metric = ?"
        params.append(metric)
    cur = lake.read_curated("observations", market, where=cond, params=params)
    hist = lake.read_curated("observation_history", market, where=cond, params=params, order="observed_at")
    if not len(cur) and not len(hist):
        raise HTTPException(404, f"no observations for {entity_type} {entity_id!r} in {market!r}")
    if entity_type == "listing":
        ev = lake.read_curated("evidence", market, where="entity_type = 'listing' AND entity_id = ?", params=[entity_id])
    elif entity_type == "product" and lake.has_curated("listings", market):
        ids = lake.read_curated("listings", market, columns=["id"], where="product_id = ?", params=[entity_id])["id"].astype(str).tolist()
        ev = (lake.read_curated("evidence", market, where="entity_type = 'listing' AND entity_id IN (" + ",".join("?" * len(ids)) + ")",
                                params=ids, limit=200) if ids else pd.DataFrame())
    else:
        ev = pd.DataFrame()
    return {"market": market, "entity_type": entity_type, "entity_id": entity_id, "metric": metric,
            "current": _obs(cur), "history": _obs(hist), "evidence": clean(ev) if len(ev) else [], "kinds_legend": KINDS}


@router.get("/markets/{market}/knowledge", response_model=KnowledgeSummary)
def knowledge_summary(market: str, principal: Principal = Depends(require("markets", "read"))):
    m = _market(market)
    assert_market(principal, market)
    kn = (m.summary or {}).get("knowledge") or {}
    apps: dict[str, int] = {}
    if lake.has_curated("records", market) and "primary_application" in lake.curated_columns("records", market):
        a = lake.query(f"SELECT primary_application AS k, COUNT(*) AS n FROM read_parquet({lake.sql_literal(lake.curated_path('records', market))}) "
                       "GROUP BY 1 ORDER BY 2 DESC")
        apps = {str(k): int(n) for k, n in zip(a["k"], a["n"]) if k is not None}
    kinds: dict[str, int] = {}
    if lake.has_curated("observations", market):
        o = lake.read_curated("observations", market, columns=["kind"])
        kinds = {str(k): int(v) for k, v in o["kind"].value_counts().items()}
    return {"market": market, "schema_name": kn.get("schema"), "records": kn.get("records", 0), "dental_band": kn.get("dental_band", {}),
            "applications": apps, "roles": kn.get("roles", {}), "attribute_coverage": kn.get("attribute_coverage"),
            "needs_llm": kn.get("needs_llm", 0), "observations_by_kind": kinds}


LISTING_COLS = ["id", "record_id", "product_id", "title", "brand", "price", "dental_confidence", "dental_band", "dental_verified",
                "dental_components", "primary_application", "applications", "component_role", "component_role_reason",
                "configuration_key", "kn_attributes", "attribute_coverage", "attribute_conflicts", "dental_evidence"]


def _listing_row(r: dict) -> dict:
    return {**{k: r.get(k) for k in LISTING_COLS if k not in ("kn_attributes", "applications", "attribute_conflicts",
                                                               "dental_components", "dental_evidence")},
            "id": str(r.get("id")),
            "attributes": _j(r.get("kn_attributes")) or {}, "applications": [a for a in str(r.get("applications") or "").split(",") if a],
            "attribute_conflicts": [a for a in str(r.get("attribute_conflicts") or "").split(",") if a],
            "dental_components": _j(r.get("dental_components")), "dental_evidence": _j(r.get("dental_evidence"))}


@router.get("/markets/{market}/knowledge/records", response_model=ListingKnowledgePage)
def knowledge_records(market: str, band: str | None = None, application: str | None = None, role: str | None = None,
                      needs_review: bool = False, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                      principal: Principal = Depends(require("markets", "read"))):
    """Every imported record with its dental classification (dental / non-dental / ambiguous with confidence,
    spec 168 A) -- filter by band, application or role; ``needs_review`` = band review or attribute conflicts."""
    _market(market)
    assert_market(principal, market)
    if not lake.has_curated("records", market) or "dental_band" not in lake.curated_columns("records", market):
        return {"market": market, "total": 0, "rows": []}
    cols = [c for c in LISTING_COLS if c in lake.curated_columns("records", market)]
    conds, params = [], []
    if band:
        conds.append("dental_band = ?")
        params.append(band)
    if application:
        conds.append("(',' || applications || ',') LIKE ?")
        params.append(f"%,{application},%")
    if role:
        conds.append("component_role = ?")
        params.append(role)
    if needs_review:
        conds.append("(dental_band = 'review' OR attribute_conflicts IS NOT NULL)")
    where = " AND ".join(conds) or None
    total = lake.query(f"SELECT COUNT(*) AS n FROM read_parquet({lake.sql_literal(lake.curated_path('records', market))})"
                       + (f" WHERE {where}" if where else ""), params)["n"].iat[0]
    df = lake.read_curated("records", market, columns=cols, where=where, params=params,
                           order="dental_confidence ASC NULLS FIRST", limit=limit, offset=offset)
    return {"market": market, "total": int(total), "rows": [_listing_row(r) for r in clean(df)]}


# ---------------------------------------------------------------- product identity (spec 95-96, 132, 147)
class DuplicateCandidate(BaseModel):
    listing_a: str
    listing_b: str
    product_a: str | None = None
    product_b: str | None = None
    score: float | None = None
    system_decision: str
    reason: str | None = None
    title_a: str | None = None
    title_b: str | None = None
    brand_a: str | None = None
    brand_b: str | None = None
    price_a: float | None = None
    price_b: float | None = None
    configuration_a: str | None = None
    configuration_b: str | None = None
    same_image: bool = False
    status: str = "pending"


class DuplicatePage(BaseModel):
    market: str
    total: int
    rows: list[DuplicateCandidate]


class IdentityDecisionIn(BaseModel):
    listing_a: str
    listing_b: str
    decision: str          # merge | keep_separate | needs_evidence
    reason: str | None = None


class IdentityDecisionOut(BaseModel):
    saved: bool
    note: str


class IdentityEvaluation(BaseModel):
    market: str
    labelled_pairs: int
    precision: float | None = None
    recall: float | None = None
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    note: str


@router.get("/markets/{market}/duplicates", response_model=DuplicatePage)
def duplicates(market: str, status: str | None = Query(None, pattern="^(pending|merge|keep_separate|needs_evidence)$"),
               limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
               principal: Principal = Depends(require("markets", "read"))):
    """Possible duplicates near the match threshold, for a person to decide (spec 95)."""
    _market(market)
    assert_market(principal, market)
    d = lake.read_curated("duplicate_candidates", market)
    if not len(d):
        return {"market": market, "total": 0, "rows": []}
    from dip.knowledge import identity
    latest = identity.load_decisions(market)          # decisions made since the last run show immediately
    if len(latest):
        st = {tuple(sorted((a, b))): dec for a, b, dec in zip(latest["listing_a"], latest["listing_b"], latest["decision"])}
        d["status"] = [st.get(tuple(sorted((a, b))), s) for a, b, s in zip(d["listing_a"], d["listing_b"], d["status"])]
    if status:
        d = d[d["status"] == status]
    return {"market": market, "total": int(len(d)), "rows": clean(d.iloc[offset: offset + limit])}


@router.post("/markets/{market}/duplicates/decision", response_model=IdentityDecisionOut)
def decide_duplicate(market: str, body: IdentityDecisionIn, principal: Principal = Depends(require("datasets", "write"))):
    _market(market)
    assert_market(principal, market)
    from dip.knowledge.identity import DECISIONS
    if body.decision not in DECISIONS:
        raise HTTPException(422, f"decision must be one of {', '.join(DECISIONS)}")
    if body.listing_a == body.listing_b:
        raise HTTPException(422, "a listing cannot be compared with itself")
    from dip import audit
    from dip.storage import business as b
    with b.session() as s:
        s.add(b.IdentityDecision(market_name=market, listing_a=body.listing_a, listing_b=body.listing_b, decision=body.decision,
                                 reason=body.reason, decided_by=principal.email or "local user"))
    audit.record("identity.decide", principal, "markets", market, body.model_dump())
    return {"saved": True, "note": "applied on the next processing run of this market; counted in the resolver evaluation now"}


@router.get("/markets/{market}/identity/evaluation", response_model=IdentityEvaluation)
def identity_evaluation(market: str, principal: Principal = Depends(require("markets", "read"))):
    """Precision / recall of the automatic duplicate decisions against people's decisions (spec 132)."""
    _market(market)
    assert_market(principal, market)
    from dip.knowledge import identity
    ev = identity.evaluate(lake.read_curated("dedup_pairs", market), identity.load_decisions(market))
    return {"market": market, **ev,
            "note": "pairs a person decided merge or keep separate; 'needs evidence' is excluded. A merge the resolver never "
                    "scored counts as a miss."}


# ---------------------------------------------------------------- dynamic taxonomy (spec 9-10, 103-104, 161)
class TaxonomyNode(BaseModel):
    node_key: str
    parent_key: str | None = None
    depth: int
    dimension: str
    value: str
    label: str
    products: int
    listings: int | None = None
    revenue_est: float | None = None
    revenue_lo: float | None = None
    revenue_hi: float | None = None
    price_median: float | None = None
    price_min: float | None = None
    price_max: float | None = None
    eta2: float | None = None
    p_value: float | None = None
    explanation: str | None = None
    status: str                   # machine | approved | rejected | renamed
    approved_label: str | None = None


class TaxonomyDimension(BaseModel):
    dimension: str
    coverage: float
    groups: dict[str, int]
    eta2: float
    p_value: float
    rank_score: float | None = None
    meaningful: bool


class TaxonomyResponse(BaseModel):
    market: str
    view: str                     # machine | approved
    nodes: list[TaxonomyNode]
    dimensions: list[TaxonomyDimension]
    note: str


class TaxonomyDecisionIn(BaseModel):
    node_key: str
    decision: str                 # approved | rejected | renamed
    label: str | None = None
    reason: str | None = None


@router.get("/markets/{market}/taxonomy", response_model=TaxonomyResponse)
def taxonomy(market: str, view: str = Query("machine", pattern="^(machine|approved)$"),
             principal: Principal = Depends(require("markets", "read"))):
    """The discovered taxonomy. ``view=approved`` keeps only approved/renamed nodes whose ancestors are all
    approved -- the human-validated taxonomy (spec 10: both are retained)."""
    _market(market)
    assert_market(principal, market)
    n = lake.read_curated("taxonomy_nodes", market)
    d = lake.read_curated("taxonomy_dimensions", market)
    if len(n):
        from dip.knowledge import taxonomy_discovery as tax
        dec = tax.load_decisions(market)            # decisions since the last run show immediately
        if len(dec):
            latest = dec.drop_duplicates("node_key", keep="last").set_index("node_key")
            st = n["node_key"].map(latest["decision"])
            n["status"] = st.fillna(n["status"])
            n["approved_label"] = n["node_key"].map(latest["label"]).where(st.isin(["renamed", "approved"]), n["approved_label"])
        n = n.drop(columns=["product_ids"], errors="ignore")
        if view == "approved":
            ok = set(n.loc[n["status"].isin(["approved", "renamed"]), "node_key"])
            n = n[[k in ok and all("/".join(k.split("/")[:i]) in ok for i in range(1, k.count("/") + 1)) for k in n["node_key"]]]
    dims = [{**r, "groups": _j(r["groups"]) or {}} for r in clean(d)] if len(d) else []
    return {"market": market, "view": view, "nodes": clean(n) if len(n) else [], "dimensions": dims,
            "note": "a dimension becomes a level only when it covers enough products and separates price (permutation test)"}


@router.post("/markets/{market}/taxonomy/decision", response_model=IdentityDecisionOut)
def decide_taxonomy(market: str, body: TaxonomyDecisionIn, principal: Principal = Depends(require("datasets", "write"))):
    _market(market)
    assert_market(principal, market)
    if body.decision not in ("approved", "rejected", "renamed"):
        raise HTTPException(422, "decision must be approved, rejected or renamed")
    if body.decision == "renamed" and not (body.label or "").strip():
        raise HTTPException(422, "a renamed node needs a label")
    n = lake.read_curated("taxonomy_nodes", market, columns=["node_key"], where="node_key = ?", params=[body.node_key])
    if not len(n):
        raise HTTPException(404, f"no taxonomy node {body.node_key!r} in {market!r}")
    from dip import audit
    from dip.storage import business as b
    with b.session() as s:
        s.add(b.TaxonomyDecision(market_name=market, node_key=body.node_key, decision=body.decision,
                                 label=(body.label or "").strip() or None, reason=body.reason, decided_by=principal.email or "local user"))
    audit.record("taxonomy.decide", principal, "markets", market, body.model_dump())
    return {"saved": True, "note": "shown now; kept across runs while the node's key is rediscovered"}


# ---------------------------------------------------------------- market capacity / leaf intelligence (spec 21-26, 37-38, 41)
class CapacityRow(BaseModel):
    scope: str                    # category | segment | taxonomy
    scope_id: str
    label: str
    products: int
    listings: int
    brands: int | None = None
    sellers: int | None = None
    units_est: float | None = None
    units_lo: float | None = None
    units_hi: float | None = None
    revenue_est: float | None = None
    revenue_lo: float | None = None
    revenue_hi: float | None = None
    interval_method: str | None = None
    revenue_source_estimate: float | None = None
    revenue_source_coverage: float | None = None
    offline_adjusted_revenue: float | None = None
    price_min: float | None = None
    price_p25: float | None = None
    price_median: float | None = None
    price_mean: float | None = None
    price_p75: float | None = None
    price_max: float | None = None
    tier_entry_below: float | None = None
    tier_premium_from: float | None = None
    avg_rating: float | None = None
    rating_basis: str | None = None
    reviews: float | None = None
    dental_confidence: float | None = None
    data_confidence: float | None = None
    hhi: float | None = None
    top_brand: str | None = None
    top3_share: float | None = None
    top5_share: float | None = None
    top10_share: float | None = None
    long_tail_share: float | None = None
    fee_headroom: float | None = None
    max_fob_median: float | None = None
    landed_cost_coverage: float | None = None
    landed_cost_basis: str | None = None
    max_fob_by_duty: dict[str, float] | None = None   # sourcing ceiling under each duty scenario (config)

    @field_validator("max_fob_by_duty", mode="before")
    @classmethod
    def _json_dict(cls, v):
        return json.loads(v) if isinstance(v, str) else v


class CapacityResponse(BaseModel):
    market: str
    rows: list[CapacityRow]
    value_kinds: dict[str, str]


@router.get("/markets/{market}/capacity", response_model=CapacityResponse)
def capacity(market: str, scope: str | None = Query(None, pattern="^(category|segment|taxonomy)$"),
             principal: Principal = Depends(require("markets", "read"))):
    """Market capacity with every value's kind: modeled demand with an interval, the source's own estimates
    summed separately, offline-adjusted empty until offline evidence exists (spec 23)."""
    _market(market)
    assert_market(principal, market)
    cap = lake.read_curated("capacity", market, where="scope = ?" if scope else None, params=[scope] if scope else None)
    return {"market": market, "rows": clean(cap) if len(cap) else [],
            "value_kinds": {"units_est / revenue_est": "modeled (demand model, 95% interval)",
                            "revenue_source_estimate": "estimated (the source's own figures, summed where listed)",
                            "offline_adjusted_revenue": "not available until offline evidence is loaded",
                            "counts / prices": "derived from observed listings",
                            "fee_headroom / max_fob_median": "derived from observed FBA fee and weight with assumed freight, "
                                                              "referral and target margin (see landed_cost_basis)"}}


# ---------------------------------------------------------------- opportunity engine (spec 35-36, 71-74, 155-156)
class EvidenceCell(BaseModel):
    status: str                  # High | Medium | Low | Unknown
    score: float | None = None
    metric: str
    value: float | None = None
    basis: str | None = None


class OpportunityRow(BaseModel):
    scope: str
    scope_id: str
    label: str
    products: int
    rank: float | None = None
    opportunity_score: float | None = None
    raw_score: float | None = None
    evidence_coverage: float
    status: str                  # detected | insufficient_evidence | gated | a linked project's stage
    gated_by: list[str] = []
    project_id: str | None = None
    confidence: float | None = None
    dimensions: dict[str, float | None]
    evidence_matrix: dict[str, EvidenceCell]
    patterns: list[dict]
    risks: list[dict]
    reasons: list[str]


class OpportunityResponse(BaseModel):
    market: str
    weights: dict[str, float]
    rows: list[OpportunityRow]
    note: str


@router.get("/markets/{market}/opportunities/explained", response_model=OpportunityResponse)
def opportunities_explained(market: str, scope: str | None = Query(None, pattern="^(segment|taxonomy)$"),
                            ranked_only: bool = False, principal: Principal = Depends(require("markets", "read"))):
    """Every opportunity with its dimension scores, evidence matrix, patterns, risks and reasons. A scope
    without enough measured evidence is 'insufficient_evidence' and has no rank."""
    _market(market)
    assert_market(principal, market)
    from dip.knowledge import config as kcfg
    from dip.knowledge.opportunity import DIMENSIONS
    o = lake.read_curated("opportunities", market, where="scope = ?" if scope else None, params=[scope] if scope else None)
    rows = []
    for r in clean(o) if len(o) else []:
        if ranked_only and r.get("opportunity_score") is None:
            continue
        rows.append({**{k: r.get(k) for k in ("scope", "scope_id", "label", "products", "rank", "opportunity_score", "raw_score",
                                               "evidence_coverage", "status", "project_id", "confidence")},
                     "dimensions": {d: r.get(f"dim_{d}") for d in DIMENSIONS},
                     "evidence_matrix": _j(r.get("evidence_matrix")) or {}, "patterns": _j(r.get("patterns")) or [],
                     "risks": _j(r.get("risks")) or [], "reasons": _j(r.get("reasons")) or [],
                     "gated_by": _j(r.get("gated_by")) or []})
    return {"market": market, "weights": kcfg()["opportunity"]["weights"], "rows": rows,
            "note": "dimensions without evidence are excluded and re-weighted, never scored as neutral; see evidence_coverage"}


# ---------------------------------------------------------------- offline evidence (spec 31-34)
class OfflineRow(BaseModel):
    source_type: str
    source_name: str
    applies_to: str | None = None
    signal: str | None = None
    value: float | None = None
    url: str | None = None
    observed_at: str | None = None
    notes: str | None = None


class OfflineEvidenceResponse(BaseModel):
    market: str
    rows: list[OfflineRow]
    source_weights: dict[str, float]
    note: str


class OfflineUploadResponse(BaseModel):
    market: str
    accepted: int
    rejected: list[dict]
    total: int
    note: str


def _offline_rows(df: pd.DataFrame) -> list[dict]:
    return [{k: (None if v is None or (isinstance(v, float) and v != v) else (str(v) if k in ("observed_at",) else v))
             for k, v in r.items()} for r in df.to_dict("records")]


@router.get("/markets/{market}/offline-evidence", response_model=OfflineEvidenceResponse)
def offline_evidence(market: str, principal: Principal = Depends(require("markets", "read"))):
    """The offline evidence loaded for a market and the weight of each source type."""
    _market(market)
    assert_market(principal, market)
    from dip.knowledge import config as kcfg
    from dip.knowledge import offline
    return {"market": market, "rows": _offline_rows(offline.load(market)), "source_weights": kcfg()["offline"]["source_weights"],
            "note": "scored into the offline-strength dimension on the next processing run of this market"}


@router.post("/markets/{market}/offline-evidence", response_model=OfflineUploadResponse)
def upload_offline_evidence(market: str, file: UploadFile = File(...), replace: bool = Form(False),
                            principal: Principal = Depends(require("datasets", "write"))):
    """Load offline evidence from a CSV / Excel table (columns: source_type, source_name, applies_to, signal,
    value, url, observed_at, notes). Invalid rows are rejected with their row number and reason."""
    _market(market)
    assert_market(principal, market)
    from dip import audit
    from dip.api.routes.operations import _save_upload
    from dip.knowledge import offline
    from dmie.engine.ingestion.adapters import read_table
    try:
        raw = read_table(_save_upload(file))
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"could not read the table: {e}") from e
    ok, rejected = offline.validate(raw)
    prev = pd.DataFrame(columns=offline.COLUMNS) if replace else offline.load(market).drop(columns=["market"], errors="ignore")
    allrows = pd.concat([prev, ok], ignore_index=True).drop_duplicates(ignore_index=True)
    for c in ("value",):
        allrows[c] = pd.to_numeric(allrows[c], errors="coerce")
    for c in [c for c in offline.COLUMNS if c != "value"]:
        allrows[c] = allrows[c].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v))
    lake.write_curated("offline_evidence", market, allrows)
    audit.record("offline.upload", principal, "markets", market, {"accepted": int(len(ok)), "rejected": len(rejected), "replace": replace})
    return {"market": market, "accepted": int(len(ok)), "rejected": rejected, "total": int(len(allrows)),
            "note": "applied to the offline-strength dimension on the next processing run of this market"}


# ---------------------------------------------------------------- data anomalies (spec 154)
class AnomalyRow(BaseModel):
    entity_id: str
    product_id: str | None = None
    code: str
    severity: str
    value: float | None = None
    expected: float | None = None
    detail: str
    title: str | None = None


class AnomalyResponse(BaseModel):
    market: str
    rows: list[AnomalyRow]
    counts: dict[str, int]
    note: str


@router.get("/markets/{market}/anomalies", response_model=AnomalyResponse)
def anomalies(market: str, code: str | None = None, limit: int = Query(200, ge=1, le=2000),
              principal: Principal = Depends(require("markets", "read"))):
    """Values that are probably wrong or changed suspiciously, for review. Nothing is dropped or corrected."""
    _market(market)
    assert_market(principal, market)
    a = lake.read_curated("anomalies", market) if lake.has_curated("anomalies", market) else pd.DataFrame()
    counts = a["code"].value_counts().to_dict() if len(a) else {}
    if len(a) and code:
        a = a[a["code"] == code]
    if len(a) and lake.has_curated("listings", market):
        t = lake.read_curated("listings", market, columns=["id", "title"])
        a = a.merge(t.assign(id=t["id"].astype(str)).rename(columns={"id": "entity_id"}), on="entity_id", how="left")
    sev = {"high": 0, "medium": 1, "low": 2}
    rows = clean(a.assign(_s=a["severity"].map(sev)).sort_values("_s").drop(columns="_s").head(limit)) if len(a) else []
    return {"market": market, "rows": rows, "counts": {k: int(v) for k, v in counts.items()},
            "note": "flagged for review only: no value was dropped or corrected"}


# ---------------------------------------------------------------- global search (spec 118-119)
class SearchHit(BaseModel):
    kind: str                     # product | segment | taxonomy | brand | supplier
    id: str
    label: str
    market: str | None = None
    score: float | None = None    # product: vector similarity; others: share of query terms matched
    detail: str | None = None


class SearchResponse(BaseModel):
    q: str
    hits: dict[str, list[SearchHit]]
    semantic: bool
    note: str


def _terms(q: str) -> list[str]:
    import re
    return [t for t in re.split(r"[\s,;/]+", q.lower()) if t]


def _match(text, terms: list[str]) -> float:
    s = str(text or "").lower()
    return sum(t in s for t in terms) / len(terms) if terms else 0.0


@router.get("/search", response_model=SearchResponse)
def global_search(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(8, ge=1, le=30),
                  principal: Principal = Depends(require("markets", "read"))):
    """One query across every visible market: products by meaning (vector index), sub-categories, taxonomy
    nodes and brands by their names, and suppliers (when the role may read them). Text hits need every
    query term unless nothing matches fully, then the best partial matches are shown with their share."""
    from dip.storage import business as b
    from dip.storage.vectors import get_vector_store

    terms = _terms(q)
    scope = principal.market_scope()
    with b.session() as s:
        markets = [m.name for m in s.query(b.Market).all() if scope is None or m.name in scope]
    hits: dict[str, list[dict]] = {"product": [], "segment": [], "taxonomy": [], "brand": [], "supplier": []}
    semantic = True
    try:
        for h in get_vector_store().similar_to_text(q, limit=limit * 2):
            if h.get("market") in markets:
                hits["product"].append({"kind": "product", "id": str(h.get("product_id")), "label": str(h.get("title") or h.get("product_id")),
                                        "market": h.get("market"), "score": h.get("score"),
                                        "detail": " · ".join(x for x in (h.get("brand"), f"${h['price']:,.2f}" if h.get("price") else None) if x)})
    except Exception:  # noqa: BLE001 -- an unavailable vector index degrades to the text results
        semantic = False
    hits["product"] = hits["product"][:limit]
    for m in markets:
        if lake.has_curated("segments", m):
            sg = lake.read_curated("segments", m)
            for r in sg.to_dict("records"):
                sc = _match(r.get("segment_label"), terms)
                if sc > 0:
                    hits["segment"].append({"kind": "segment", "id": str(r["segment_id"]), "label": str(r.get("segment_label")),
                                            "market": m, "score": round(sc, 3), "detail": None})
        if lake.has_curated("taxonomy_nodes", m):
            for r in lake.read_curated("taxonomy_nodes", m).to_dict("records"):
                sc = _match(r.get("label"), terms)
                if sc > 0:
                    hits["taxonomy"].append({"kind": "taxonomy", "id": str(r["node_key"]), "label": str(r.get("label")), "market": m,
                                             "score": round(sc, 3), "detail": f"{int(r.get('products') or 0)} products"})
        if lake.has_curated("listings", m):
            br = lake.read_curated("listings", m, columns=["brand"])["brand"].dropna().astype(str)
            for name, n in br.value_counts().items():
                sc = _match(name, terms)
                if sc > 0:
                    hits["brand"].append({"kind": "brand", "id": name, "label": name, "market": m, "score": round(sc, 3),
                                          "detail": f"{int(n)} listings"})
    if principal.can("suppliers", "read"):
        with b.session() as s:
            for x in s.query(b.Supplier).all():
                if (x.org_id or b.DEFAULT_ORG) != principal.org:
                    continue
                sc = max(_match(x.name, terms), _match(x.product_categories, terms))
                if sc > 0:
                    hits["supplier"].append({"kind": "supplier", "id": x.id, "label": x.name, "market": None, "score": round(sc, 3),
                                             "detail": " · ".join(v for v in (x.country, x.business_type) if v)})
    for k in ("segment", "taxonomy", "brand", "supplier"):
        full = [h for h in hits[k] if h["score"] >= 1]
        hits[k] = sorted(full or hits[k], key=lambda h: -h["score"])[:limit]
    return {"q": q, "hits": hits, "semantic": semantic,
            "note": "products ranked by meaning (vector similarity); other results by the share of query terms in their names"}


# ---------------------------------------------------------------- product requirements brief (spec 77)
@router.get("/markets/{market}/requirements")
def requirements(market: str, scope: str = Query(..., pattern="^(segment|taxonomy)$"), id: str = Query(..., min_length=1),
                 format: str = Query("json", pattern="^(json|md)$"), principal: Principal = Depends(require("markets", "read"))):
    """What a new product for one sub-category or taxonomy node must be, assembled from computed facts only
    (price band, must-have attributes, differentiators, configuration, pain to fix, sourcing, compliance)."""
    _market(market)
    assert_market(principal, market)
    from fastapi.responses import PlainTextResponse

    from dip.knowledge import requirements as rq

    def t(name):
        return lake.read_curated(name, market) if lake.has_curated(name, market) else pd.DataFrame()
    req = rq.build(scope, id, t("capacity"), t("opportunities"), t("products"), t("taxonomy_nodes"), t("recommendations"))
    if req is None:
        raise HTTPException(404, f"no {scope} '{id}' in market '{market}'")
    if format == "md":
        from urllib.parse import quote
        name = quote(f"requirements_{market}_{req['label']}.md".replace("/", "-").replace(" ", "_"))
        return PlainTextResponse(rq.markdown(req, market), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})
    return clean(req)


# ---------------------------------------------------------------- accuracy of the knowledge layer (spec 130-134)
@router.get("/markets/{market}/knowledge/accuracy")
def knowledge_accuracy(market: str, principal: Principal = Depends(require("markets", "read"))):
    """Dental Confidence against the gold benchmark (model-labelled, reported as such) and against people's
    labels; resolver precision / recall against identity decisions; taxonomy decisions; and how stable the
    opportunity ranking is when every weight moves by +/- the configured spread."""
    _market(market)
    assert_market(principal, market)
    from dip.knowledge import config as kcfg
    from dip.knowledge import evaluation as ev
    from dip.knowledge import identity
    from dip.storage import business as b

    rec = lake.read_curated("records", market, columns=["id", "dental_band"]) if lake.has_curated("records", market) else pd.DataFrame()
    edges = lake.read_curated("duplicate_candidates", market) if lake.has_curated("duplicate_candidates", market) else pd.DataFrame()
    with b.session() as s:
        tax = [x.decision for x in s.query(b.TaxonomyDecision).filter_by(market_name=market).all()]
    opps = lake.read_curated("opportunities", market) if lake.has_curated("opportunities", market) else pd.DataFrame()
    return clean({
        "market": market,
        "dental": {"gold_benchmark": ev.gold_benchmark(market, rec) if len(rec) else None, "human_labels": ev.human_dental(market)},
        "identity": identity.evaluate(edges, identity.load_decisions(market)) if len(edges) else None,
        "taxonomy_decisions": {d: tax.count(d) for d in sorted(set(tax))},
        "ranking_stability": ev.ranking_stability(opps, kcfg()["opportunity"]["weights"]) if len(opps) else None,
        "note": "rates appear only with at least evaluation.min_labels labelled rows; a review band is counted apart, not as an error",
    })

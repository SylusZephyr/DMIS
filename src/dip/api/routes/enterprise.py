"""Enterprise intelligence layer: data confidence, product hierarchy, trends,
competitors, launch evaluation, analyst, events and alerts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dip.api.deprecation import gone, responses
from dip.api.util import clean
from dip.cache import response_cache
from dip.metrics.facts import market_revenue
from dip.auth import Principal, require, visible_markets
from dip.storage import business as b
from dip.storage import lake

router = APIRouter(tags=["enterprise intelligence"])


def market_or_404(name: str) -> b.Market:
    with b.session() as s:
        m = s.get(b.Market, name)
        if m is None:
            raise HTTPException(404, f"market '{name}' not found")
        return m


@router.get("/markets/{market}/hierarchy", dependencies=[Depends(require("markets", "read"))])
@response_cache
def hierarchy(market: str, segment: str | None = None, listings: bool = True, listing_limit: int = Query(20, le=500)):
    """Category -> Family -> Segment -> Model -> Variant -> Listing with revenue, opportunity and confidence."""
    from dip.pipeline.clustering.hierarchy import hierarchy_tree

    market_or_404(market)
    have = lake.curated_columns("products", market)
    if "variant_label" not in have:
        raise HTTPException(409, "this market was processed before the hierarchy upgrade; re-process it")
    cols = [c for c in ["product_id", "title", "brand", "family_id", "segment_id", "model_id", "model_label", "model_basis",
                        "variant_label", "price", "monthly_revenue", "opportunity_score", "confidence_score"] if c in have]
    where, params = ("segment_id = ?", [segment]) if segment else (None, None)
    v3 = "revenue_est" in have                    # metrics v3: estimated revenue (badge floors are only lower bounds)
    if v3:
        cols.append("revenue_est")
    prods = lake.read_curated("products", market, columns=cols, where=where, params=params)
    if v3:
        prods["monthly_revenue"] = prods["revenue_est"]
    segs = lake.read_curated("segments", market)
    lst = None
    if listings:
        lcols = lake.curated_columns("listings", market)
        units = "units_est" if "units_est" in lcols else "sales"
        lst = lake.read_curated("listings", market, columns=["id", "product_id", "price", units, "is_best_listing"],
                                where=where, params=params, order=f"{units} DESC NULLS LAST").rename(columns={units: "sales"})
    return clean(hierarchy_tree(prods, segs, lst, market, listing_limit))


@router.get("/markets/{market}/confidence", dependencies=[Depends(require("markets", "read"))])
def market_confidence(market: str, level: str | None = None, limit: int = Query(50, le=5000)):
    """Market / segment / product confidence with components and reasons."""
    m = market_or_404(market)
    have = lake.curated_columns("products", market)
    if "confidence_score" not in have:
        raise HTTPException(409, "this market was processed before the confidence framework; re-process it")
    where, params = ("confidence_level = ?", [level]) if level else (None, None)
    prods = lake.read_curated("products", market, columns=["product_id", "title", "brand", "segment_id", "monthly_revenue",
                                                          "confidence_score", "confidence_level", "confidence_components",
                                                          "confidence_reasons"],
                              where=where, params=params, order="monthly_revenue DESC NULLS LAST", limit=limit)
    segs = lake.read_curated("segments", market, columns=["segment_id", "segment_label", "confidence_score", "confidence_level",
                                                         "coverage", "sales_coverage"], order="confidence_score DESC")
    return clean({"market": (m.summary or {}).get("confidence"), "history_periods": (m.summary or {}).get("history_periods"),
                  "segments": segs, "products": prods})


def _needs(market: str, table: str) -> None:
    if not lake.has_curated(table, market):
        raise HTTPException(409, f"'{table}' not computed for this market yet; re-process it")


@router.get("/markets/{market}/trends", dependencies=[Depends(require("markets", "read"))])
def market_trends(market: str):
    """Trend per market and segment: label, confidence, expected 12-month growth, signals, evidence."""
    market_or_404(market)
    _needs(market, "trends")
    t = lake.read_curated("trends", market)
    segs = lake.read_curated("segments", market, columns=["segment_id", "segment_label", "monthly_revenue", "products"])
    t = t.merge(segs.rename(columns={"segment_id": "scope"}), on="scope", how="left")
    t.loc[t["scope"] == "__market__", "segment_label"] = market
    return clean({"market": t[t["scope"] == "__market__"].to_dict("records")[0],
                  "segments": t[t["scope"] != "__market__"].sort_values("direction", ascending=False, na_position="last")})


@router.get("/trends")
@response_cache
def all_trends(principal: Principal = Depends(require("markets", "read"))):
    """Which markets are growing? Market-level trend of every market."""
    with b.session() as s:
        rows = [{"market": m.name, "industry_branch": m.industry_branch, **((m.summary or {}).get("trend") or {}),
                 "monthly_revenue": market_revenue(m.summary)["headline"],
                 "monthly_revenue_basis": market_revenue(m.summary)["basis"]}
                for m in s.query(b.Market).all()]
    rows = visible_markets(principal, rows)
    order = {"Growing": 0, "Emerging": 1, "Stable": 2, "Mature": 3, "Declining": 4}
    return clean(sorted(rows, key=lambda r: (order.get(r.get("trend"), 5), -(r.get("direction") or 0))))


@router.get("/markets/{market}/competitors", deprecated=True, responses=responses("GET /markets/{market}/competitors"))
def market_competitors(market: str):
    """Retired (410): use GET /markets/{market}/competitors-v3."""
    gone("GET /markets/{market}/competitors")


@router.get("/competitors/{brand}", deprecated=True, responses=responses("GET /competitors/{brand}"))
def brand_profile(brand: str):
    """Retired (410): use GET /markets/{market}/competitors-v3."""
    gone("GET /competitors/{brand}")


@router.post("/launch/evaluate", deprecated=True, responses=responses("POST /launch/evaluate"))
def launch_evaluate():
    """Retired (410): use POST /launch/simulate."""
    gone("POST /launch/evaluate")


# ------------------------------------------------------------------ events, alerts, connectors
INBOUND_KINDS = {"news.item", "external.signal"}


class InboundEvent(BaseModel):
    kind: str
    market: str | None = None
    subject: str | None = None
    severity: str = "notice"
    payload: dict = {}


@router.get("/events")
def list_events(market: str | None = None, kind: str | None = None, severity: str | None = None,
                limit: int = Query(100, le=2000), principal: Principal = Depends(require("markets", "read"))):
    with b.session() as s:
        q = s.query(b.Event).order_by(b.Event.created_at.desc())
        if market:
            q = q.filter(b.Event.market_name == market)
        if kind:
            q = q.filter(b.Event.kind.like(kind.replace("*", "%")))
        if severity:
            q = q.filter(b.Event.severity == severity)
        scope = principal.market_scope()
        if scope is not None:
            q = q.filter(b.Event.market_name.in_(scope))
        return clean([b.row_dict(e) for e in q.limit(limit).all()])


@router.post("/events", dependencies=[Depends(require("datasets", "write"))])
def inbound_event(ev: InboundEvent):
    """External push (news, signals from other systems). News is matched to markets by text;
    notice/important events alert the market's owners."""
    from dip import events
    from dip.connectors import publish_news

    if ev.kind not in INBOUND_KINDS:
        raise HTTPException(400, f"inbound kind must be one of {sorted(INBOUND_KINDS)} (upload datasets via /datasets)")
    if ev.severity not in events.SEVERITIES:
        raise HTTPException(400, f"severity must be one of {events.SEVERITIES}")
    if ev.kind == "news.item" and not ev.market:
        item = {"title": ev.subject or ev.payload.get("title", ""), **ev.payload}
        return {"event_ids": publish_news(item, "api")}
    if ev.market:
        market_or_404(ev.market)
    return {"event_ids": [events.publish(ev.kind, ev.market, ev.subject, ev.payload, ev.severity, "api")]}


def _employee_for(principal: Principal, employee_id: str | None) -> str | None:
    if principal.user_id:
        with b.session() as s:
            u = s.get(b.User, principal.user_id)
            own = u.employee_id if u else None
        if employee_id and employee_id != own and not principal.can("people", "write"):
            raise HTTPException(403, "you can only read your own alerts")
        return employee_id or own
    return employee_id


@router.get("/alerts")
def list_alerts(employee_id: str | None = None, status: str | None = None, limit: int = Query(100, le=2000),
                principal: Principal = Depends(require("markets", "read"))):
    emp = _employee_for(principal, employee_id)
    with b.session() as s:
        q = s.query(b.Alert).join(b.Event).order_by(b.Event.created_at.desc())
        if emp:
            q = q.filter(b.Alert.employee_id == emp)
        if status:
            q = q.filter(b.Alert.status == status)
        rows = q.limit(limit).all()
        out = [{**b.row_dict(a), "event": b.row_dict(a.event),
                "employee": (s.get(b.Employee, a.employee_id).name if s.get(b.Employee, a.employee_id) else None)} for a in rows]
    return clean(out)


class AlertUpdate(BaseModel):
    status: str


@router.post("/alerts/{alert_id}")
def update_alert(alert_id: str, upd: AlertUpdate, principal: Principal = Depends(require("markets", "read"))):
    if upd.status not in ("new", "read", "done"):
        raise HTTPException(400, "status must be new, read or done")
    with b.session() as s:
        a = s.get(b.Alert, alert_id)
        if a is None:
            raise HTTPException(404, "alert not found")
        _employee_for(principal, a.employee_id)
        a.status = upd.status
    return {"saved": True}


@router.get("/connectors", dependencies=[Depends(require("datasets", "read"))])
def connectors():
    from dip.connectors import REGISTRY

    return [st.__dict__ for st in (c.status() for c in REGISTRY.values())]


@router.post("/connectors/{name}/poll")
def poll_connector(name: str, principal: Principal = Depends(require("datasets", "write"))):
    from dip.connectors import REGISTRY

    if name not in REGISTRY:
        raise HTTPException(404, f"unknown connector '{name}'")
    from dip import tenancy
    if not tenancy.has_feature(principal.org_id, "connectors"):
        raise HTTPException(402, "your plan does not include live connectors")
    try:
        return REGISTRY[name].poll()
    except Exception as exc:  # network / parse errors are reported, not raised as 500s
        raise HTTPException(502, f"{name}: {exc}") from exc


# ------------------------------------------------------------------ employee intelligence
@router.get("/markets/{market}/brief", dependencies=[Depends(require("markets", "read"))])
def market_brief(market: str):
    """One-screen market brief: size, products, opportunity level, suggested development, trend,
    confidence, competitors, suppliers."""
    from dip.intelligence.briefs import market_brief as brief

    market_or_404(market)
    return clean(brief(market))


@router.get("/employees/{employee_id}/focus")
def employee_focus(employee_id: str, principal: Principal = Depends(require("people", "read"))):
    """What should this employee focus on: categories, market status, competitors, suppliers, actions."""
    from dip.intelligence.briefs import employee_focus as focus

    out = focus(employee_id)
    if out is None:
        raise HTTPException(404, "employee not found")
    scope = principal.market_scope()
    if scope is not None:
        out["markets"] = [m for m in out["markets"] if m["market"] in scope]
        out["recommended_actions"] = [a for a in out["recommended_actions"] if a["market"] is None or a["market"] in scope]
    return clean(out)


# ------------------------------------------------------------------ analyst agent
@router.post("/analyst/ask", deprecated=True, responses=responses("POST /analyst/ask"))
def analyst_ask():
    """Retired (410): use POST /analyst/ask-v3."""
    gone("POST /analyst/ask")


class AnalystQuestionV3(BaseModel):
    question: str
    use_ai: bool = False
    lang: str = "en"


@router.post("/analyst/ask-v3")
def analyst_ask_v3(q: AnalystQuestionV3, principal: Principal = Depends(require("markets", "read"))):
    """Facts computed from metrics v3 (each with its source path); optional Gemini phrasing that is rejected
    when it contains a number not in the facts; every AI call traced."""
    from dip import tenancy
    from dip.intelligence.analyst_v3 import ask

    if not q.question.strip():
        raise HTTPException(400, "question is empty")
    if q.lang not in ("en", "zh"):
        raise HTTPException(400, "lang must be en or zh")
    if q.use_ai and not tenancy.has_feature(principal.org_id, "analyst_ai"):
        raise HTTPException(402, "your plan does not include AI phrasing (analyst_ai)")
    with b.session() as s:
        names = [m.name for m in s.query(b.Market).all()]
    out = ask(q.question, visible_markets(principal, names), q.use_ai, q.lang)
    tenancy.meter(principal.org_id, "analyst_questions", 1)
    if q.use_ai and out.get("ai_status") != "unavailable":
        tenancy.meter(principal.org_id, "ai_calls", 1)
    return clean(out)


@router.get("/ai/traces", dependencies=[Depends(require("datasets", "read"))])
def ai_traces(limit: int = Query(50, le=1000)):
    """Every AI call: model, prompt version, input, output, status, time."""
    with b.session() as s:
        return clean([b.row_dict(t) for t in s.query(b.AITrace).order_by(b.AITrace.created_at.desc()).limit(limit).all()])


# ------------------------------------------------------------------ scenario comparison (P3b, retired)
@router.post("/launch/compare", deprecated=True, responses=responses("POST /launch/compare"))
def launch_compare():
    """Retired (410): use POST /launch/compare-v3."""
    gone("POST /launch/compare")

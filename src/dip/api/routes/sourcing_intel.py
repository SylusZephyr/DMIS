"""Sourcing intelligence: marketplace status, concept preview, supplier search runs, offers, reaching out."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dip.api.util import clean
from dip.auth import Principal, assert_market, require

router = APIRouter(tags=["sourcing"])


class ConceptIn(BaseModel):
    market: str
    scope: str | None = None          # segment | taxonomy (from the board / intelligence map)
    scope_id: str | None = None
    text: str | None = None           # or a free-text product idea
    target_price: float | None = None
    qty: int | None = None
    platforms: list[str] | None = None
    project_id: str | None = None


def _concept(body: ConceptIn):
    from dip.sourcing_intel import concept as cp

    if body.scope and body.scope_id:
        try:
            return cp.from_scope(body.market, body.scope, body.scope_id, body.target_price, body.qty)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    if body.text and body.text.strip():
        return cp.from_text(body.market, body.text, body.target_price, body.qty)
    raise HTTPException(400, "give scope + scope_id (a sub-category or taxonomy node) or text (a product idea)")


@router.get("/sourcing/status", dependencies=[Depends(require("suppliers", "read"))])
def sourcing_status():
    from dip.sourcing_intel import status
    from dip.sourcing_intel.platforms import config

    plats = status()
    return {"platforms": plats, "ready": any(p["configured"] for p in plats), "fx": config()["fx"],
            "weights": config()["weights"], "run": config()["run"]}


@router.post("/sourcing/concept")
def sourcing_concept(body: ConceptIn, principal: Principal = Depends(require("suppliers", "read"))):
    """What would be searched for, and the facts offers will be judged against (no marketplace call)."""
    assert_market(principal, body.market)
    return clean(_concept(body).to_dict())


@router.post("/sourcing/runs")
def sourcing_run(body: ConceptIn, principal: Principal = Depends(require("suppliers", "write"))):
    """Search every configured marketplace for the concept in the background; poll /sourcing/runs/{id}."""
    from dip.sourcing_intel import run_concept, status
    from dip.storage import business as b

    assert_market(principal, body.market)
    if not any(p["configured"] for p in status()):
        raise HTTPException(409, "no marketplace is configured: set the credentials listed in config/platform/sourcing_intel.yaml "
                                 "(1688 open API, AliExpress affiliate API, or a data API for Alibaba.com / Taobao / Made-in-China)")
    c = _concept(body)
    by = getattr(principal, "email", None) or "local user"
    box: dict = {}
    started = threading.Event()

    def work() -> None:
        try:
            box["result"] = run_concept(c, platforms=body.platforms, by=by, project_id=body.project_id)
        except Exception as exc:  # noqa: BLE001 -- recorded on the run
            box["error"] = str(exc)
        finally:
            started.set()
    threading.Thread(target=work, daemon=True, name="sourcing").start()
    started.wait(timeout=1.5)          # fast runs return complete; slow ones are polled by id
    if "result" in box:
        return clean(box["result"])
    with b.session() as s:
        row = s.query(b.SourcingRun).order_by(b.SourcingRun.started_at.desc()).first()
        return {"id": row.id if row else None, "status": "running"}


@router.get("/sourcing/runs", dependencies=[Depends(require("suppliers", "read"))])
def sourcing_runs(market: str | None = None, limit: int = Query(20, le=200)):
    from dip.sourcing_intel import runs

    return clean(runs(market, limit))


@router.get("/sourcing/runs/{run_id}", dependencies=[Depends(require("suppliers", "read"))])
def sourcing_run_get(run_id: str):
    from dip.storage import business as b

    with b.session() as s:
        r = s.get(b.SourcingRun, run_id)
        if r is None:
            raise HTTPException(404, f"run '{run_id}' not found")
        return clean(b.row_dict(r))


@router.get("/sourcing/runs/{run_id}/offers", dependencies=[Depends(require("suppliers", "read"))])
def sourcing_offers(run_id: str, match_only: bool = False, limit: int = Query(500, le=5000)):
    from dip.sourcing_intel.run import offers_of

    rows = offers_of(run_id)
    if match_only:
        rows = [r for r in rows if r.get("match")]
    return {"run_id": run_id, "total": len(rows), "offers": clean(rows[:limit])}


class ReachIn(BaseModel):
    platform: str
    offer_id: str
    project_id: str | None = None


@router.post("/sourcing/runs/{run_id}/reach-out")
def sourcing_reach_out(run_id: str, body: ReachIn, principal: Principal = Depends(require("suppliers", "write"))):
    """Register the offer's supplier (once) and record an inquiry with the EN/ZH RFQ text."""
    from dip.sourcing_intel import reach_out

    try:
        return clean(reach_out(run_id, body.offer_id, body.platform, by=getattr(principal, "email", None), project_id=body.project_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


class EconomicsIn(BaseModel):
    price: float
    unit_cost: float
    fba_fee: float
    weight_g: float
    qty: int = 500
    ad_share: float = 0.1
    duty_rate: float | None = None
    duty_scenario: str | None = None
    market: str | None = None
    launch_costs: float = 0.0
    units_per_month: float | None = None


@router.post("/economics/unit", dependencies=[Depends(require("markets", "read"))])
def unit_economics(body: EconomicsIn):
    """Per-unit profit, margin, break-even ACoS, max unit cost for the target margin and cash to launch."""
    from dip.sourcing_intel import economics

    try:
        return economics.unit(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/economics/defaults", dependencies=[Depends(require("markets", "read"))])
def economics_defaults():
    from dip.knowledge import landed_cost

    c = landed_cost.config()["landed_cost"]
    return {k: c.get(k) for k in ("referral_fee", "freight_usd_per_kg", "min_billable_kg", "target_margin", "duty_rate",
                                  "duty_scenarios")}

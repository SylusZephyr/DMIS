"""Suppliers, employees, ownership, product-manager portfolio."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from dip.api.util import clean
from dip.auth import Principal, require
from dip.geo import resolve
from dip.pipeline import supplier as supplier_stage
from dip.storage import business as b
from dip.storage import lake

router = APIRouter(tags=["people & suppliers"])


def _same_org(row_org, principal: Principal) -> bool:
    return (row_org or b.DEFAULT_ORG) == principal.org


@router.get("/suppliers")
def suppliers(q: str | None = None, country: str | None = None, principal: Principal = Depends(require("suppliers", "read"))):
    with b.session() as s:
        rows = [b.row_dict(x) for x in s.query(b.Supplier).order_by(b.Supplier.score.desc().nullslast()).all()
                if _same_org(x.org_id, principal)]
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    if q:
        m = df["name"].str.lower().str.contains(q.lower(), na=False) | df["product_categories"].fillna("").str.lower().str.contains(q.lower())
        df = df[m]
    if country:
        df = df[df["country"].fillna("").str.lower() == country.lower()]
    df["geo"] = df["country"].map(resolve)
    return clean(df)


@router.get("/suppliers/{supplier_id}")
def supplier(supplier_id: str, principal: Principal = Depends(require("suppliers", "read"))):
    with b.session() as s:
        sup = s.get(b.Supplier, supplier_id)
        if sup is None or not _same_org(sup.org_id, principal):
            raise HTTPException(404, "supplier not found")
        out = b.row_dict(sup)
    m = lake.read_curated("supplier_matches", None, where="supplier_id = ?", params=[supplier_id], order="match_score DESC")
    out["matches"] = m
    out["geo"] = resolve(out.get("country"))
    return clean(out)


class SupplierIn(BaseModel):
    name: str
    country: str | None = None
    city: str | None = None
    website: str | None = None
    business_type: str | None = None
    oem: bool = False
    odm: bool = False
    certifications: str | None = None
    product_categories: str | None = None
    contact: str | None = None
    notes: str | None = None


@router.post("/suppliers")
def create_supplier(sup: SupplierIn, principal: Principal = Depends(require("suppliers", "write"))):
    n = supplier_stage.import_suppliers(pd.DataFrame([sup.model_dump()]), "platform-ui", principal.org_id)
    return {"saved": n, "note": "re-process a market to refresh its supplier matches and scores"}


@router.post("/suppliers/import")
def import_suppliers(file: UploadFile = File(...), principal: Principal = Depends(require("suppliers", "write"))):
    from dmie.engine.ingestion import read_table

    path = Path(tempfile.mkdtemp(prefix="dip_sup_")) / Path(file.filename or "suppliers.csv").name
    with path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    n = supplier_stage.import_suppliers(read_table(path), file.filename or "upload", principal.org_id)
    return {"imported": n}


def _employee_payload(s, e: b.Employee) -> dict:
    cats = [o.category for o in e.ownership]
    markets = sorted({c.market_name for c in cats if c.market_name})
    from dip.metrics import config as metrics_config

    high = metrics_config()["opportunity"]["high_from"]
    opp_segments, potential, supplier_matches, market_size = 0, [], 0, 0.0
    for m in markets:
        segs = lake.read_curated("segments", m, columns=["segment_id", "segment_label", "opportunity_score", "monthly_revenue"])
        opp_segments += int((segs["opportunity_score"] >= high).sum()) if len(segs) else 0
        market_size += float(segs["monthly_revenue"].sum()) if len(segs) else 0.0
        pr = lake.read_curated("products", m, columns=["product_id", "title", "price", "monthly_sales", "opportunity_score", "image"],
                               where=f"opportunity_score >= {float(high)}", order="opportunity_score DESC", limit=20)
        potential += [{**r, "market": m} for r in pr.to_dict("records")]
        sm = lake.read_curated("supplier_matches", m, where="match_score >= 0.15")
        supplier_matches += len(sm)
    return {"id": e.id, "name": e.name, "title": e.title,
            "categories": [{"id": c.id, "label": c.label, "market": c.market_name, "source_listing_count": c.source_listing_count} for c in cats],
            "markets": markets, "kpis": {"categories": len(cats), "markets": len(markets), "opportunities": opp_segments,
                                         "potential_products": len(potential), "supplier_matches": supplier_matches,
                                         "market_size_monthly": market_size or None},
            "potential_products": sorted(potential, key=lambda r: -(r["opportunity_score"] or 0))[:20]}


@router.get("/employees", dependencies=[Depends(require("people", "read"))])
def employees():
    with b.session() as s:
        out = []
        for e in s.query(b.Employee).all():
            cats = [o.category for o in e.ownership]
            out.append({"id": e.id, "name": e.name, "categories": len(cats),
                        "markets": len({c.market_name for c in cats if c.market_name}),
                        "source_listings": sum(c.source_listing_count or 0 for c in cats)})
    return clean(sorted(out, key=lambda r: (-r["markets"], -r["source_listings"])))


@router.get("/employees/{employee_id}/dashboard", dependencies=[Depends(require("people", "read"))])
def employee_dashboard(employee_id: str):
    with b.session() as s:
        e = s.get(b.Employee, employee_id)
        if e is None:
            raise HTTPException(404, "employee not found")
        return clean(_employee_payload(s, e))


class OwnershipIn(BaseModel):
    employee: str
    category_label: str
    market: str | None = None


@router.post("/ownership", dependencies=[Depends(require("people", "write"))])
def assign(o: OwnershipIn):
    with b.session() as s:
        eid, cid = b.stable_id("emp", o.employee), b.stable_id("cat", o.category_label)
        if s.get(b.Employee, eid) is None:
            s.add(b.Employee(id=eid, name=o.employee))
        cat = s.get(b.Category, cid) or b.Category(id=cid, label=o.category_label)
        if o.market:
            cat.market_name = o.market
        s.merge(cat)
        s.flush()
        if not s.query(b.Ownership).filter_by(employee_id=eid, category_id=cid).first():
            s.add(b.Ownership(employee_id=eid, category_id=cid, basis="assigned in platform"))
    return {"saved": True, "employee_id": eid}


# ------------------------------------------------------------------ sourcing (P3b)
class InteractionIn(BaseModel):
    kind: str                       # inquiry | quote | sample | order | audit | issue
    product: str | None = None
    market_name: str | None = None
    project_id: str | None = None
    unit_price: float | None = None
    currency: str | None = "USD"
    moq: int | None = None
    lead_time_days: int | None = None
    rating: int | None = None       # 1..5
    note: str | None = None


def _own_supplier(supplier_id: str, principal: Principal) -> None:
    with b.session() as s:
        sup = s.get(b.Supplier, supplier_id)
        if sup is None or not _same_org(sup.org_id, principal):
            raise HTTPException(404, "supplier not found")


@router.get("/suppliers/{supplier_id}/interactions")
def supplier_interactions(supplier_id: str, principal: Principal = Depends(require("suppliers", "read"))):
    """Cooperation history: inquiries, quotes, samples, orders, audits, issues."""
    from dip.sourcing import history

    _own_supplier(supplier_id, principal)
    return clean(history(supplier_id))


@router.post("/suppliers/{supplier_id}/interactions")
def add_supplier_interaction(supplier_id: str, body: InteractionIn, principal: Principal = Depends(require("suppliers", "write"))):
    from dip.sourcing import SourcingError, add_interaction

    _own_supplier(supplier_id, principal)
    try:
        return clean(add_interaction(supplier_id, body.model_dump(), principal))
    except SourcingError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sourcing/ranking")
def supplier_ranking(market: str | None = None, segment_id: str | None = None, limit: int = 50,
                     principal: Principal = Depends(require("suppliers", "read"))):
    """Suppliers ranked for a market / segment by fit, cooperation, price, lead time, reliability."""
    from dip.sourcing import ranking

    if market and not principal.may_see_market(market):
        raise HTTPException(403, f"market '{market}' is not assigned to you")
    return clean(ranking(principal.org_id, market, segment_id, limit))

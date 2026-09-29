"""Organizations (tenants), plans and usage -- commercial readiness (Master Prompt 3, P3b)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dip import audit, tenancy
from dip.api.util import clean
from dip.auth import Principal, current_principal, require
from dip.storage import business as b

router = APIRouter(tags=["organizations & plans"])


def _platform_admin(p: Principal = Depends(require("users", "admin"))) -> Principal:
    if p.org != b.DEFAULT_ORG:
        raise HTTPException(403, "platform administration is limited to admins of the default organization")
    return p


@router.get("/org")
def my_org(p: Principal = Depends(current_principal)):
    """Your organization: plan, limits, features, usage this month."""
    return clean(tenancy.summary(p.org_id))


class OrgIn(BaseModel):
    name: str
    plan: str | None = None


class PlanIn(BaseModel):
    plan: str
    status: str | None = None     # active | suspended


@router.get("/orgs")
def orgs(p: Principal = Depends(_platform_admin)):
    with b.session() as s:
        ids = [o.id for o in s.query(b.Organization).all()]
    return clean([tenancy.summary(b.DEFAULT_ORG)] + [tenancy.summary(i) for i in ids if i != b.DEFAULT_ORG])


@router.post("/orgs")
def create_org(body: OrgIn, p: Principal = Depends(_platform_admin)):
    plan = body.plan or tenancy.plans_config()["default_plan"]
    if plan not in tenancy.plans_config()["plans"]:
        raise HTTPException(400, f"unknown plan '{plan}'")
    with b.session() as s:
        if s.query(b.Organization).filter(b.Organization.name == body.name).first():
            raise HTTPException(409, "organization exists")
        o = b.Organization(name=body.name, plan=plan)
        s.add(o)
        s.flush()
        oid = o.id
    audit.record("org.create", p, "orgs", oid, {"name": body.name, "plan": plan})
    return clean(tenancy.summary(oid))


@router.post("/orgs/{org_id}/plan")
def set_plan(org_id: str, body: PlanIn, p: Principal = Depends(_platform_admin)):
    if body.plan not in tenancy.plans_config()["plans"]:
        raise HTTPException(400, f"unknown plan '{body.plan}'")
    with b.session() as s:
        o = s.get(b.Organization, org_id)
        if o is None:
            if org_id != b.DEFAULT_ORG:
                raise HTTPException(404, "organization not found")
            o = b.Organization(id=b.DEFAULT_ORG, name="Default organization")
            s.add(o)
        o.plan = body.plan
        if body.status:
            o.status = body.status
    audit.record("org.plan", p, "orgs", org_id, body.model_dump())
    tenancy.plan_of.cache_clear() if hasattr(tenancy.plan_of, "cache_clear") else None
    return clean(tenancy.summary(org_id))

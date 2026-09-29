"""Operations and collaboration (Master Prompt 3): projects (development pipeline + approvals +
outcomes), comments/notes/evaluations, internal messages, daily summaries, employee profiles,
audit log and the scheduler."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dip import projects as pj
from dip.api.util import clean
from dip.auth import Principal, require
from dip.storage import business as b

router = APIRouter(tags=["operations & collaboration"])


def _call(fn, *a, **kw):
    try:
        return clean(fn(*a, **kw))
    except KeyError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc
    except pj.WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc


# ------------------------------------------------------------------ projects
class ProjectIn(BaseModel):
    title: str
    market: str | None = None
    segment_id: str | None = None
    owner_employee_id: str | None = None
    idea: dict = {}           # {title, price, specs, unit_cost}


class StageIn(BaseModel):
    stage: str
    note: str | None = None


class TextIn(BaseModel):
    text: str | None = None


class TrackIn(BaseModel):
    listing_ids: list[str]


@router.get("/projects")
def list_projects(market: str | None = None, stage: str | None = None, status: str | None = None,
                  owner_employee_id: str | None = None, principal: Principal = Depends(require("projects", "read"))):
    scope = principal.market_scope()
    with b.session() as s:
        q = s.query(b.Project).order_by(b.Project.updated_at.desc())
        for col, v in ((b.Project.market_name, market), (b.Project.stage, stage), (b.Project.status, status),
                       (b.Project.owner_employee_id, owner_employee_id)):
            if v:
                q = q.filter(col == v)
        rows = [b.row_dict(p) for p in q.limit(1000).all()]
    if scope is not None:
        rows = [r for r in rows if r["market_name"] in scope]
    return clean({"stages": pj.cfg()["stages"], "projects": rows})


@router.post("/projects")
def create_project(body: ProjectIn, principal: Principal = Depends(require("projects", "write"))):
    if body.market and not principal.may_see_market(body.market):
        raise HTTPException(403, f"market '{body.market}' is not assigned to you")
    from dip import tenancy
    if not tenancy.has_feature(principal.org_id, "projects"):
        raise HTTPException(402, "your plan does not include the product pipeline (projects)")
    idea = {**body.idea, "title": body.idea.get("title") or body.title}
    return _call(pj.create, body.title, body.market, body.segment_id, idea, body.owner_employee_id, principal)


def _scoped(project_id: str, principal: Principal) -> None:
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if p is None:
            raise HTTPException(404, "project not found")
        if not principal.may_see_market(p.market_name):
            raise HTTPException(403, "this project's market is not assigned to you")


@router.get("/projects/{project_id}")
def project(project_id: str, principal: Principal = Depends(require("projects", "read"))):
    _scoped(project_id, principal)
    return _call(pj.get, project_id)


@router.post("/projects/{project_id}/stage")
def project_stage(project_id: str, body: StageIn, principal: Principal = Depends(require("projects", "write"))):
    _scoped(project_id, principal)
    return _call(pj.move, project_id, body.stage, principal, body.note)


@router.post("/projects/{project_id}/recommend")
def project_recommend(project_id: str, body: TextIn, principal: Principal = Depends(require("projects", "write"))):
    _scoped(project_id, principal)
    return _call(pj.recommend, project_id, principal, body.text)


@router.post("/projects/{project_id}/approve")
def project_approve(project_id: str, body: TextIn, principal: Principal = Depends(require("projects", "admin"))):
    return _call(pj.decide, project_id, True, principal, body.text)


@router.post("/projects/{project_id}/reject")
def project_reject(project_id: str, body: TextIn, principal: Principal = Depends(require("projects", "admin"))):
    return _call(pj.decide, project_id, False, principal, body.text)


@router.post("/projects/{project_id}/track")
def project_track(project_id: str, body: TrackIn, principal: Principal = Depends(require("projects", "write"))):
    _scoped(project_id, principal)
    return _call(pj.track, project_id, body.listing_ids, principal)


class SuppliersIn(BaseModel):
    supplier_ids: list[str]


@router.post("/projects/{project_id}/suppliers")
def project_suppliers(project_id: str, body: SuppliersIn, principal: Principal = Depends(require("projects", "write"))):
    _scoped(project_id, principal)
    return _call(pj.link_suppliers, project_id, body.supplier_ids, principal)


@router.get("/projects/{project_id}/outcome")
def project_outcome(project_id: str, principal: Principal = Depends(require("projects", "read"))):
    _scoped(project_id, principal)
    return _call(pj.outcome, project_id)


@router.get("/calibration", dependencies=[Depends(require("projects", "read"))])
def calibration():
    """Measured launch outcomes versus the predictions they were approved on."""
    return clean(pj.calibration())


# ------------------------------------------------------------------ comments
class CommentIn(BaseModel):
    target_kind: str          # product | segment | market | supplier | project
    target_id: str
    text: str
    kind: str = "comment"     # comment | note | evaluation
    rating: int | None = None
    market: str | None = None


@router.get("/comments", dependencies=[Depends(require("markets", "read"))])
def comments(target_kind: str, target_id: str):
    with b.session() as s:
        rows = s.query(b.Comment).filter_by(target_kind=target_kind, target_id=target_id).order_by(b.Comment.created_at.asc()).all()
        return clean([b.row_dict(c) for c in rows])


@router.post("/comments")
def add_comment(body: CommentIn, principal: Principal = Depends(require("markets", "read"))):
    if body.market and not principal.may_see_market(body.market):
        raise HTTPException(403, f"market '{body.market}' is not assigned to you")
    if not body.text.strip():
        raise HTTPException(400, "text is empty")
    return _call(pj.comment, body.target_kind, body.target_id, body.text, principal, body.kind, body.rating, body.market)


# ------------------------------------------------------------------ messages, summaries, profiles
def _own_employee(principal: Principal, employee_id: str | None) -> str:
    if principal.user_id:
        own = principal.employee_id
        if employee_id and employee_id != own and not principal.can("people", "write"):
            raise HTTPException(403, "you can only read your own messages")
        employee_id = employee_id or own
    if not employee_id:
        raise HTTPException(400, "employee_id is required (or link your user to an employee)")
    return employee_id


@router.get("/messages")
def messages(employee_id: str | None = None, unread: bool = False, limit: int = Query(100, le=1000),
             principal: Principal = Depends(require("people", "read"))):
    emp = _own_employee(principal, employee_id)
    with b.session() as s:
        q = s.query(b.Message).filter(b.Message.to_employee_id == emp).order_by(b.Message.created_at.desc())
        if unread:
            q = q.filter(b.Message.read.is_(False))
        return clean([b.row_dict(m) for m in q.limit(limit).all()])


@router.post("/messages/{message_id}/read")
def read_message(message_id: str, principal: Principal = Depends(require("people", "read"))):
    with b.session() as s:
        m = s.get(b.Message, message_id)
        if m is None:
            raise HTTPException(404, "message not found")
        _own_employee(principal, m.to_employee_id)
        m.read = True
    return {"saved": True}


@router.get("/employees/{employee_id}/summary")
def summary(employee_id: str, hours: int = Query(24, ge=1, le=24 * 31),
            principal: Principal = Depends(require("people", "read"))):
    """Daily intelligence summary: what changed in the employee's markets, as sentences."""
    from dip.operations import daily_summary

    out = daily_summary(employee_id, hours)
    if out is None:
        raise HTTPException(404, "employee not found")
    return clean(out)


class ProfileIn(BaseModel):
    title: str | None = None
    department: str | None = None
    responsibilities: str | None = None
    email: str | None = None


@router.get("/employees/{employee_id}/profile", dependencies=[Depends(require("people", "read"))])
def profile(employee_id: str, activity_limit: int = Query(50, le=500)):
    """Name, department, responsibilities, assigned categories and activity history."""
    with b.session() as s:
        e = s.get(b.Employee, employee_id)
        if e is None:
            raise HTTPException(404, "employee not found")
        out = {k: getattr(e, k) for k in ("id", "name", "title", "department", "responsibilities", "email")}
        out["categories"] = [{"label": o.category.label, "market": o.category.market_name} for o in e.ownership]
        users = [u for u in s.query(b.User).filter(b.User.employee_id == employee_id).all()]
        out["users"] = [{"email": u.email, "role": u.role} for u in users]
        names = [u.email for u in users] + [e.name]
        acts = (s.query(b.AuditLog).filter(b.AuditLog.user.in_(names)).order_by(b.AuditLog.at.desc()).limit(activity_limit).all())
        out["activity"] = [b.row_dict(a) for a in acts]
        out["projects"] = [b.row_dict(p) for p in s.query(b.Project).filter(b.Project.owner_employee_id == employee_id).all()]
    return clean(out)


@router.post("/employees/{employee_id}/profile", dependencies=[Depends(require("people", "write"))])
def update_profile(employee_id: str, body: ProfileIn):
    with b.session() as s:
        e = s.get(b.Employee, employee_id)
        if e is None:
            raise HTTPException(404, "employee not found")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(e, k, v)
    return {"saved": True}


# ------------------------------------------------------------------ audit, scheduler
@router.get("/audit", dependencies=[Depends(require("audit", "read"))])
def audit_log(user: str | None = None, action: str | None = None, resource: str | None = None,
              limit: int = Query(200, le=5000)):
    with b.session() as s:
        q = s.query(b.AuditLog).order_by(b.AuditLog.at.desc())
        if user:
            q = q.filter(b.AuditLog.user == user)
        if action:
            q = q.filter(b.AuditLog.action.like(f"%{action}%"))
        if resource:
            q = q.filter(b.AuditLog.resource == resource)
        return clean([b.row_dict(a) for a in q.limit(limit).all()])


@router.post("/scheduler/run", dependencies=[Depends(require("datasets", "write"))])
def scheduler_run(send_summaries: bool = False):
    """One scheduler tick now: process the inbox, poll configured connectors, optionally send summaries."""
    from dip.operations import run_once

    return clean(run_once(send_summaries=send_summaries or None))

"""Pilot (Master Prompt 4): accuracy labelling, "this looks wrong" feedback, page-view usage."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from dip import audit
from dip.api.util import clean
from dip.auth import Principal, current_principal, require
from dip.pilot import labels as L
from dip.pilot import metrics as M
from dip.storage import business as b

router = APIRouter(tags=["pilot"])


def _sample_for(sample_id: str, p: Principal) -> dict:
    try:
        smp = L.sample_summary(sample_id)
    except KeyError:
        raise HTTPException(404, "sample not found") from None
    if not p.may_see_market(smp["market_name"]):
        raise HTTPException(403, f"market '{smp['market_name']}' is not assigned to you")
    return smp


# ------------------------------------------------------------------ labelling
class SampleIn(BaseModel):
    market: str
    n: int = 50
    n_excluded: int = 20
    seed: int = 7
    name: str | None = None


@router.get("/labels/samples")
def samples(market: str | None = None, p: Principal = Depends(require("labels", "read"))):
    return clean([x for x in L.list_samples(market) if p.may_see_market(x["market_name"])])


@router.post("/labels/samples")
def new_sample(body: SampleIn, p: Principal = Depends(require("labels", "write"))):
    if not p.may_see_market(body.market):
        raise HTTPException(403, f"market '{body.market}' is not assigned to you")
    if not 5 <= body.n <= 500 or not 0 <= body.n_excluded <= 200:
        raise HTTPException(409, "n must be 5..500 and n_excluded 0..200")
    try:
        out = L.draw_sample(body.market, body.n, body.n_excluded, body.seed, body.name, p.email)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    audit.record("labels.sample", p, "labels", out["id"], body.model_dump())
    return clean(out)


@router.get("/labels/samples/{sample_id}/items")
def sample_items(sample_id: str, p: Principal = Depends(require("labels", "read"))):
    _sample_for(sample_id, p)
    return clean(L.items(sample_id))


class LabelIn(BaseModel):
    check: str
    value: dict | None = None
    correct: bool | None = None
    notes: str | None = None


@router.post("/labels/items/{item_id}")
def label_item(item_id: str, body: LabelIn, p: Principal = Depends(require("labels", "write"))):
    with b.session() as s:
        it = s.get(b.LabelItem, item_id)
        if it is None:
            raise HTTPException(404, "item not found")
        sid = it.sample_id
    _sample_for(sid, p)
    try:
        return clean(L.save_label(item_id, body.check, body.value, body.correct, body.notes, p.email or "local user"))
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/labels/samples/{sample_id}/metrics")
def sample_metrics(sample_id: str, against: str = "snapshot", p: Principal = Depends(require("labels", "read"))):
    _sample_for(sample_id, p)
    try:
        return clean(M.evaluate(sample_id, against))
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/labels/samples/{sample_id}/sheet", response_class=PlainTextResponse)
def sheet(sample_id: str, p: Principal = Depends(require("labels", "read"))):
    _sample_for(sample_id, p)
    return L.export_sheet(sample_id).to_csv(index=False)


@router.post("/labels/samples/{sample_id}/sheet")
def import_sheet(sample_id: str, file: UploadFile = File(...), p: Principal = Depends(require("labels", "write"))):
    _sample_for(sample_id, p)
    try:
        n = L.import_sheet(sample_id, pd.read_csv(io.BytesIO(file.file.read()), dtype=str), p.email or "local user")
    except (ValueError, KeyError) as e:
        raise HTTPException(409, str(e)) from None
    return {"labels_saved": n}


# ------------------------------------------------------------------ feedback
class FeedbackIn(BaseModel):
    target_kind: str                  # product | segment | market | alert | page
    target_id: str | None = None
    market: str | None = None
    field: str | None = None
    shown_value: str | None = None
    comment: str
    page: str | None = None


TRIAGE = ("data_bug", "rule_fix", "ui_confusion", "feature_request", "not_a_bug")
STATUSES = ("new", "triaged", "fixed", "closed")


@router.post("/feedback")
def send_feedback(body: FeedbackIn, p: Principal = Depends(require("markets", "read"))):
    if not body.comment.strip():
        raise HTTPException(409, "comment is required")
    if body.market and not p.may_see_market(body.market):
        raise HTTPException(403, f"market '{body.market}' is not assigned to you")
    with b.session() as s:
        row = b.UserFeedback(target_kind=body.target_kind, target_id=body.target_id, market_name=body.market,
                             field=body.field, shown_value=body.shown_value, comment=body.comment.strip(), page=body.page,
                             user=p.email or "local user", org_id=p.org_id)
        s.add(row)
        s.flush()
        return clean(b.row_dict(row))


@router.get("/feedback")
def list_feedback(status: str | None = None, p: Principal = Depends(require("feedback", "write"))):
    with b.session() as s:
        q = s.query(b.UserFeedback).order_by(b.UserFeedback.created_at.desc())
        if status:
            q = q.filter(b.UserFeedback.status == status)
        rows = [b.row_dict(x) for x in q.all() if (x.org_id or b.DEFAULT_ORG) == p.org]
    return clean(rows)


class TriageIn(BaseModel):
    status: str | None = None
    triage: str | None = None
    resolution: str | None = None


@router.post("/feedback/{feedback_id}")
def triage_feedback(feedback_id: str, body: TriageIn, p: Principal = Depends(require("feedback", "write"))):
    if body.triage and body.triage not in TRIAGE:
        raise HTTPException(409, f"triage must be one of {TRIAGE}")
    if body.status and body.status not in STATUSES:
        raise HTTPException(409, f"status must be one of {STATUSES}")
    with b.session() as s:
        row = s.get(b.UserFeedback, feedback_id)
        if row is None or (row.org_id or b.DEFAULT_ORG) != p.org:
            raise HTTPException(404, "feedback not found")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(row, k, v)
        if body.triage and row.status == "new" and not body.status:
            row.status = "triaged"
        out = b.row_dict(row)
    audit.record("feedback.triage", p, "feedback", feedback_id, body.model_dump(exclude_none=True))
    return clean(out)


# ------------------------------------------------------------------ usage
class ViewIn(BaseModel):
    path: str


@router.post("/telemetry/view")
def page_view(body: ViewIn, p: Principal = Depends(current_principal)):
    path = body.path.split("?", 1)[0][:512]           # path only, never query strings or content
    with b.session() as s:
        s.add(b.PageView(path=path, user=p.email or "local user", role=p.role, org_id=p.org_id))
    return {"ok": True}


@router.get("/usage/pages")
def page_usage(days: int = Query(28, ge=1, le=365), p: Principal = Depends(require("feedback", "write"))):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with b.session() as s:
        rows = [(x.path, x.user, x.role, x.at) for x in s.query(b.PageView).filter(b.PageView.at >= since).all()
                if (x.org_id or b.DEFAULT_ORG) == p.org]
    if not rows:
        return {"days": days, "views": 0, "pages": [], "users": []}
    df = pd.DataFrame(rows, columns=["path", "user", "role", "at"])
    df["page"] = df["path"].str.replace(r"/[^/]*\d[^/]*", "/:id", regex=True)   # collapse ids
    pages = df.groupby("page").agg(views=("path", "size"), users=("user", "nunique")).sort_values("views", ascending=False)
    users = df.groupby(["user", "role"]).agg(views=("path", "size"), pages=("page", "nunique"),
                                             last=("at", "max")).reset_index().sort_values("views", ascending=False)
    return clean({"days": days, "views": len(df), "pages": pages.reset_index().to_dict("records"),
                  "users": users.to_dict("records")})


# ---------------------------------------------------------------- accuracy dashboard (Phase 8)
@router.get("/accuracy")
def accuracy(p: Principal = Depends(require("markets", "read"))):
    """Measured accuracy: synthetic validation, per-market hold-out, label precision, launch calibration."""
    from dip.auth import visible_markets
    from dip.pilot import accuracy as A

    with b.session() as s:
        names = [m.name for m in s.query(b.Market).all()]
    return clean(A.dashboard(None if p.market_scope() is None else visible_markets(p, names)))


@router.post("/accuracy/synthetic")
def accuracy_run(markets: int | None = Query(None, ge=2, le=40), p: Principal = Depends(require("datasets", "write"))):
    """Start a synthetic validation run (background); poll GET /accuracy."""
    from dip.pilot import accuracy as A

    started = A.start_synthetic(markets)
    audit.record("accuracy.synthetic", p, "accuracy", None, {"markets": markets})
    return {"started": started, "note": None if started else "a validation run is already going"}

"""Product development pipeline: every opportunity can become a project.

    opportunity -> evaluation -> supplier_search -> prototype -> launch_decision -> launched

* A project keeps the launch evaluation made when it was created or re-evaluated
  (``prediction``) -- the numbers the decision was based on.
* ``recommend`` (product manager) puts it in ``pending_approval``; ``approve`` / ``reject``
  need ``projects:admin`` (manager, admin). Entering the approval stage in config
  (``launched`` by default) requires an approved project.
* Every step is a ``ProjectEvent`` (decision history) and an audit-log entry.
* Once launched, the project tracks its listing ids (ASINs). Each new upload of the market
  records the listings' actual sales, revenue and rating as an ``outcome`` event, and
  ``outcome`` compares them with the prediction at 3 / 6 / 12 months.
Stages and checkpoints: config/platform/operations.yaml -> projects.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dip import audit
from dip.events import ops_config
from dip.storage import business as b
from dip.storage import lake


class WorkflowError(ValueError):
    pass


def cfg() -> dict:
    return ops_config()["projects"]


def _who(principal) -> str | None:
    return getattr(principal, "email", None) or getattr(principal, "name", None)


def _event(s, project_id: str, kind: str, by, text=None, frm=None, to=None, data=None) -> None:
    s.add(b.ProjectEvent(project_id=project_id, kind=kind, by=by, text=text, from_stage=frm, to_stage=to, data=data))


def validate_idea(idea: dict) -> dict:
    """Reject inputs the models cannot use honestly (no silent defaults, no invented numbers)."""
    out = dict(idea or {})
    for k in ("price", "unit_cost", "fulfilment_fee", "fixed_monthly_cost", "launch_cost"):
        if out.get(k) in ("", None):
            out.pop(k, None)
            continue
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            raise WorkflowError(f"{k} must be a number") from None
        if out[k] < 0 or (k == "price" and out[k] <= 0):
            raise WorkflowError(f"{k} must be {'positive' if k == 'price' else 'zero or more'}")
    if "unit_cost" in out and "price" in out and out["unit_cost"] >= out["price"]:
        out["warnings"] = [f"unit cost {out['unit_cost']:g} is not below the price {out['price']:g}: every sale loses money"]
    return out


def _prediction(idea: dict, market: str | None, segment_id: str | None) -> dict | None:
    """Launch simulation on the market's fitted demand model (metrics v3). ``market_attractiveness`` is the
    placed segment's opportunity index (0-100). Needs a title and a price; a unit cost only if given."""
    if not idea or not idea.get("title") or not idea.get("price"):
        return None
    from dip.intelligence.launch import choose_market
    from dip.metrics import launch as launch_mod

    if not market:
        market, _ = choose_market(f"{idea['title']} {idea.get('specs', '')}")
    if not market:
        return None
    data = launch_mod.load(market)
    if data is None:
        return None
    with b.session() as s:
        m = s.get(b.Market, market)
        summary = ((m.summary or {}) if m else {}).get("metrics_v3") or {}
    r = launch_mod.strip(launch_mod.simulate({k: v for k, v in idea.items() if k != "warnings"}, data, market, summary, segment_id))
    u, rv, seg = r["units"], r["revenue"], r["segment"]
    return {"model": "metrics-v3 launch simulation", "market": market, "segment_id": r["placement"]["segment_id"],
            "segment": r["placement"]["segment_label"], "market_attractiveness": seg.get("opportunity_index"),
            "attractiveness_basis": "opportunity index of the segment the idea is placed in",
            "verdict": seg.get("opportunity_level"), "expected_positioning": None,
            "main_risk": ({"risk": r["risks"][0]["code"], "evidence": str(r["risks"][0].get("value"))} if r["risks"] else None),
            "risks": r["risks"], "economics": r["economics"], "gaps": r["gaps"],
            "units_p10_p50_p90": [u["p10"], u["median"], u["p90"]], "revenue_p10_p50_p90": [rv["p10"], rv["median"], rv["p90"]],
            "profit": r.get("profit"), "probability_profitable": (r.get("profit") or {}).get("p_positive"),
            "break_even_units": r.get("break_even_units"), "entrants_actual": r["entrants_actual"],
            "warnings": idea.get("warnings", []), "evaluated_at": pd.Timestamp.now(tz="UTC").isoformat()}


def create(title: str, market: str | None, segment_id: str | None, idea: dict | None, owner_employee_id: str | None,
           principal=None) -> dict:
    title = (title or "").strip()
    if len(title) < 3:
        raise WorkflowError("a project needs a title of at least 3 characters")
    idea = validate_idea(idea or {})
    if market:
        with b.session() as s:
            if s.get(b.Market, market) is None:
                raise WorkflowError(f"unknown market '{market}'")
    pred = _prediction(idea, market, segment_id)
    with b.session() as s:
        p = b.Project(title=title[:512], market_name=market or (pred or {}).get("market"),
                      segment_id=segment_id or (pred or {}).get("segment_id"), idea=idea or {}, prediction=pred,
                      owner_employee_id=owner_employee_id, created_by=_who(principal), stage=cfg()["stages"][0])
        s.add(p)
        s.flush()
        _event(s, p.id, "created", _who(principal), f"project created at stage {p.stage}", to=p.stage,
               data={"prediction": bool(pred)})
        pid = p.id
    audit.record("project.create", principal, "projects", pid, {"title": title})
    sync_graph(pid)
    return get(pid)


def get(project_id: str) -> dict:
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if p is None:
            raise KeyError(project_id)
        out = b.row_dict(p)
        out["history"] = [b.row_dict(e) for e in s.query(b.ProjectEvent).filter_by(project_id=project_id)
                          .order_by(b.ProjectEvent.created_at.asc()).all()]
        out["comments"] = [b.row_dict(c) for c in s.query(b.Comment).filter_by(target_kind="project", target_id=project_id)
                           .order_by(b.Comment.created_at.asc()).all()]
        owner = s.get(b.Employee, p.owner_employee_id) if p.owner_employee_id else None
        out["owner"] = owner.name if owner else None
    return out


def move(project_id: str, to_stage: str, principal=None, note: str | None = None) -> dict:
    stages = cfg()["stages"]
    if to_stage not in stages:
        raise WorkflowError(f"unknown stage '{to_stage}'; stages: {stages}")
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if p is None:
            raise KeyError(project_id)
        if p.status in ("rejected", "closed"):
            raise WorkflowError(f"project is {p.status}")
        gate = cfg()["approval_required_to_enter"]
        if to_stage == gate and p.status != "approved":
            raise WorkflowError(f"entering '{gate}' needs an approved project (status is {p.status}); recommend it first")
        frm = p.stage
        p.stage = to_stage
        _event(s, p.id, "stage", _who(principal), note, frm, to_stage)
    audit.record("project.stage", principal, "projects", project_id, {"from": frm, "to": to_stage})
    sync_graph(project_id)
    return get(project_id)


def recommend(project_id: str, principal=None, text: str | None = None, reevaluate: bool = True) -> dict:
    """Product manager recommends the project for approval; the prediction is refreshed so the
    decision is made on current numbers, and approvers are notified."""
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if p is None:
            raise KeyError(project_id)
        if p.status not in ("active", "rejected"):
            raise WorkflowError(f"cannot recommend a project that is {p.status}")
        idea, market, seg = dict(p.idea or {}), p.market_name, p.segment_id
    pred = _prediction(idea, market, seg) if reevaluate else None
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if pred:
            p.prediction = pred
        p.status = "pending_approval"
        _event(s, p.id, "recommend", _who(principal), text, p.stage, p.stage,
               {"market_attractiveness": (pred or p.prediction or {}).get("market_attractiveness"),
                "verdict": (pred or p.prediction or {}).get("verdict")})
        title = p.title
    audit.record("project.recommend", principal, "projects", project_id, {"text": text})
    _notify_approvers(project_id, title, text, _who(principal))
    return get(project_id)


def _notify_approvers(project_id: str, title: str, text: str | None, by: str | None) -> None:
    from dip.operations import notify

    with b.session() as s:
        emps = [u.employee_id for u in s.query(b.User).filter(b.User.role.in_(("manager", "admin"))).all() if u.employee_id]
    for e in set(emps):
        notify(e, f"[DMIS] Approval requested: {title}"[:200],
               f"{by or 'A product manager'} recommends: {title}\n\n{text or ''}\n\nProject {project_id}", kind="approval")


def decide(project_id: str, approve: bool, principal=None, text: str | None = None) -> dict:
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if p is None:
            raise KeyError(project_id)
        if p.status != "pending_approval":
            raise WorkflowError(f"project is {p.status}, not pending approval")
        p.status = "approved" if approve else "rejected"
        _event(s, p.id, "approve" if approve else "reject", _who(principal), text, p.stage, p.stage,
               {"prediction_at_decision": p.prediction})
        owner, title = p.owner_employee_id, p.title
    audit.record("project.approve" if approve else "project.reject", principal, "projects", project_id, {"text": text})
    if owner:
        from dip.operations import notify

        notify(owner, f"[DMIS] {'Approved' if approve else 'Rejected'}: {title}"[:200], text or "", kind="approval")
    return get(project_id)


_LISTING_ID = __import__("re").compile(r"^[A-Z0-9]{8,14}$")


def track(project_id: str, listing_ids: list[str], principal=None) -> dict:
    ids = sorted({str(x).strip().upper() for x in listing_ids if str(x).strip()})
    bad = [i for i in ids if not _LISTING_ID.match(i)]
    if not ids or bad:
        raise WorkflowError(f"listing ids must be 8-14 letters/digits (e.g. an ASIN); invalid: {', '.join(bad) or 'none given'}")
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if p is None:
            raise KeyError(project_id)
        p.tracked_listings = ids
        _event(s, p.id, "note", _who(principal), f"tracking listings {', '.join(ids)}", data={"listings": ids})
    audit.record("project.track", principal, "projects", project_id, {"listings": ids})
    return get(project_id)


def link_suppliers(project_id: str, supplier_ids: list[str], principal=None) -> dict:
    with b.session() as s:
        p = s.get(b.Project, project_id)
        if p is None:
            raise KeyError(project_id)
        known = [i for i in supplier_ids if s.get(b.Supplier, i) is not None]
        p.supplier_ids = sorted(set((p.supplier_ids or []) + known))
        _event(s, p.id, "note", _who(principal), f"suppliers linked: {', '.join(known)}", data={"suppliers": known})
    audit.record("project.suppliers", principal, "projects", project_id, {"suppliers": supplier_ids})
    sync_graph(project_id)
    return get(project_id)


def comment(target_kind: str, target_id: str, text: str, principal=None, kind: str = "comment",
            rating: int | None = None, market: str | None = None) -> dict:
    if kind not in ("comment", "note", "evaluation"):
        raise WorkflowError("kind must be comment, note or evaluation")
    if rating is not None and not 1 <= rating <= 5:
        raise WorkflowError("rating must be 1..5")
    with b.session() as s:
        c = b.Comment(target_kind=target_kind, target_id=target_id, text=text, kind=kind, rating=rating,
                      market_name=market, author=_who(principal) or "local user")
        s.add(c)
        s.flush()
        out = b.row_dict(c)
    audit.record("comment.add", principal, target_kind, target_id, {"kind": kind})
    return out


# ------------------------------------------------------------------ outcomes
def _launch_time(project_id: str) -> pd.Timestamp | None:
    with b.session() as s:
        ev = (s.query(b.ProjectEvent).filter_by(project_id=project_id, kind="stage", to_stage="launched")
              .order_by(b.ProjectEvent.created_at.asc()).first())
    if ev is None:
        return None
    ts = pd.Timestamp(ev.created_at)
    return (ts.tz_convert(None) if ts.tzinfo else ts).normalize()


def _market_actuals(market: str, listing_ids: list[str]) -> pd.DataFrame:
    """Observed sales / revenue / rating per period for the given listings, from every upload of the market."""
    from dip.intelligence.history import dataset_period, prior_datasets

    empty = pd.DataFrame(columns=["period", "sales", "revenue", "rating", "listings"])
    ds = prior_datasets(market)
    if not ds:
        return empty
    ids_sql = ", ".join("'" + i.replace("'", "") + "'" for i in listing_ids)
    rec = lake.read_std([d.id for d in ds], ["id", "timestamp", "sales", "revenue", "price", "rating"],
                        where=f"upper(id) IN ({ids_sql})")
    if rec.empty:
        return empty
    per = {d.id: dataset_period(d) for d in ds}
    rec["period"] = pd.to_datetime(rec["timestamp"]).fillna(rec["dataset_id"].map(per)).dt.normalize()
    rec["revenue"] = rec["revenue"].where(rec["revenue"].notna(), rec["price"] * rec["sales"])
    g = rec.drop_duplicates(["id", "period"]).groupby("period")
    return pd.DataFrame({"sales": g["sales"].sum(min_count=1), "revenue": g["revenue"].sum(min_count=1),
                         "rating": g["rating"].mean(), "listings": g["id"].nunique()}).reset_index()


def actuals(market: str, listing_ids: list[str]) -> pd.DataFrame:
    """Actual sales / revenue per period for the given listings. Your own Seller Central sales (src/dip/own_sales.py,
    exact, per month) are used where they exist; market uploads (estimates or sales badges) fill the other months.
    ``source`` says which one each period comes from."""
    from dip import own_sales

    cols = ["period", "sales", "revenue", "rating", "listings", "source"]
    if not listing_ids:
        return pd.DataFrame(columns=cols)
    mk = _market_actuals(market, listing_ids).assign(source="market")
    own = own_sales.monthly(listing_ids)
    if own.empty:
        return mk.reindex(columns=cols)
    own = own.assign(period=pd.to_datetime(own["period"]).dt.normalize(), rating=np.nan, source="own_sales")
    months = set(own["period"].dt.to_period("M"))
    if len(mk):
        mk = mk[~pd.to_datetime(mk["period"]).dt.to_period("M").isin(months)]
    frames = [f for f in (mk, own) if len(f)]
    return pd.concat(frames, ignore_index=True).reindex(columns=cols).sort_values("period").reset_index(drop=True)


def _actual_interval(market: str, sales: float | None) -> tuple[float | None, float | None]:
    """What an observed sales value means: exact, or a badge interval [v, next rung)."""
    if sales is None:
        return None, None
    from dip.metrics.observation import observe
    with b.session() as s:
        m = s.get(b.Market, market)
        kind = ((((m.summary or {}) if m else {}).get("metrics_v3") or {}).get("sales_observation") or {}).get("kind")
    if kind != "badge":
        return sales, sales
    o = observe(pd.Series([sales]), "badge")
    hi = float(o.upper[0])
    return float(o.lower[0]), (None if not np.isfinite(hi) else hi)


def outcome(project_id: str) -> dict:
    """Actual performance of a launched project against the prediction it was approved on."""
    p = get(project_id)
    ids = p.get("tracked_listings") or []
    if not ids or not p.get("market_name"):
        return {"project_id": project_id, "status": "not_tracking", "note": "add the launched listing ids (ASINs) to track outcomes"}
    act = actuals(p["market_name"], ids)
    if act.empty:
        return {"project_id": project_id, "status": "no_observations",
                "note": "the tracked listings have not appeared in any upload of this market yet"}
    pred = p.get("prediction") or {}
    units = pred.get("units_p10_p50_p90") or [None, None, None]
    rev = pred.get("revenue_p10_p50_p90") or [None, None, None]
    launched = _launch_time(project_id) or act["period"].min()
    checks = []
    for months in cfg()["outcome_checkpoints_months"]:
        due = launched + pd.DateOffset(months=months)
        seen = act[act["period"] <= due]
        if seen.empty or act["period"].max() < due:
            checks.append({"months": months, "status": "pending", "due": str(due.date())})
            continue
        row = seen.iloc[-1]
        a_units = None if pd.isna(row["sales"]) else float(row["sales"])
        exact = row.get("source") == "own_sales"                  # your own sales are exact, not a badge interval
        a_lo, a_hi = (a_units, a_units) if exact else _actual_interval(p["market_name"], a_units)
        checks.append({"months": months, "status": "measured", "period": str(pd.Timestamp(row["period"]).date()),
                       "actual_units": a_units, "actual_revenue": None if pd.isna(row["revenue"]) else float(row["revenue"]),
                       "actual_source": row.get("source") or "market",
                       "predicted_units_p50": units[1], "predicted_range": [units[0], units[2]],
                       "actual_range": [a_lo, a_hi],
                       # badge data: the actual is an interval [badge, next rung); it agrees when it overlaps p10-p90
                       "within_p10_p90": (a_units is not None and units[0] is not None
                                          and a_lo <= units[2] and (a_hi is None or a_hi >= units[0])),
                       "ratio_to_p50": (round(a_units / units[1], 3) if a_units is not None and units[1] else None)})
    return {"project_id": project_id, "status": "ok", "launched": str(launched.date()), "prediction": pred,
            "observations": act.assign(period=act["period"].astype(str)).to_dict("records"), "checkpoints": checks,
            "predicted_revenue_p50": rev[1]}


def record_outcomes(market: str, dataset_id: str | None) -> int:
    """After a market upload: every launched project tracking listings in it gets an outcome event."""
    from dip import events

    with b.session() as s:
        ps = [(p.id, p.title, p.tracked_listings, p.owner_employee_id) for p in
              s.query(b.Project).filter(b.Project.market_name == market, b.Project.stage == "launched").all()]
    n = 0
    for pid, title, ids, owner in ps:
        if not ids:
            continue
        act = actuals(market, ids)
        if act.empty:
            continue
        last = act.sort_values("period").iloc[-1]
        data = {"period": str(pd.Timestamp(last["period"]).date()), "sales": None if pd.isna(last["sales"]) else float(last["sales"]),
                "revenue": None if pd.isna(last["revenue"]) else float(last["revenue"]), "dataset_id": dataset_id}
        with b.session() as s:
            _event(s, pid, "outcome", "system", f"observed {data['sales']} units in {data['period']}", data=data)
        events.publish("project.outcome", market, f"{title}: {data['sales']} units ({data['period']})", {"project_id": pid, **data},
                       "notice", "pipeline")
        n += 1
    return n


def calibration() -> dict:
    """How accurate were past launch predictions? Every measured checkpoint of every project."""
    with b.session() as s:
        ids = [p.id for p in s.query(b.Project).filter(b.Project.stage == "launched").all()]
    rows = []
    for pid in ids:
        o = outcome(pid)
        for c in o.get("checkpoints", []):
            if c.get("status") == "measured":
                rows.append({"project_id": pid, **c})
    measured = [r for r in rows if r.get("ratio_to_p50") is not None]
    return {"projects": len(ids), "measured_checkpoints": len(measured),
            "within_p10_p90_share": (round(sum(1 for r in measured if r["within_p10_p90"]) / len(measured), 3) if measured else None),
            "median_ratio_to_p50": (float(pd.Series([r["ratio_to_p50"] for r in measured]).median()) if measured else None),
            "rows": rows,
            "note": "weights are not changed automatically; use these numbers to review config/platform/launch.yaml"}


# ------------------------------------------------------------------ knowledge graph
def project_graph(market: str) -> tuple[list[dict], list[dict]]:
    """Project nodes of a market with Segment -HAS_PROJECT-> Project and Project -SOURCED_FROM-> Supplier."""
    with b.session() as s:
        ps = s.query(b.Project).filter(b.Project.market_name == market).all()
        rows = [(p.id, p.title, p.stage, p.status, p.segment_id, p.supplier_ids or []) for p in ps]
    nodes, edges = [], []
    for pid, title, stage, status, seg, sups in rows:
        nid = f"project:{pid}"
        nodes.append({"id": nid, "kind": "Project", "label": title[:120], "props": {"stage": stage, "status": status}})
        if seg:
            edges.append({"source": f"segment:{market}/{seg}", "target": nid, "rel": "HAS_PROJECT",
                          "props": {"evidence": "product project created for this segment", "basis": "fact", "strength": 1.0}})
        for sid in sups:
            edges.append({"source": nid, "target": f"supplier:{sid}", "rel": "SOURCED_FROM",
                              "props": {"evidence": "supplier shortlisted on the project", "basis": "fact", "strength": 1.0}})
    return nodes, edges


def sync_graph(project_id: str) -> None:
    """Reflect a project change in the graph immediately (upsert; the next market run rebuilds it too)."""
    try:
        from dip.storage.graph import get_graph_store

        with b.session() as s:
            p = s.get(b.Project, project_id)
            market = p.market_name if p else None
        if not market:
            return
        nodes, edges = project_graph(market)
        mine = [n for n in nodes if n["id"] == f"project:{project_id}"]
        get_graph_store().upsert(mine, [e for e in edges if f"project:{project_id}" in (e["source"], e["target"])], market)
    except Exception:  # the graph is a view; never fail the workflow because of it
        import logging

        logging.getLogger("dip.projects").exception("graph sync failed for project %s", project_id)

"""Operations layer (Master Prompt 3): migration, audit log, supplier and market-level change
events, daily summary, projects (pipeline, approvals, outcomes), comments, messages, roles,
scheduler inbox."""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_ops")
    mp = pytest.MonkeyPatch()
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    mp.setenv("DIP_INBOX_DIR", str(d / "inbox"))
    mp.setenv("DIP_AUTH", "off")
    mp.setenv("DIP_FORECAST_WORKERS", "1")
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_SMTP_HOST"):
        mp.delenv(var, raising=False)
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()
    from fastapi.testclient import TestClient

    from dip.api.app import app
    yield TestClient(app), d
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def _wait(c, job_id):
    for _ in range(240):
        j = c.get(f"/api/v2/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def _upload(c, df, market, name):
    r = c.post("/api/v2/datasets", files={"file": (name, df.to_csv(index=False), "text/csv")}, data={"market": market})
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    return j


@pytest.fixture(scope="module")
def market(env):
    from test_engine_smoke import synthetic_dataset

    c, _ = env
    c.post("/api/v2/ownership", json={"employee": "Ni Zheng", "category_label": "Micromotor", "market": "micromotor"})
    _upload(c, synthetic_dataset(months=4), "micromotor", "mm1.csv")
    return "micromotor"


def test_additive_migration_adds_new_columns(tmp_path):
    from sqlalchemy import create_engine

    from dip.storage import business as b

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("create table employees (id varchar(32) primary key, name varchar(255), title varchar(255), created_at datetime)")
    con.commit()
    con.close()
    added = b.migrate(create_engine(f"sqlite:///{db}"))
    assert {"employees.department", "employees.responsibilities", "employees.email"} <= set(added)
    assert b.migrate(create_engine(f"sqlite:///{db}")) == []             # idempotent


def test_audit_log_records_mutations(env, market):
    c, _ = env
    c.post("/api/v2/ownership", json={"employee": "Audit Person", "category_label": "Audit cat"})
    rows = c.get("/api/v2/audit?resource=ownership").json()
    assert rows and rows[0]["action"] == "POST /api/v2/ownership" and rows[0]["status"] == 200
    # page views and read-only computations are not state changes: never audited
    c.post("/api/v2/telemetry/view", json={"path": "/markets"})
    c.post("/api/v2/analyst/ask-v3", json={"question": "How big is the market?", "lang": "en"})
    assert not c.get("/api/v2/audit?resource=telemetry").json() and not c.get("/api/v2/audit?resource=analyst").json()


def test_market_size_change_and_supplier_events_reach_owner_summary(env, market):
    from test_engine_smoke import SUPPLIERS, synthetic_dataset

    c, _ = env
    df = synthetic_dataset(months=4)
    df["Units/Month"] = df["Units/Month"] * 2                      # demand doubles
    df["Col_X"] = df["Col_X"] * 2
    _upload(c, df, market, "mm2.csv")
    ev = {e["kind"]: e for e in c.get(f"/api/v2/events?market={market}").json()}
    assert ev["market.size_change"]["payload"]["change"] == pytest.approx(1.0, abs=0.05)
    assert ev["market.size_change"]["severity"] == "important"
    c.post("/api/v2/suppliers/import", files={"file": ("s.csv", SUPPLIERS.to_csv(index=False), "text/csv")})
    sup = [e for e in c.get("/api/v2/events?kind=supplier.new").json()]
    assert any(e["market_name"] == market and "Shenzhen Dental Motor Co" in e["subject"] for e in sup)
    from dip.storage import business as b
    emp = b.stable_id("emp", "Ni Zheng")
    s = c.get(f"/api/v2/employees/{emp}/summary?hours=24").json()
    assert s["markets"] == [market] and s["events"] > 0
    assert any("revenue +100%" in i or "units +100%" in i for i in s["items"])
    assert any("supplier" in h for h in s["headline"])
    msgs = c.get(f"/api/v2/messages?employee_id={emp}").json()
    assert any(m["kind"] == "alert" for m in msgs)                   # important event -> internal message


def test_project_pipeline_approval_and_outcome(env, market):
    c, _ = env
    from dip.storage import business as b
    emp = b.stable_id("emp", "Ni Zheng")
    p = c.post("/api/v2/projects", json={"title": "Brushless lab micromotor 50k rpm", "market": market,
                                         "owner_employee_id": emp,
                                         "idea": {"price": 330, "unit_cost": 120}}).json()
    assert p["stage"] == "opportunity" and p["prediction"]["market_attractiveness"] is not None
    pid = p["id"]
    for st in ("evaluation", "supplier_search", "prototype", "launch_decision"):
        assert c.post(f"/api/v2/projects/{pid}/stage", json={"stage": st}).status_code == 200
    blocked = c.post(f"/api/v2/projects/{pid}/stage", json={"stage": "launched"})
    assert blocked.status_code == 409 and "approved" in blocked.json()["detail"]
    assert c.post(f"/api/v2/projects/{pid}/approve", json={}).status_code == 409   # not recommended yet
    r = c.post(f"/api/v2/projects/{pid}/recommend", json={"text": "I recommend developing this product."}).json()
    assert r["status"] == "pending_approval"
    r = c.post(f"/api/v2/projects/{pid}/approve", json={"text": "Approved"}).json()
    assert r["status"] == "approved"
    r = c.post(f"/api/v2/projects/{pid}/stage", json={"stage": "launched"}).json()
    kinds = [h["kind"] for h in r["history"]]
    assert kinds[0] == "created" and "recommend" in kinds and "approve" in kinds and kinds[-1] == "stage"
    # track an existing listing of the market; its observed sales become the outcome
    asin = c.get(f"/api/v2/markets/{market}/products?limit=1&sort=revenue").json()["items"][0]["best_listing"]
    c.post(f"/api/v2/projects/{pid}/track", json={"listing_ids": [asin]})
    o = c.get(f"/api/v2/projects/{pid}/outcome").json()
    assert o["status"] == "ok" and o["observations"] and o["checkpoints"][0]["status"] == "pending"
    assert c.get("/api/v2/calibration").json()["projects"] >= 1
    msgs = c.get(f"/api/v2/messages?employee_id={emp}").json()
    assert any(m["kind"] == "approval" and "Approved" in m["subject"] for m in msgs)
    assert any(a["action"] == "project.approve" for a in c.get("/api/v2/audit?action=project").json())


def test_comments_and_evaluations(env, market):
    c, _ = env
    r = c.post("/api/v2/comments", json={"target_kind": "segment", "target_id": "F0-S0", "market": market,
                                         "kind": "evaluation", "rating": 4, "text": "Strong demand, crowded mid tier."})
    assert r.status_code == 200 and r.json()["rating"] == 4
    assert c.post("/api/v2/comments", json={"target_kind": "segment", "target_id": "x", "text": "x", "rating": 9,
                                            "kind": "evaluation"}).status_code == 409
    rows = c.get("/api/v2/comments?target_kind=segment&target_id=F0-S0").json()
    assert rows[0]["text"].startswith("Strong demand")


def test_roles_supplier_manager_and_approvals(env, market, monkeypatch):
    from dip.auth import create_user
    from dip.storage import business as b

    c, _ = env
    emp = b.stable_id("emp", "Ni Zheng")
    _, pm = create_user("pm3@example.com", "PM", "product_manager", employee_id=emp)
    _, mgr = create_user("mgr3@example.com", "Manager", "manager")
    _, sm = create_user("sm3@example.com", "Supplier Mgr", "supplier_manager")
    monkeypatch.setenv("DIP_AUTH", "on")
    H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    pid = c.post("/api/v2/projects", json={"title": "Scoped project", "market": market}, headers=H(pm)).json()["id"]
    c.post(f"/api/v2/projects/{pid}/recommend", json={"reevaluate": False}, headers=H(pm))
    assert c.post(f"/api/v2/projects/{pid}/approve", json={}, headers=H(pm)).status_code == 403   # PM cannot approve
    assert c.post(f"/api/v2/projects/{pid}/approve", json={}, headers=H(mgr)).status_code == 200
    assert c.post("/api/v2/suppliers", json={"name": "Test OEM", "country": "China"}, headers=H(sm)).status_code == 200
    assert c.post("/api/v2/projects", json={"title": "x"}, headers=H(sm)).status_code == 403
    assert c.get("/api/v2/audit", headers=H(pm)).status_code == 403                               # admin only
    prof = c.get(f"/api/v2/employees/{emp}/profile", headers=H(mgr)).json()
    assert prof["categories"] and any(u["role"] == "product_manager" for u in prof["users"])
    assert any("projects" in (a["action"] or "") for a in prof["activity"])                        # activity history


def test_scheduler_processes_inbox_once(env):
    from test_engine_smoke import synthetic_dataset

    from dip.operations import run_once

    c, d = env
    inbox = d / "inbox" / "inbox_market"
    inbox.mkdir(parents=True)
    synthetic_dataset(months=2).to_csv(inbox / "inbox_market_2025-02.csv", index=False)
    first = run_once(send_summaries=False)
    assert [r["status"] for r in first["inbox"]] == ["processed"]
    assert run_once(send_summaries=False)["inbox"] == []               # already processed: skipped
    assert "inbox_market" in {m["name"] for m in c.get("/api/v2/markets").json()}
    rep = run_once(send_summaries=True)
    assert rep["summaries_sent"] >= 1

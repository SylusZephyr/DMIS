"""Productization (Master Prompt 3, P3b): tenant isolation, plans / feature flags / usage, token
expiry and revocation, scoped global views, supplier cooperation + ranking, scenario comparison,
graph Trend / Project nodes, durable job queue + worker, backup and restore."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_p3b")
    mp = pytest.MonkeyPatch()
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    mp.setenv("DIP_AUTH", "off")
    mp.setenv("DIP_FORECAST_WORKERS", "1")
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_SMTP_HOST", "DIP_JOB_MODE"):
        mp.delenv(var, raising=False)
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()
    from fastapi.testclient import TestClient

    from dip.api.app import app
    yield TestClient(app), d, mp
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def _wait(c, job_id, headers=None):
    for _ in range(240):
        j = c.get(f"/api/v2/jobs/{job_id}", headers=headers).json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def _csv(df):
    return df.to_csv(index=False)


H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731


@pytest.fixture(scope="module")
def tenants(env):
    """Default org owns 'micromotor'; org Acme owns 'acme_mm' (uploaded by an Acme analyst)."""
    from test_engine_smoke import SUPPLIERS, synthetic_dataset

    from dip.auth import create_user
    from dip.storage import business as b

    c, _, mp = env
    c.post("/api/v2/suppliers/import", files={"file": ("s.csv", _csv(SUPPLIERS), "text/csv")})
    r = c.post("/api/v2/datasets", files={"file": ("mm.csv", _csv(synthetic_dataset(months=4)), "text/csv")},
               data={"market": "micromotor"})
    assert _wait(c, r.json()["job_id"])["status"] == "done"
    _, platform_admin = create_user("root@dmis.local", "Root", "admin")
    mp.setenv("DIP_AUTH", "on")
    acme = c.post("/api/v2/orgs", json={"name": "Acme Dental", "plan": "starter"}, headers=H(platform_admin)).json()
    tok = c.post("/api/v2/auth/users", json={"email": "ana@acme.com", "name": "Ana", "role": "analyst",
                                             "org_id": acme["org_id"]}, headers=H(platform_admin)).json()["token"]
    r = c.post("/api/v2/datasets", files={"file": ("a.csv", _csv(synthetic_dataset(months=3)), "text/csv")},
               data={"market": "acme_mm"}, headers=H(tok))
    assert _wait(c, r.json()["job_id"], H(tok))["status"] == "done"
    sm = c.post("/api/v2/auth/users", json={"email": "sam@acme.com", "name": "Sam", "role": "supplier_manager",
                                            "org_id": acme["org_id"]}, headers=H(platform_admin)).json()["token"]
    assert c.post("/api/v2/suppliers", json={"name": "Acme Private Supplier", "country": "Germany",
                                             "product_categories": "dental micromotor"}, headers=H(sm)).status_code == 200
    mp.setenv("DIP_AUTH", "off")
    with b.session() as s:
        assert s.get(b.Market, "acme_mm").org_id == acme["org_id"]
    return {"admin": platform_admin, "acme": acme["org_id"], "acme_tok": tok, "acme_sm": sm}


# ---------------------------------------------------------------- tenancy, plans, usage
def test_tenant_isolation(env, tenants):
    c, _, mp = env
    mp.setenv("DIP_AUTH", "on")
    a, root = H(tenants["acme_tok"]), H(tenants["admin"])
    assert {m["name"] for m in c.get("/api/v2/markets", headers=a).json()} == {"acme_mm"}
    assert {m["name"] for m in c.get("/api/v2/markets", headers=root).json()} == {"micromotor"}
    assert c.get("/api/v2/markets/micromotor", headers=a).status_code == 403
    assert [s["name"] for s in c.get("/api/v2/suppliers", headers=a).json()] == ["Acme Private Supplier"]
    assert "Acme Private Supplier" not in {s["name"] for s in c.get("/api/v2/suppliers", headers=root).json()}
    # graph / geo / overview only show the tenant's own markets
    nodes = c.get("/api/v2/graph/explore?node=industry:dental&depth=2", headers=a).json()["nodes"]
    assert not any(n["id"] == "category:micromotor" for n in nodes)
    assert all(m["market"] == "acme_mm" for m in c.get("/api/v2/overview", headers=a).json()["by_market"])
    # a market name owned by another organization cannot be written to
    from test_engine_smoke import synthetic_dataset
    r = c.post("/api/v2/datasets", files={"file": ("x.csv", _csv(synthetic_dataset(months=2)), "text/csv")},
               data={"market": "micromotor"}, headers=a)
    assert r.status_code == 409


def test_plan_features_limits_and_usage(env, tenants):
    c, _, mp = env
    mp.setenv("DIP_AUTH", "on")
    a = H(tenants["acme_tok"])
    org = c.get("/api/v2/org", headers=a).json()
    assert org["plan"] == "starter" and org["counts"]["markets"] == 1
    assert org["usage_this_month"]["records_ingested"] > 0 and org["usage_this_month"]["datasets_processed"] == 1
    # starter has no connectors / AI analyst; projects allowed
    assert c.post("/api/v2/connectors/news_feed/poll", headers=a).status_code in (402, 403)
    assert c.post("/api/v2/analyst/ask-v3", json={"question": "Which markets are growing?", "use_ai": True}, headers=a).status_code == 402
    assert c.post("/api/v2/analyst/ask-v3", json={"question": "Which markets are growing?"}, headers=a).status_code == 200
    assert c.get("/api/v2/org", headers=a).json()["usage_this_month"]["analyst_questions"] == 1
    # user limit (starter: 5): the analyst and supplier manager are users 1-2; 3 more fit, the 6th is refused
    root = H(tenants["admin"])
    for i in range(3):
        assert c.post("/api/v2/auth/users", json={"email": f"u{i}@acme.com", "name": f"U{i}", "role": "viewer",
                                                  "org_id": tenants["acme"]}, headers=root).status_code == 200
    r = c.post("/api/v2/auth/users", json={"email": "u9@acme.com", "name": "U9", "role": "viewer", "org_id": tenants["acme"]},
               headers=root)
    assert r.status_code == 402 and "users" in r.json()["detail"]
    # upgrading the plan lifts the limit; only platform admins manage organizations
    assert c.post(f"/api/v2/orgs/{tenants['acme']}/plan", json={"plan": "professional"}, headers=a).status_code == 403
    assert c.post(f"/api/v2/orgs/{tenants['acme']}/plan", json={"plan": "professional"}, headers=root).json()["plan"] == "professional"


def test_token_expiry_and_revocation(env, tenants):
    from dip.auth import create_user
    from dip.storage import business as b

    c, _, mp = env
    mp.setenv("DIP_AUTH", "on")
    uid, tok = create_user("temp@dmis.local", "Temp", "viewer")
    assert c.get("/api/v2/auth/me", headers=H(tok)).json()["authenticated"]
    toks = c.get("/api/v2/auth/tokens", headers=H(tok)).json()
    assert toks[0]["expires_at"] is not None and "token_hash" not in toks[0]      # 90-day default, hash never returned
    c.post(f"/api/v2/auth/tokens/{toks[0]['id']}/revoke", headers=H(tok))
    assert c.get("/api/v2/markets", headers=H(tok)).status_code == 401
    _, tok2 = create_user("temp2@dmis.local", "Temp2", "viewer")
    with b.session() as s:
        t = s.query(b.ApiToken).filter(b.ApiToken.user_id == s.query(b.User).filter_by(email="temp2@dmis.local").one().id).one()
        t.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    r = c.get("/api/v2/markets", headers=H(tok2))
    assert r.status_code == 401 and "expired" in r.json()["detail"]


# ---------------------------------------------------------------- sourcing
def test_supplier_cooperation_price_level_and_ranking(env, tenants):
    c, _, mp = env
    mp.setenv("DIP_AUTH", "off")
    sups = {s["name"]: s["id"] for s in c.get("/api/v2/suppliers").json()}
    good, trade = sups["Shenzhen Dental Motor Co"], sups["Global Dental Trading Ltd"]
    for sid, price, rating, lead in ((good, 95.0, 5, 20), (trade, 140.0, 2, 60)):
        for kind in ("quote", "order"):
            r = c.post(f"/api/v2/suppliers/{sid}/interactions",
                       json={"kind": kind, "market_name": "micromotor", "unit_price": price, "rating": rating,
                             "lead_time_days": lead, "product": "brushless micromotor"})
            assert r.status_code == 200
    c.post(f"/api/v2/suppliers/{trade}/interactions", json={"kind": "issue", "note": "late delivery", "rating": 1})
    assert c.post(f"/api/v2/suppliers/{good}/interactions", json={"kind": "gift"}).status_code == 400
    hist = c.get(f"/api/v2/suppliers/{good}/interactions").json()
    assert {h["kind"] for h in hist} == {"quote", "order"}
    levels = {s["id"]: s["price_level"] for s in c.get("/api/v2/suppliers").json()}
    assert levels[good] == "low" and levels[trade] == "high"
    rank = c.get("/api/v2/sourcing/ranking?market=micromotor").json()
    assert rank[0]["supplier_id"] == good and rank[0]["rank"] == 1
    assert {"fit", "cooperation", "price", "lead_time", "reliability"} <= set(rank[0]["components"])
    assert any("issue" in e for e in rank[1]["evidence"])


# ---------------------------------------------------------------- scenario comparison
def test_launch_scenario_comparison(env, tenants):
    # v1 POST /launch/compare is retired; scenario comparison is /launch/compare-v3 (test_metrics_engine.py)
    c, _, mp = env
    mp.setenv("DIP_AUTH", "off")
    r = c.post("/api/v2/launch/compare", json={"title": "x", "scenarios": [{"name": "A", "price": 1}, {"name": "B", "price": 2}]})
    assert r.status_code == 410 and r.json()["detail"]["use_instead"] == "POST /api/v2/launch/compare-v3"


# ---------------------------------------------------------------- graph
def test_graph_trend_and_project_nodes(env, tenants):
    c, _, mp = env
    mp.setenv("DIP_AUTH", "off")
    g = c.get("/api/v2/graph/explore?node=category:micromotor&depth=1&limit=2000").json()
    assert any(n["kind"] == "Trend" for n in g["nodes"])
    sid = c.get("/api/v2/suppliers").json()[0]["id"]
    p = c.post("/api/v2/projects", json={"title": "Graph project", "market": "micromotor",
                                         "segment_id": c.get("/api/v2/markets/micromotor/segments").json()[0]["segment_id"]}).json()
    c.post(f"/api/v2/projects/{p['id']}/suppliers", json={"supplier_ids": [sid]})
    g = c.get(f"/api/v2/graph/explore?node=project:{p['id']}&depth=1").json()
    rels = {e["rel"] for e in g["edges"]}
    assert "HAS_PROJECT" in rels and "SOURCED_FROM" in rels


# ---------------------------------------------------------------- queue + worker
def test_queue_mode_and_worker(env, tenants):
    from test_engine_smoke import synthetic_dataset

    from dip import worker

    c, _, mp = env
    mp.setenv("DIP_AUTH", "off")
    mp.setenv("DIP_JOB_MODE", "queue")
    r = c.post("/api/v2/datasets", files={"file": ("q.csv", _csv(synthetic_dataset(months=2)), "text/csv")},
               data={"market": "queued_market"})
    job_id = r.json()["job_id"]
    time.sleep(0.5)
    assert c.get(f"/api/v2/jobs/{job_id}").json()["status"] == "queued"     # nothing runs inside the API
    assert worker.stats()["queued"] == 1
    assert worker.run_one("test-worker") is not None
    assert c.get(f"/api/v2/jobs/{job_id}").json()["status"] == "done"
    assert worker.run_one("test-worker") is None                            # queue empty
    mp.setenv("DIP_JOB_MODE", "thread")


# ---------------------------------------------------------------- acceptance
def test_acceptance_company_upload_to_report(env, tenants):
    """Master Prompt 3 acceptance: a new company uploads data and the platform processes it, creates
    products, sizes the market, finds opportunities, routes it to the owner, finds the company's
    suppliers, raises alerts, updates the graph and produces a report -- visible to that company only."""
    from test_engine_smoke import SUPPLIERS, synthetic_dataset

    from dip.storage import business as b

    c, _, mp = env
    mp.setenv("DIP_AUTH", "on")
    root = H(tenants["admin"])
    org = c.post("/api/v2/orgs", json={"name": "Beta Dental", "plan": "professional"}, headers=root).json()
    adm = H(c.post("/api/v2/auth/users", json={"email": "boss@beta.com", "name": "Boss", "role": "admin",
                                               "org_id": org["org_id"]}, headers=root).json()["token"])
    emp = c.post("/api/v2/ownership", json={"employee": "Bea Owner", "category_label": "Beta micromotors",
                                            "market": "beta_mm"}, headers=adm).json()["employee_id"]
    sup = SUPPLIERS.copy()
    sup["Supplier Name"] = "Beta " + sup["Supplier Name"].astype(str)
    c.post("/api/v2/suppliers/import", files={"file": ("s.csv", _csv(sup), "text/csv")}, headers=adm)
    first = synthetic_dataset(months=3)
    for i, df in enumerate([first, first.assign(**{"Units/Month": first["Units/Month"] * 2, "Col_X": first["Col_X"] * 2})]):
        r = c.post("/api/v2/datasets", files={"file": (f"b{i}.csv", _csv(df), "text/csv")}, data={"market": "beta_mm"}, headers=adm)
        job = _wait(c, r.json()["job_id"], adm)
        assert job["status"] == "done", job.get("error")
    assert job["report"]["accepted"] > 0                                                   # processed
    m = {x["name"]: x for x in c.get("/api/v2/markets", headers=adm).json()}["beta_mm"]
    assert m["products"] > 0 and m["monthly_revenue"] > 0                                  # products + market size
    segs = c.get("/api/v2/markets/beta_mm/segments", headers=adm).json()
    assert segs and all(s["opportunity_score"] is not None for s in segs)                  # opportunities
    assert any(a["event"]["market_name"] == "beta_mm"                                      # owner alerted
               for a in c.get(f"/api/v2/alerts?employee_id={emp}", headers=adm).json())
    ranked = c.get("/api/v2/sourcing/ranking?market=beta_mm", headers=adm).json()
    assert ranked and all(x["name"].startswith("Beta ") for x in ranked)                  # own suppliers only
    g = c.get("/api/v2/graph/explore?node=category:beta_mm&depth=1&limit=2000", headers=adm).json()
    assert {"Segment", "Trend"} <= {n["kind"] for n in g["nodes"]}                          # graph updated
    brief = c.get("/api/v2/markets/beta_mm/brief", headers=adm).json()
    assert brief["status"] == "ok" and brief["suppliers"]                                  # report
    assert c.get("/api/v2/markets/beta_mm", headers=H(tenants["acme_tok"])).status_code == 403
    assert "beta_mm" not in {x["name"] for x in c.get("/api/v2/markets", headers=root).json()}
    with b.session() as s:
        assert s.get(b.Market, "beta_mm").org_id == org["org_id"]
    mp.setenv("DIP_AUTH", "off")


# ---------------------------------------------------------------- backup / restore
def test_backup_and_restore_roundtrip(env, tenants, tmp_path):
    from dip import settings
    from dip.backup import backup, restore

    c, d, mp = env
    mp.setenv("DIP_AUTH", "off")
    before = {m["name"]: m["products"] for m in c.get("/api/v2/markets").json()}
    archive = backup(tmp_path / "bk")
    assert archive.exists() and archive.stat().st_size > 0
    # restore into fresh folders and read the same markets back
    mp.setenv("DIP_DATA_DIR", str(tmp_path / "r_platform"))
    mp.setenv("DIP_LAKE_DIR", str(tmp_path / "r_lake"))
    settings.get_settings.cache_clear()
    out = restore(archive)
    assert out["restored_files"] > 10
    from dip.storage import business as b
    b._engine.cache_clear()
    with b.session() as s:
        restored = {m.name: (m.summary or {}).get("category", {}).get("products") for m in s.query(b.Market).all()}
    assert {k: v for k, v in restored.items() if k in before} == before
    with pytest.raises(FileExistsError):
        restore(archive)                                                   # refuses to overwrite without force
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    settings.get_settings.cache_clear()
    b._engine.cache_clear()

"""Smoke tests for Intelligence Platform v2 (src/dip): storage adapters in
embedded mode, the staged pipeline, and every /api/v2 route. Basic by
design -- they prove the layers connect (see ARCHITECTURE_V2.md)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip")
    mp = pytest.MonkeyPatch()
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL"):
        mp.delenv(var, raising=False)
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()
    from fastapi.testclient import TestClient

    from dip.api.app import app
    yield TestClient(app)
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


@pytest.fixture(scope="module")
def market(client):
    from test_engine_smoke import REVIEWS, SUPPLIERS, synthetic_dataset

    r = client.post("/api/v2/suppliers/import", files={"file": ("s.csv", SUPPLIERS.to_csv(index=False), "text/csv")})
    assert r.json()["imported"] == 2
    r = client.post("/api/v2/datasets",
                    files={"file": ("mm.csv", synthetic_dataset().to_csv(index=False), "text/csv"),
                           "reviews": ("rv.csv", REVIEWS.to_csv(index=False), "text/csv")},
                    data={"market": "micromotor", "marketplace": "US"})
    job_id = r.json()["job_id"]
    for _ in range(1200):                 # up to 10 min: forecasting + metrics are CPU-bound on loaded CI machines
        job = client.get(f"/api/v2/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["status"] == "done", job.get("error")
    return job


def test_health_reports_embedded_backends(client):
    h = client.get("/api/v2/health").json()
    assert h["storage"]["business_db"].startswith("sqlite")
    assert h["storage"]["graph"].startswith("embedded")
    assert "local" in h["vectors"]["backend"]


def test_job_stages_and_ingestion_report(market):
    names = [s["name"] for s in market["stages"]]
    # the pipeline ends with the knowledge graph, then the truth harness checks the numbers, then the opportunity
    # board is computed for the cache
    assert names[:3] == ["ingestion", "cleaning", "relevance"] and names[-3:] == ["knowledge_graph", "integrity", "board"]
    rep = market["report"]
    assert rep["accepted"] + rep["rejected"] == rep["raw_rows"]
    assert rep["rejection_reasons"]  # the bad-price row and non-ASIN ids are rejected with reasons


def test_market_intelligence(client, market):
    m = client.get("/api/v2/markets/micromotor").json()
    assert m["dedup"]["products"] < m["dedup"]["listings"] + 1
    assert m["discovery"]["segments"] >= 3
    assert m["horizons"]["12_month"] in {"Growing", "High potential"}
    segs = client.get("/api/v2/markets/micromotor/segments").json()
    assert all(0 <= s["opportunity_score"] <= 100 for s in segs)
    assert {"f_demand", "f_supplier", "f_difficulty"} <= set(segs[0])


def test_product_detail_graph_vectors(client, market):
    items = client.get("/api/v2/markets/micromotor/products?limit=5&sort=revenue").json()["items"]
    d = client.get(f"/api/v2/products/{items[0]['product_id']}").json()
    assert d["listings"] and d["segment"] and d["opportunity"]["factors"]
    assert d["similar"], "vector index should return similar products"
    g = client.get("/api/v2/graph/explore", params={"node": "category:micromotor", "depth": 2}).json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"Category", "ProductFamily", "Segment"} <= kinds
    rels = client.get("/api/v2/graph/stats").json()["edges"]
    assert {"HAS_MODEL", "PRODUCT_SIMILAR_TO", "COMPETES_WITH", "SUPPLIED_BY", "LOCATED_IN"} <= set(rels)
    assert client.get("/api/v2/markets/micromotor/galaxy").status_code == 410       # retired v1 -> galaxy-v3
    assert client.get("/api/v2/markets/micromotor/galaxy-v3").json()["products"]


def test_universe_geo_people_modes(client, market):
    u = client.get("/api/v2/universe").json()
    assert any(c["market"] == "micromotor" for b in u["children"] for c in b["children"])
    geo = client.get("/api/v2/geo").json()["countries"]
    assert {c["iso2"] for c in geo} >= {"US", "CN"}
    # retired v1 endpoints answer 410 with their replacement (v3 behaviour is tested in test_metrics_engine.py)
    for path, v3 in (("/api/v2/shopping/recommend", "/api/v2/shopping/recommend-v3"), ("/api/v2/simulate", "/api/v2/launch/simulate"),
                     ("/api/v2/ask", "/api/v2/analyst/ask-v3")):
        r = client.post(path, json={"need": "micromotor", "question": "x", "market": "micromotor"})
        assert r.status_code == 410 and v3 in r.json()["detail"]["use_instead"]
    client.post("/api/v2/ownership", json={"employee": "Ni Zheng", "category_label": "Micromotor", "market": "micromotor"})
    emp = next(e for e in client.get("/api/v2/employees").json() if e["name"] == "Ni Zheng")
    k = client.get(f"/api/v2/employees/{emp['id']}/dashboard").json()["kpis"]
    assert k["markets"] == 1 and k["opportunities"] >= 1


def test_live_acquisition_and_sourcing_routes(client, market):
    """Without credentials: status says what to configure; runs are refused with guidance (never faked)."""
    st = client.get("/api/v2/acquire/status").json()
    assert st["ready"] is False and {p["name"] for p in st["providers"]} >= {"keepa", "scraper_api", "paapi", "amazon_html"}
    assert client.post("/api/v2/acquire/markets/micromotor/run", json={}).status_code == 409
    assert client.get("/api/v2/acquire/runs").json() == []
    ss = client.get("/api/v2/sourcing/status").json()
    assert ss["ready"] is False and {p["name"] for p in ss["platforms"]} >= {"1688", "alibaba", "aliexpress"}
    seg = client.get("/api/v2/markets/micromotor/segments").json()[0]["segment_id"]
    c = client.post("/api/v2/sourcing/concept", json={"market": "micromotor", "scope": "segment", "scope_id": seg}).json()
    assert c["label"] and c["target_price"] and c["first_order_qty"] > 0 and c["queries_en"]
    assert any("一" <= ch <= "鿿" for q in c["terms_zh"] for ch in q)      # Chinese queries for 1688 / Taobao
    assert client.post("/api/v2/sourcing/concept", json={"market": "micromotor"}).status_code == 400
    assert client.post("/api/v2/sourcing/runs", json={"market": "micromotor", "text": "brushless micromotor"}).status_code == 409

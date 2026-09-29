"""Token auth and role permissions on /api/v2."""

from __future__ import annotations

import pytest


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    for v in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL"):
        monkeypatch.delenv(v, raising=False)
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear(); graph.get_graph_store.cache_clear(); vectors.get_vector_store.cache_clear()
    from fastapi.testclient import TestClient

    from dip.api.app import app
    yield TestClient(app), monkeypatch
    settings.get_settings.cache_clear(); graph.get_graph_store.cache_clear(); vectors.get_vector_store.cache_clear()


def h(token):
    return {"Authorization": f"Bearer {token}"}


def test_embedded_mode_is_open_by_default(api):
    client, mp = api
    mp.delenv("DIP_AUTH", raising=False)
    assert client.get("/api/v2/markets").status_code == 200
    me = client.get("/api/v2/auth/me").json()
    assert me["auth_enabled"] is False and me["authenticated"] is False


def test_enforced_role_matrix(api):
    client, mp = api
    mp.setenv("DIP_AUTH", "on")
    from dip.auth import create_user

    _, admin = create_user("admin@example.com", "Admin", "admin")
    _, analyst = create_user("analyst@example.com", "Analyst", "analyst")
    _, viewer = create_user("viewer@example.com", "Viewer", "viewer")

    assert client.get("/api/v2/health").status_code == 200                      # public
    assert client.get("/api/v2/markets").status_code == 401                     # no token
    assert client.get("/api/v2/markets", headers=h("dmis_not_a_real_token")).status_code == 401
    for tok in (admin, analyst, viewer):
        assert client.get("/api/v2/markets", headers=h(tok)).status_code == 200
    assert client.get("/api/v2/auth/me", headers=h(viewer)).json()["role"] == "viewer"

    fb = {"record_id": "B0TEST0001", "text": "dental wax", "is_dental": True}
    assert client.post("/api/v2/relevance/feedback", json=fb, headers=h(viewer)).status_code == 403
    assert client.post("/api/v2/relevance/feedback", json=fb, headers=h(analyst)).status_code == 200
    sup = {"name": "Test Supplier", "country": "China"}
    assert client.post("/api/v2/suppliers", json=sup, headers=h(analyst)).status_code == 403   # analyst: suppliers read-only
    assert client.post("/api/v2/suppliers", json=sup, headers=h(admin)).status_code == 200
    assert client.get("/api/v2/jobs", headers=h(viewer)).status_code == 403    # viewers do not see data operations
    own = {"employee": "Ni Zheng", "category_label": "Micromotor"}
    assert client.post("/api/v2/ownership", json=own, headers=h(analyst)).status_code == 403

    assert client.post("/api/v2/auth/users", json={"email": "x@example.com", "name": "X"}, headers=h(analyst)).status_code == 403
    r = client.post("/api/v2/auth/users", json={"email": "m@example.com", "name": "M", "role": "manager"}, headers=h(admin))
    assert r.status_code == 200 and r.json()["token"].startswith("dmis_")
    assert client.post("/api/v2/ownership", json=own, headers=h(r.json()["token"])).status_code == 200

    t = client.post("/api/v2/auth/tokens", json={"name": "laptop"}, headers=h(viewer)).json()["token"]
    assert client.get("/api/v2/markets", headers=h(t)).status_code == 200


def test_tokens_are_stored_hashed(api):
    client, mp = api
    from dip.auth import create_user
    from dip.storage import business as b

    _, token = create_user("hash@example.com", "Hash", "viewer")
    with b.session() as s:
        stored = [t.token_hash for t in s.query(b.ApiToken).all()]
    assert token not in stored and all(len(x) == 64 for x in stored)

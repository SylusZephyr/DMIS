"""Correctness and safety (audit phase A): market-name validation and safe lake paths,
content-hash duplicate registry, evidence grade vs. a failing hold-out check, atomic lake
writes + per-market lock, upload limits, security headers, rate limits and session cookies."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))

HOSTILE = ["O'Brien dental", "a/b", "../escape", "..", "x" * 65, "", "   ", 'say "hi"', "back\\slash",
           "market=x", "glob*", "semi;colon", "trailing.", "_leading", "new\nline"]
VALID = ["dental_models", "denture_base", "O Brien dental", "义齿基托", "a.b-c_d", "Implants 2026"]


def _reset_stores():
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def _reset_settings():
    """Lake-only unit tests: re-read DIP_LAKE_DIR without reopening the module's graph/vector stores."""
    from dip import settings
    settings.get_settings.cache_clear()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_safety")
    mp = pytest.MonkeyPatch()
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    mp.setenv("DIP_INBOX_DIR", str(d / "inbox"))
    mp.setenv("DIP_AUTH", "off")
    mp.setenv("DIP_FORECAST_WORKERS", "1")
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_SMTP_HOST"):
        mp.delenv(var, raising=False)
    _reset_stores()
    from fastapi.testclient import TestClient

    from dip.api.app import app
    yield TestClient(app), d
    mp.undo()
    _reset_stores()


def _wait(c, job_id):
    for _ in range(400):
        j = c.get(f"/api/v2/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def _body(months=3):
    from test_engine_smoke import synthetic_dataset
    return synthetic_dataset(months=months).to_csv(index=False)


def _post(c, body, market, name="d.csv", **data):
    return c.post("/api/v2/datasets", files={"file": (name, body, "text/csv")}, data={"market": market, **data})


# ---------------------------------------------------------------- 1. market names
@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_market_names_are_rejected(name):
    from dip.storage import lake
    with pytest.raises(lake.InvalidMarketName):
        lake.validate_market_name(name)
    assert not lake.is_valid_market_name(name)


@pytest.mark.parametrize("name", VALID)
def test_valid_market_names_are_kept(name):
    from dip.storage import lake
    assert lake.validate_market_name(f"  {name} ") == name


def test_lake_paths_never_leave_the_root_or_break_sql(tmp_path, monkeypatch):
    from dip.storage import lake
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    _reset_settings()
    try:
        for bad in ("O'Brien", "../x", "a/b"):
            with pytest.raises(ValueError):
                lake.curated_path("products", bad)
            with pytest.raises(ValueError):
                lake.write_curated("products", bad, pd.DataFrame({"a": [1]}))
            assert lake.has_curated("products", bad) is False          # reads: "not found", never a crash
            assert lake.read_curated("products", bad).empty
            assert lake.curated_columns("products", bad) == set()
        for bad_id in ("../x", "a'b", "x/y"):
            with pytest.raises(ValueError):
                lake.std_path(bad_id)
        with pytest.raises(ValueError):
            lake.write_curated("../products", "m", pd.DataFrame({"a": [1]}))
        assert lake.sql_literal("/a/it's") == "'/a/it''s'"
        assert not list((tmp_path / "lake").rglob("*.parquet")) if (tmp_path / "lake").exists() else True
    finally:
        _reset_settings()


def test_lake_root_with_a_quote_is_escaped_for_duckdb(tmp_path, monkeypatch):
    from dip.storage import lake
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "it's lake"))
    _reset_settings()
    try:
        lake.write_std(pd.DataFrame({"id": ["B0A"], "title": ["x"]}), "ds1")
        assert lake.read_std(["ds1"], ["id", "title"])["id"].tolist() == ["B0A"]   # list literal escaped
        with pytest.raises(ValueError, match="quote"):
            lake.curated_path("products", "m")          # callers that embed it in SQL are protected
    finally:
        _reset_settings()


def test_api_rejects_hostile_market_names(env):
    c, d = env
    before = c.get("/api/v2/jobs").json()
    for name in ["O'Brien dental", "a/b", "../escape", "x" * 65, "", "  "]:
        r = _post(c, "a,b\n1,2\n", name)
        assert r.status_code in (400, 422), (name, r.status_code, r.text)
    assert c.get("/api/v2/jobs").json() == before                       # nothing was queued
    lake_dir = d / "lake"
    assert not [p for p in lake_dir.rglob("*") if "Brien" in str(p) or "escape" in str(p)] if lake_dir.exists() else True


def test_cli_rejects_hostile_market_names(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a,b\n1,2\n")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dmis.py"), "process", str(f), "--market", "../evil"],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "DIP_DATA_DIR": str(tmp_path / "p"),
                                                            "DIP_LAKE_DIR": str(tmp_path / "l")})
    assert r.returncode == 2 and "invalid market name" in r.stderr


@pytest.fixture(scope="module")
def spaced_market(env):
    """A valid name with spaces processed end to end, snapshot 2026-01-01."""
    c, _ = env
    r = _post(c, _body(), "O Brien dental", snapshot_date="2026-01-01")
    assert r.status_code == 200, r.text
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    return "O Brien dental"


def test_valid_name_with_spaces_works_end_to_end(env, spaced_market):
    c, _ = env
    m = quote(spaced_market)
    assert c.get(f"/api/v2/markets/{m}").status_code == 200
    assert spaced_market in {x["name"] for x in c.get("/api/v2/markets").json()}
    segs = c.get(f"/api/v2/markets/{m}/segments-v3")
    assert segs.status_code == 200 and len(segs.json()) > 0
    from dip.storage import lake
    assert len(lake.read_curated("products", spaced_market)) > 0


# ---------------------------------------------------------------- 2. duplicate content
def test_duplicate_content_as_new_period_or_market_is_refused(env, spaced_market):
    c, _ = env
    body = _body()
    r = _post(c, body, spaced_market, snapshot_date="2026-02-01")
    assert r.status_code == 409
    assert spaced_market in r.json()["detail"] and "2026-01-01" in r.json()["detail"]
    r = _post(c, body, "market_b", snapshot_date="2026-01-01")
    assert r.status_code == 409 and spaced_market in r.json()["detail"]
    r = _post(c, body, "market_b")
    assert r.status_code == 409
    assert "market_b" not in {x["name"] for x in c.get("/api/v2/markets").json()}


def test_identical_reupload_same_market_and_period_still_works(env, spaced_market):
    c, _ = env
    r = _post(c, _body(), spaced_market, snapshot_date="2026-01-01")
    assert r.status_code == 200
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done" and [s["name"] for s in j["stages"]] == ["skipped"]


def test_duplicate_override_needs_a_reason_and_is_audited(env, spaced_market):
    c, _ = env
    body = _body()
    assert _post(c, body, "market_c", allow_duplicate="true").status_code == 400          # no reason
    r = _post(c, body, "market_c", allow_duplicate="true", reason="same export covers two sub-markets")
    assert r.status_code == 200
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    ds = [x for x in c.get("/api/v2/datasets", params={"market": "market_c"}).json()]
    assert ds and ds[0]["duplicate_of"] and ds[0]["duplicate_reason"] == "same export covers two sub-markets"
    from dip.storage import business as b
    with b.session() as s:
        rows = s.query(b.AuditLog).filter(b.AuditLog.action == "dataset.duplicate_override").all()
        assert any((x.detail or {}).get("reason") == "same export covers two sub-markets"
                   and (x.detail or {}).get("existing") for x in rows)


def test_duplicate_is_refused_on_the_runner_and_inbox_paths(env, spaced_market, tmp_path):
    from dip import operations
    from dip.pipeline import runner
    from dip.storage import business as b

    c, d = env
    f = tmp_path / "again.csv"
    f.write_text(_body())
    with pytest.raises(b.DuplicateContent, match="O Brien dental"):
        runner.process_dataset(f, "cli_market", snapshot_date="2026-03-01")
    inbox = d / "inbox"
    (inbox / "inbox_market").mkdir(parents=True)
    (inbox / "inbox_market" / "export_2026-04.csv").write_text(_body())
    (inbox / "bad'name").mkdir()
    out = operations.process_inbox()
    assert {"market": "inbox_market", "file": "export_2026-04.csv"}.items() <= next(
        o for o in out if o["market"] == "inbox_market").items()
    assert next(o for o in out if o["market"] == "inbox_market")["status"] == "duplicate"
    assert next(o for o in out if o["market"] == "bad'name")["status"] == "rejected"


def test_duplicates_are_scoped_to_the_organization():
    from dip.storage import business as b
    assert b.duplicate_conflicts("0" * 64, "m", None, "some-org") == []


# ---------------------------------------------------------------- 3. evidence grade vs. hold-out AUC
@pytest.mark.parametrize("auc, capped", [(0.46, True), (0.95, False)])
def test_failing_holdout_caps_evidence_grade(monkeypatch, auc, capped):
    from test_metrics_engine import planted_market

    from dip.metrics import engine
    monkeypatch.setattr(engine, "crossvalidate", lambda *a, **k: {"auc_badged_vs_unbadged": auc})
    L, P, S = planted_market()
    L = L.copy()
    L["sales"] = L["sales"].fillna(50.0)               # every listing badged: the A-C thresholds are met
    s = engine.compute(L, P, S, as_of=pd.Timestamp("2026-07-01")).summary
    if capped:
        assert s["evidence_grade"] == "D"
        assert "capped" in s["evidence_grade_reason"] and "0.46" in s["evidence_grade_reason"]
    else:
        assert s["evidence_grade"] == "A" and "capped" not in s["evidence_grade_reason"]


def test_evidence_grade_floor_is_configured():
    from dip.metrics import config
    eg = config()["evidence_grade"]
    assert 0.5 < eg["min_auc_any"] <= eg["B"]["auc"]


# ---------------------------------------------------------------- 4. atomic writes + per-market lock
def test_failed_write_leaves_previous_file_intact(tmp_path, monkeypatch):
    from dip.storage import lake
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    _reset_settings()
    try:
        lake.write_curated("products", "m", pd.DataFrame({"product_id": ["p1", "p2"]}))

        def broken(self, path, *a, **k):
            Path(path).write_bytes(b"PAR1 half a file")          # partial output, then the crash
            raise OSError("disk full")
        monkeypatch.setattr(pd.DataFrame, "to_parquet", broken)
        with pytest.raises(OSError):
            lake.write_curated("products", "m", pd.DataFrame({"product_id": ["new"]}))
        monkeypatch.undo()
        monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
        _reset_settings()
        assert lake.read_curated("products", "m")["product_id"].tolist() == ["p1", "p2"]
        assert [p.name for p in (tmp_path / "lake" / "curated" / "products" / "market=m").iterdir()] == ["data.parquet"]
    finally:
        _reset_settings()


def test_market_lock_is_exclusive_per_market(tmp_path, monkeypatch):
    from dip.storage import lake
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    _reset_settings()
    try:
        with lake.market_lock("m1", timeout=0):
            with pytest.raises(lake.MarketBusy, match="being processed"):
                with lake.market_lock("m1", timeout=0.3):
                    pass
            with lake.market_lock("m2", timeout=0):              # other markets are independent
                pass
        with lake.market_lock("m1", timeout=0):                  # released
            pass
        # across processes (API + worker/CLI): a second process cannot take a held lock
        code = ("import sys, time; sys.path.insert(0, %r); from dip.storage import lake\n"
                "with lake.market_lock('m1', timeout=0):\n    print('held', flush=True); time.sleep(4)\n") % str(ROOT / "src")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                                env={"PATH": "/usr/bin:/bin", "DIP_LAKE_DIR": str(tmp_path / "lake"),
                                     "DIP_DATA_DIR": str(tmp_path / "p")})
        try:
            assert proc.stdout.readline().strip() == "held"
            with pytest.raises(lake.MarketBusy):
                with lake.market_lock("m1", timeout=0.2):
                    pass
        finally:
            proc.wait(timeout=30)
        with lake.market_lock("m1", timeout=5):
            pass
    finally:
        _reset_settings()


def test_concurrent_run_of_same_market_fails_clearly(env, monkeypatch, tmp_path):
    from dip.pipeline import runner
    from dip.storage import business as b
    from dip.storage import lake

    monkeypatch.setenv("DIP_MARKET_LOCK_TIMEOUT", "0")
    f = tmp_path / "x.csv"
    f.write_text(_body(months=2))
    job = runner.create_job("process_dataset", "locked_market")
    with lake.market_lock("locked_market"):
        with pytest.raises(lake.MarketBusy):
            runner.process_dataset(f, "locked_market", job_id=job)
    with b.session() as s:
        j = s.get(b.Job, job)
        assert j.status == "failed" and "being processed by another job" in j.error


# ---------------------------------------------------------------- 5. uploads, headers, rate limits, cookies
def test_upload_size_limit(env, monkeypatch):
    c, _ = env
    monkeypatch.setenv("DIP_MAX_UPLOAD_MB", "0.001")               # ~1 KB
    r = _post(c, "a,b\n" + "1,2\n" * 2000, "size_market")            # under the header slack: counted while saving
    assert r.status_code == 413 and "too large" in r.json()["detail"]
    r = _post(c, "a,b\n" + "1,2\n" * 400_000, "size_market")         # over the declared-length limit
    assert r.status_code == 413


def test_upload_row_limit(env, monkeypatch, tmp_path):
    c, _ = env
    monkeypatch.setenv("DIP_MAX_UPLOAD_ROWS", "5")
    r = _post(c, "a,b\n" + "1,2\n" * 10, "rows_market")
    assert r.status_code == 400 and "too many rows" in r.json()["detail"]
    from dip.pipeline.ingestion import ingest_dataset
    from test_engine_smoke import synthetic_dataset
    p = tmp_path / "big.csv"
    synthetic_dataset(months=2).to_csv(p, index=False)
    with pytest.raises(ValueError, match="DIP_MAX_UPLOAD_ROWS"):
        ingest_dataset(p, "rows_market", "rowsds1")


def test_security_headers(env):
    c, _ = env
    r = c.get("/api/v2/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"]
    assert "content-security-policy" not in c.get("/docs").headers         # Swagger UI keeps working
    caddy = (ROOT / "infra" / "Caddyfile").read_text()
    for h in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "frame-ancestors"):
        assert h in caddy


def test_rate_limits_expensive_and_auth_endpoints(env, monkeypatch):
    from dip.api.security import limiter
    c, _ = env
    limiter.reset()
    monkeypatch.setenv("DIP_RATE_LIMIT_EXPENSIVE", "2")
    monkeypatch.setenv("DIP_RATE_LIMIT_AUTH", "1")
    try:
        codes = [c.post("/api/v2/launch/simulate", json={"title": "x", "price": 10}).status_code for _ in range(3)]
        assert codes[:2] != [429, 429] and codes[2] == 429
        codes = [c.post("/api/v2/analyst/ask-v3", json={"question": "How big is the market?"}).status_code for _ in range(3)]
        assert 429 not in codes[:2] and codes[2] == 429              # each endpoint has its own budget
        assert c.post("/api/v2/auth/session", json={"token": "dmis_nope"}).status_code == 401
        r = c.post("/api/v2/auth/session", json={"token": "dmis_nope"})
        assert r.status_code == 429 and r.headers["retry-after"]
        monkeypatch.setenv("DIP_RATE_LIMIT", "off")
        assert c.post("/api/v2/launch/simulate", json={"title": "x", "price": 10}).status_code != 429
    finally:
        limiter.reset()


def test_session_cookie_is_httponly_and_bearer_still_works(env, monkeypatch):
    from dip.api.security import limiter
    from dip.auth import create_user
    c, _ = env
    limiter.reset()
    monkeypatch.setenv("DIP_AUTH", "on")
    _, token = create_user("cookie@example.com", "Cookie", "analyst")
    try:
        assert c.get("/api/v2/auth/me").status_code == 401
        assert c.post("/api/v2/auth/session", json={"token": "dmis_invalid"}).status_code == 401
        r = c.post("/api/v2/auth/session", json={"token": token})
        assert r.status_code == 200 and token not in r.text                   # never echoed to scripts
        cookie = r.headers["set-cookie"].lower()
        assert "dmis_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
        me = c.get("/api/v2/auth/me").json()
        assert me["authenticated"] and me["email"] == "cookie@example.com"
        # cookie-authenticated writes from another site are refused; same-origin and bearer are fine
        fb = {"record_id": "B0COOKIE01", "text": "dental wax", "is_dental": True}
        assert c.post("/api/v2/relevance/feedback", json=fb, headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.post("/api/v2/relevance/feedback", json=fb, headers={"Origin": "http://testserver"}).status_code == 200
        assert c.delete("/api/v2/auth/session").status_code == 200
        c.cookies.clear()
        assert c.get("/api/v2/auth/me").status_code == 401
        assert c.get("/api/v2/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["authenticated"]
    finally:
        c.cookies.clear()
        limiter.reset()

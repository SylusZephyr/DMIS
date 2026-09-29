"""Operations (Phase E): response-cache backends, request ids / JSON logs, Alembic baseline, Qdrant backup hook."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- response cache backends
@pytest.fixture()
def rcache(monkeypatch):
    from dip import cache, settings

    monkeypatch.setattr(cache, "data_version", lambda: "v1")
    monkeypatch.setattr(cache, "_redis", None)
    monkeypatch.setattr(cache, "_redis_url", None)
    monkeypatch.setattr(cache, "_redis_down_until", 0.0)
    monkeypatch.delenv("DIP_REDIS_URL", raising=False)
    settings.get_settings.cache_clear()
    cache._memory.clear()
    yield cache
    cache._memory.clear()
    settings.get_settings.cache_clear()


def _counted(cache):
    calls = []

    @cache.response_cache
    def endpoint(x: int):
        calls.append(x)
        return {"x": x, "items": [1, 2]}
    return endpoint, calls


def test_cache_defaults_to_in_process(rcache):
    assert rcache.backend() is rcache._memory
    ep, calls = _counted(rcache)
    assert ep(1) == ep(1) == {"x": 1, "items": [1, 2]}
    assert calls == [1]


def test_cache_uses_redis_when_configured_and_is_shared(rcache, monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    monkeypatch.setattr(rcache, "_redis_client", lambda url: fakeredis.FakeRedis(server=server))
    monkeypatch.setenv("DIP_REDIS_URL", "redis://cache:6379/0")
    from dip import settings
    settings.get_settings.cache_clear()

    assert rcache.backend().name == "redis"
    ep, calls = _counted(rcache)
    assert ep(2) == {"x": 2, "items": [1, 2]}
    rcache._memory.clear()                          # another API worker: empty local memory, same Redis
    assert ep(2) == {"x": 2, "items": [1, 2]}
    assert calls == [2]
    keys = fakeredis.FakeRedis(server=server).keys(rcache.REDIS_PREFIX + "*")
    assert len(keys) == 1
    rcache.clear()
    assert fakeredis.FakeRedis(server=server).keys(rcache.REDIS_PREFIX + "*") == []


def test_cache_falls_back_when_redis_is_down(rcache, monkeypatch, caplog):
    class Down:
        def get(self, *a, **k):
            raise ConnectionError("connection refused")

        set = scan_iter = delete = get

    monkeypatch.setattr(rcache, "_redis_client", lambda url: Down())
    monkeypatch.setenv("DIP_REDIS_URL", "redis://nowhere:6379/0")
    from dip import settings
    settings.get_settings.cache_clear()

    ep, calls = _counted(rcache)
    with caplog.at_level(logging.WARNING, logger="dip.cache"):
        assert ep(3) == {"x": 3, "items": [1, 2]}      # the request still succeeds
    assert "Redis unavailable" in caplog.text
    assert rcache.backend() is rcache._memory           # retried only after REDIS_RETRY_SECONDS
    assert ep(3) == {"x": 3, "items": [1, 2]}
    assert calls == [3]                                 # served from the in-process fallback


def test_cache_missing_redis_package_falls_back(rcache, monkeypatch):
    def boom(url):
        raise ImportError("No module named 'redis'")
    monkeypatch.setattr(rcache, "_redis_client", boom)
    monkeypatch.setenv("DIP_REDIS_URL", "redis://x:6379/0")
    from dip import settings
    settings.get_settings.cache_clear()
    assert rcache.backend() is rcache._memory


def test_non_json_values_stay_in_process(rcache, monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    monkeypatch.setattr(rcache, "_redis_client", lambda url: fakeredis.FakeRedis(server=server))
    monkeypatch.setenv("DIP_REDIS_URL", "redis://cache:6379/0")
    from dip import settings
    settings.get_settings.cache_clear()
    marker = object()

    @rcache.response_cache
    def ep():
        return {"obj": marker}
    assert ep()["obj"] is marker and ep()["obj"] is marker
    assert fakeredis.FakeRedis(server=server).keys(rcache.REDIS_PREFIX + "*") == []


# ---------------------------------------------------------------- request ids and JSON logs
def _mini_app():
    from fastapi import FastAPI

    from dip.logging_setup import RequestIdMiddleware, request_id_var

    app = FastAPI()

    @app.get("/rid")
    def rid():
        logging.getLogger("dip.test").info("inside request")
        return {"rid": request_id_var.get()}
    return RequestIdMiddleware(app)


def test_request_id_is_generated_and_returned():
    from fastapi.testclient import TestClient

    c = TestClient(_mini_app())
    r = c.get("/rid")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32 and r.json()["rid"] == rid
    assert c.get("/rid").headers["x-request-id"] != rid           # one per request


def test_request_id_is_propagated_when_safe_and_replaced_when_not():
    from fastapi.testclient import TestClient

    c = TestClient(_mini_app())
    assert c.get("/rid", headers={"X-Request-ID": "abc-123.x"}).headers["x-request-id"] == "abc-123.x"
    bad = c.get("/rid", headers={"X-Request-ID": "evil\tvalue with spaces" + "x" * 200}).headers["x-request-id"]
    assert bad != "evil" and len(bad) == 32


def test_platform_asgi_entry_point_sets_request_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    for v in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_REDIS_URL"):
        monkeypatch.delenv(v, raising=False)
    from dip import settings
    from dip.storage import graph, vectors

    def reset():
        settings.get_settings.cache_clear()
        graph.get_graph_store.cache_clear()
        vectors.get_vector_store.cache_clear()
    reset()
    from fastapi.testclient import TestClient

    from dip.api.asgi import app
    try:
        r = TestClient(app).get("/api/v2/health", headers={"X-Request-ID": "smoke-1"})
        assert r.status_code == 200 and r.headers["x-request-id"] == "smoke-1"
    finally:
        reset()


def test_json_formatter_carries_request_job_and_stage():
    from dip.logging_setup import ContextFilter, JsonFormatter, log_context

    rec = logging.LogRecord("dip.runner", logging.INFO, __file__, 1, "stage %s", ("x",), None)
    with log_context(request_id="r1", job_id="j1", stage="relevance"):
        ContextFilter().filter(rec)
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "stage x" and out["level"] == "INFO" and out["logger"] == "dip.runner"
    assert (out["request_id"], out["job_id"], out["stage"]) == ("r1", "j1", "relevance")
    rec2 = logging.LogRecord("dip", logging.INFO, __file__, 1, "outside", (), None)
    ContextFilter().filter(rec2)
    assert "job_id" not in json.loads(JsonFormatter().format(rec2))


def test_sentry_is_optional(monkeypatch):
    from dip import logging_setup

    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert logging_setup.init_sentry() is False


# ---------------------------------------------------------------- Alembic
def test_alembic_upgrade_head_on_fresh_sqlite(tmp_path):
    pytest.importorskip("alembic.command")  # the repo's alembic/ folder imports as a namespace package when alembic is absent
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    from dip.storage.business import Base

    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    eng = create_engine(url)
    tables = set(inspect(eng).get_table_names())
    assert "alembic_version" in tables
    assert {"users", "markets", "jobs", "job_queue"} <= tables
    with eng.connect() as con:
        diff = compare_metadata(MigrationContext.configure(con), Base.metadata)
    # the baseline matches the models; later model changes may only be additive until a revision is written
    flat = [d for group in diff for d in (group if isinstance(group, list) else [group])]
    assert all(d[0] in ("add_table", "add_column", "add_index") for d in flat), flat
    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}


# ---------------------------------------------------------------- backup: Qdrant server snapshot hook
class _FakeQdrant(BaseHTTPRequestHandler):
    deleted: list[str] = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/collections":
            self._json({"result": {"collections": [{"name": "products"}]}})
        elif self.path == "/collections/products/snapshots/snap-1.snapshot":
            body = b"SNAPSHOT-BYTES"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        assert self.path.startswith("/collections/products/snapshots")
        self._json({"result": {"name": "snap-1.snapshot"}})

    def do_DELETE(self):
        _FakeQdrant.deleted.append(self.path)
        self._json({"result": True})


def test_backup_includes_qdrant_server_snapshots(tmp_path, monkeypatch):
    import tarfile

    srv = HTTPServer(("127.0.0.1", 0), _FakeQdrant)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setenv("DIP_QDRANT_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("DIP_NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.delenv("DIP_POSTGRES_URL", raising=False)
    from dip import settings
    settings.get_settings.cache_clear()
    try:
        from dip.backup import backup

        (tmp_path / "lake" / "x.txt").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "lake" / "x.txt").write_text("lake")
        archive = backup(tmp_path / "bk")
        with tarfile.open(archive) as tar:
            man = json.load(tar.extractfile("manifest.json"))
            assert tar.extractfile("qdrant/products.snapshot").read() == b"SNAPSHOT-BYTES"
        assert man["qdrant"]["products"]["file"] == "qdrant/products.snapshot"
        assert "neo4j-admin" in man["neo4j"]
        assert "lake/x.txt" in man["files"]
        assert _FakeQdrant.deleted                                   # server-side copy cleaned up
        assert not list((tmp_path / "bk").glob("qdrant-*"))          # temp files removed

        srv.shutdown()                                               # unreachable server: recorded, not fatal
        archive2 = backup(tmp_path / "bk2")
        with tarfile.open(archive2) as tar:
            assert json.load(tar.extractfile("manifest.json"))["qdrant"].startswith("NOT included")
    finally:
        settings.get_settings.cache_clear()


# ---------------------------------------------------------------- queue: inputs staged on the shared volume
def test_enqueue_stages_inputs_under_the_data_dir(tmp_path, monkeypatch):
    import pandas as pd

    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.delenv("DIP_POSTGRES_URL", raising=False)
    from dip import settings
    settings.get_settings.cache_clear()
    try:
        from dip import worker
        from dip.storage import business as b

        upload = tmp_path / "api-private-tmp" / "export.csv"          # the API container's own temp dir
        upload.parent.mkdir()
        upload.write_text("a,b\n1,2\n")
        worker.enqueue("job123", {"source": upload, "market": "m", "reviews": pd.DataFrame({"text": ["hot"]})})
        with b.session() as s:
            payload = s.query(b.QueuedJob).filter_by(job_id="job123").one().payload
        staged = Path(payload["source"])
        assert staged.parent == tmp_path / "platform" / "queue" / "job123"
        upload.unlink()                                                # a worker elsewhere still has the file
        assert staged.read_text() == "a,b\n1,2\n"
        assert Path(payload["reviews_path"]).parent == staged.parent
    finally:
        settings.get_settings.cache_clear()

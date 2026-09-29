"""Acceptance (Phase 8): one product manager's journey through the platform, in English and Chinese.

Two monthly uploads of a market -> v3 size with interval and evidence grade -> segments explained ->
recommended product -> competitor shares and significance-tested changes -> launch simulation and
scenario comparison -> shopping choice -> galaxy -> knowledge-graph path with evidence -> analyst in both
languages -> alerts only on significant changes -> a project predicted on v3 -> the accuracy dashboard.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))


def _wait(c, job_id):
    for _ in range(240):
        j = c.get(f"/api/v2/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


@pytest.fixture(scope="module")
def c(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_accept")
    mp = pytest.MonkeyPatch()
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    mp.setenv("DIP_AUTH", "off")
    mp.setenv("DIP_FORECAST_WORKERS", "1")
    mp.delenv("GEMINI_API_KEY", raising=False)
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_JOB_MODE"):
        mp.delenv(var, raising=False)
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()
    from fastapi.testclient import TestClient
    from test_engine_smoke import synthetic_dataset

    from dip.api.app import app
    client = TestClient(app)
    first = synthetic_dataset(months=3)
    second = synthetic_dataset(months=4)                           # the next export adds a month ...
    second["Units/Month"] = second["Units/Month"] * 3            # ... and demand triples
    second["Col_X"] = second["Col_X"] * 3
    for i, (df, snap) in enumerate(((first, "2026-01-01"), (second, "2026-02-01"))):
        r = client.post("/api/v2/datasets", files={"file": (f"m{i}.csv", df.to_csv(index=False), "text/csv")},
                        data={"market": "accept", "snapshot_date": snap})
        j = _wait(client, r.json()["job_id"])
        assert j["status"] == "done", j.get("error")
    yield client
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def test_journey(c):
    # 1. the market's size is an estimate with an interval and an evidence grade
    m = next(x for x in c.get("/api/v2/markets").json() if x["name"] == "accept")
    assert m["revenue_lo"] <= m["revenue_est"] <= m["revenue_hi"] and m["evidence_grade"] in "ABCD"
    assert m["top_segment"]["opportunity_index"] is not None

    # 2. segments explained, with the formula documented
    segs = c.get("/api/v2/markets/accept/segments-v3").json()
    sid = segs[0]["segment_id"]
    ex = c.get(f"/api/v2/markets/accept/segments/{sid}/explain").json()
    assert ex["opportunity_components"] and ex["metrics"]
    assert c.get("/api/v2/methodology").json()["sections"]

    # 3. competitors: shares with intervals; two snapshots -> changes are tested, not asserted
    comp = c.get("/api/v2/markets/accept/competitors-v3").json()
    assert comp["brands"] and comp["change_info"]["status"] == "ok"
    assert all({"z", "q_value", "significant"} <= set(r) for r in comp["changes"])

    # 4. launch what-if and scenario comparison on the fitted model
    p = c.get("/api/v2/markets/accept/products?limit=1").json()["items"][0]
    sim = c.post("/api/v2/launch/simulate", json={"title": p["title"], "price": p["price"], "unit_cost": p["price"] * 0.3,
                                                  "market": "accept"}).json()
    assert sim["units"]["p10"] <= sim["units"]["median"] <= sim["units"]["p90"] and 0 <= sim["profit"]["p_positive"] <= 1
    cmp = c.post("/api/v2/launch/compare-v3", json={"title": p["title"], "market": "accept", "unit_cost": p["price"] * 0.3,
                                                    "scenarios": [{"price": p["price"]}, {"price": p["price"] * 1.3}]}).json()
    assert abs(sum(s["p_best"] for s in cmp["scenarios"]) - 1) < 1e-9

    # 5. shopping: Pareto-ranked, dominated products never above their dominator
    shop = c.post("/api/v2/shopping/recommend-v3", json={"need": p["title"].split()[0], "limit": 20}).json()
    order = {r["product_id"]: i for i, r in enumerate(shop["results"])}
    for r in shop["results"]:
        if r["dominated_by"] in order:
            assert order[r["dominated_by"]] < order[r["product_id"]]

    # 6. galaxy and a knowledge-graph path whose every hop carries evidence
    assert c.get("/api/v2/markets/accept/galaxy-v3").json()["products"]
    path = c.get(f"/api/v2/graph/path?source=category:accept&target=product:{p['product_id']}").json()
    assert path["edges"] and all(e["props"].get("evidence") for e in path["edges"])

    # 7. the analyst answers in both languages from computed facts only
    en = c.post("/api/v2/analyst/ask-v3", json={"question": "How big is the accept market?", "lang": "en"}).json()
    zh = c.post("/api/v2/analyst/ask-v3", json={"question": "accept 市场有多大？", "lang": "zh"}).json()
    assert en["intent"] == zh["intent"] == "size" and en["facts"] and zh["facts"]
    assert re.search(r"[一-鿿]", zh["facts"][0]["text"]) and not re.search(r"[一-鿿]", en["facts"][0]["text"])
    assert all(f["source"].startswith("/api/v2/") for f in en["facts"])

    # 8. the tripled demand is a significant change; nothing untested or insignificant reaches anyone as an alert
    evs = c.get("/api/v2/events?market=accept&limit=500").json()
    size = [e for e in evs if e["kind"] == "market.size_change" and e["payload"].get("significance")]
    assert size and any(e["payload"]["significance"]["significant"] for e in size)
    assert all(a["event"]["severity"] != "info" for a in c.get("/api/v2/alerts").json())

    # 9. a project predicted on the v3 simulator, with validated inputs
    pr = c.post("/api/v2/projects", json={"title": p["title"], "market": "accept", "idea": {"price": p["price"]}}).json()
    assert pr["prediction"]["model"].startswith("metrics-v3") and pr["prediction"]["profit"] is None
    assert c.post("/api/v2/projects", json={"title": "ok title", "idea": {"price": 0}}).status_code == 409

    # 10. the opportunity board answers "what to sell" with the same numbers as the pages it joins
    board = [r for r in c.get("/api/v2/opportunities-v3").json()["items"] if r["market"] == "accept"]
    # ordered by the explainable opportunity score (M1: one engine); unscored rows come after scored ones
    from dip.metrics.board import board_key
    assert board and [board_key(r) for r in board] == sorted(board_key(r) for r in board)
    scored = [r["engine"]["score"] for r in board if r.get("engine") and r["engine"]["score"] is not None]
    assert scored == sorted(scored, reverse=True)
    assert all(r["growth"].get("status") == "needs_snapshots" for r in board)          # two snapshots: no trend claimed

    # 11. accuracy is measured, per market
    acc = c.get("/api/v2/accuracy").json()
    assert any(r["market"] == "accept" for r in acc["markets"])

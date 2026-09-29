"""Enterprise intelligence layer (Master Prompt 2): data confidence, product
hierarchy, trends, competitors, launch evaluation, analyst, events/alerts,
roles. Unit tests on small frames plus API tests on the synthetic
micromotor market (8 monthly snapshots, reviews, two suppliers)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_enterprise")
    mp = pytest.MonkeyPatch()
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    mp.setenv("DIP_AUTH", "off")
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


def _wait(client, job_id):
    for _ in range(240):
        job = client.get(f"/api/v2/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.5)
    raise AssertionError("job did not finish")


@pytest.fixture(scope="module")
def market(client):
    from test_engine_smoke import REVIEWS, SUPPLIERS, synthetic_dataset

    client.post("/api/v2/suppliers/import", files={"file": ("s.csv", SUPPLIERS.to_csv(index=False), "text/csv")})
    r = client.post("/api/v2/datasets",
                    files={"file": ("mm.csv", synthetic_dataset().to_csv(index=False), "text/csv"),
                           "reviews": ("rv.csv", REVIEWS.to_csv(index=False), "text/csv")},
                    data={"market": "micromotor", "marketplace": "US"})
    job = _wait(client, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    return "micromotor"


# ---------------------------------------------------------------- 1. confidence
def _listings(rows):
    base = {"title": "t", "brand": "b", "price": 10.0, "sales": 5.0, "rating": 4.5, "reviews": np.nan, "image": "u",
            "launch_date": pd.Timestamp("2024-01-01"), "data_confidence": 100.0, "relevance_status": "relevant",
            "relevance_score": 98.0, "_revenue_derived": False}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_confidence_components_caps_and_reasons():
    from dip.intelligence import confidence

    L = _listings([{"id": "A1", "product_id": "P1"}, {"id": "A2", "product_id": "P1"},
                   {"id": "B1", "product_id": "P2", "sales": np.nan, "rating": np.nan}])
    P = pd.DataFrame({"product_id": ["P1", "P2"], "monthly_revenue": [100.0, np.nan]})
    out = confidence.score_products(P, L, "sellersprite", history=None).set_index("product_id")
    caps = confidence.config()["caps"]
    # corroborated product with observed sales beats a single listing with no sales / rating
    assert out.loc["P1", "confidence_score"] > out.loc["P2", "confidence_score"]
    assert out.loc["P1", "confidence_score"] <= caps["without_history"]          # one snapshot: capped
    assert out.loc["P2", "confidence_score"] <= caps["without_sales"]            # no demand evidence: capped
    comps = json.loads(out.loc["P1", "confidence_components"])
    assert comps["historical_consistency"] is None and comps["source_reliability"] == 85.0
    reasons = {r["signal"]: r for r in json.loads(out.loc["P1", "confidence_reasons"])}
    assert reasons["listings"]["ok"] and reasons["sales"]["ok"]
    assert "review count not in source" in reasons["reviews"]["detail"]           # missing is reported, not guessed
    assert not reasons["history"]["ok"]


def test_confidence_history_consistency():
    from dip.intelligence import confidence

    L = _listings([{"id": "S", "product_id": "STABLE"}, {"id": "V", "product_id": "VOLATILE"}])
    P = pd.DataFrame({"product_id": ["STABLE", "VOLATILE"], "monthly_revenue": [1.0, 1.0]})
    periods = pd.date_range("2025-01-01", periods=4, freq="MS")
    H = pd.DataFrame({"id": ["S"] * 4 + ["V"] * 4, "period": list(periods) * 2,
                      "sales": [100, 102, 98, 101, 1, 400, 2, 5]})
    out = confidence.score_products(P, L, "sellersprite", history=H).set_index("product_id")
    cs = {k: json.loads(v)["historical_consistency"] for k, v in out["confidence_components"].items()}
    assert cs["STABLE"] == 100.0 and cs["VOLATILE"] == 0.0
    assert out.loc["STABLE", "confidence_score"] > out.loc["VOLATILE", "confidence_score"]
    no_hist = confidence.score_products(P, L, "sellersprite", history=None).set_index("product_id")
    assert out.loc["STABLE", "confidence_score"] > no_hist.loc["STABLE", "confidence_score"]  # consistent history adds confidence
    assert out.loc["VOLATILE", "confidence_score"] < no_hist.loc["VOLATILE", "confidence_score"]  # volatile history removes it


def test_confidence_rollup_is_revenue_weighted():
    from dip.intelligence import confidence

    P = pd.DataFrame({"product_id": ["a", "b"], "segment_id": ["s", "s"], "confidence_score": [90.0, 40.0],
                      "monthly_revenue": [900.0, 100.0]})
    r = confidence.rollup(P)
    assert r["confidence_score"] == 85.0 and r["high_confidence_products"] == 1 and r["low_confidence_products"] == 1
    seg = confidence.rollup(P, "segment_id")
    assert seg.loc[0, "confidence_score"] == 85.0


# ---------------------------------------------------------------- 2. hierarchy
def test_text_models_and_variants():
    from dip.pipeline.clustering.hierarchy import assign_variants, text_models

    titles = ["Giant 6X Mouth Model for Speech Therapy with Toothbrush",
              "6X Giant Mouth Model Speech Therapy Teaching Toothbrush Blue",
              "Giant Mouth Model 6X for Speech Therapy Kids Red",
              "Periodontal Disease Demonstration Model 6 Stage",
              "Periodontal Disease Model 6-Stage Teaching Demonstration",
              "Orthodontic Bracket Kit Stainless"]
    P = pd.DataFrame({"product_id": [f"p{i}" for i in range(6)], "title": titles, "brand": ["x"] * 6,
                      "segment_id": ["S0"] * 6, "model_label": ["other"] * 6, "attributes": [{}] * 6})
    out = assign_variants(text_models(P))
    giant = out.loc[:2, "model_id"].unique()
    perio = out.loc[3:4, "model_id"].unique()
    assert len(giant) == 1 and len(perio) == 1 and giant[0] != perio[0]
    assert set(out.loc[:4, "model_basis"]) == {"text"}
    assert out.loc[5, "model_label"] == "other" and out.loc[5, "model_basis"] == "none"   # a singleton is not a model
    # variant = what distinguishes the product inside its model
    assert "blue" in out.loc[1, "variant_label"] and "red" in out.loc[2, "variant_label"]
    assert (out["variant_id"] == out["product_id"]).all()


def test_spec_models_are_kept():
    from dip.pipeline.clustering.hierarchy import text_models

    P = pd.DataFrame({"product_id": ["a", "b"], "title": ["Motor 50000 rpm", "Motor 50000 rpm"], "brand": ["x", "y"],
                      "segment_id": ["S"] * 2, "model_label": ["50,000 rpm"] * 2, "attributes": [{"rpm": 50000}] * 2})
    out = text_models(P)
    assert list(out["model_basis"]) == ["spec", "spec"] and list(out["model_label"]) == ["50,000 rpm"] * 2


def test_hierarchy_api_is_complete(client, market):
    tree = client.get(f"/api/v2/markets/{market}/hierarchy").json()
    summary = client.get(f"/api/v2/markets/{market}").json()
    assert tree["kind"] == "Category" and tree["products"] == summary["category"]["products"]
    variants = [v for f in tree["children"] for s in f["children"] for m in s["children"] for v in m["children"]]
    assert len(variants) == tree["products"]
    assert {c["kind"] for c in variants[0]["children"]} == {"Listing"}
    assert all(v["label"] for v in variants)


def test_confidence_api_uses_snapshot_history(client, market):
    c = client.get(f"/api/v2/markets/{market}/confidence").json()
    assert len(c["history_periods"]) == 8                        # 8 monthly snapshots in the dataset
    assert c["market"]["confidence_level"] in {"high", "medium", "low"}
    top = c["products"][0]
    assert top["confidence_components"]["historical_consistency"] is not None
    assert any(r["signal"] == "history" and "snapshots" in r["detail"] for r in top["confidence_reasons"])
    items = client.get(f"/api/v2/markets/{market}/products").json()["items"]
    assert "confidence_score" in items[0] and "variant_label" in items[0]


# ---------------------------------------------------------------- 4. trends
def _hist(values_by_period: dict[str, list[float]], ids=None):
    rows = []
    for per, vals in values_by_period.items():
        for i, v in enumerate(vals):
            rows.append({"id": (ids or [f"L{j}" for j in range(len(vals))])[i], "period": pd.Timestamp(per),
                         "price": 10.0, "sales": v, "revenue": 10.0 * v, "reviews": np.nan})
    return pd.DataFrame(rows)


def test_trend_from_snapshot_history():
    from dip.intelligence import trends

    grow = {f"2025-{m:02d}-01": [100 * 1.08 ** m] * 6 for m in range(1, 7)}
    H = _hist(grow)
    L = pd.DataFrame({"id": [f"L{j}" for j in range(6)], "segment_id": ["S"] * 6, "price": 10.0, "sales": 100.0,
                      "revenue": 1000.0, "launch_date": pd.NaT})
    segs = pd.DataFrame({"segment_id": ["S"], "products": [6], "sales_coverage": [1.0]})
    out = trends.detect(H, L, segs, pd.Timestamp("2025-06-01")).set_index("scope")
    m = out.loc["__market__"]
    assert m["trend"] == "Growing" and m["expected_growth_12m"] == pytest.approx(1.08 ** 12 - 1, rel=0.01)
    assert m["confidence"] >= 60 and m["periods"] == 6
    shrink = _hist({f"2025-{m:02d}-01": [100 * 0.9 ** m] * 6 for m in range(1, 7)})
    assert trends.detect(shrink, L, segs, pd.Timestamp("2025-06-01")).set_index("scope").loc["__market__", "trend"] == "Declining"


def test_single_snapshot_never_claims_a_trend():
    """Claims policy (E5): a trend label needs trends.min_periods_for_label dated snapshots. From one snapshot the
    launch dates still say how many listings arrived recently -- reported as launch activity, never as a trend.
    (This test used to accept 'Growing'/'Emerging' and 'Mature'/'Stable' from one snapshot; that premise is what
    the policy removes.)"""
    from dip.intelligence import trends

    as_of = pd.Timestamp("2026-01-01")
    L = pd.DataFrame({"id": [f"L{j}" for j in range(10)], "segment_id": ["S"] * 10, "price": 10.0,
                      "sales": [50.0] * 10, "revenue": [500.0] * 10,
                      "launch_date": [as_of - pd.Timedelta(days=d) for d in [30, 60, 90, 120, 200, 250, 300, 340, 500, 900]]})
    H = L.assign(period=as_of, reviews=np.nan)[["id", "period", "price", "sales", "revenue", "reviews"]]
    segs = pd.DataFrame({"segment_id": ["S"], "products": [10], "sales_coverage": [1.0]})
    m = trends.detect(H, L, segs, as_of).set_index("scope").loc["__market__"]
    assert m["trend"] == "Insufficient evidence"                # one snapshot: no trend label at all
    assert m["launch_activity"] == "Rising"                     # 8 of 10 listings launched in the last year
    assert m["expected_growth_12m"] is None or pd.isna(m["expected_growth_12m"])  # no number from one snapshot
    assert m["confidence"] is None or pd.isna(m["confidence"])
    assert "3 dated snapshots" in m["expected_growth_basis"] and "has 1" in m["expected_growth_basis"]
    # old launches: activity is falling, and still no 'Mature' / 'Declining' claim about the market
    old = L.assign(launch_date=[as_of - pd.Timedelta(days=d) for d in [800] * 8 + [30, 400]],
                   revenue=[500.0] * 8 + [0.0, 500.0])
    m2 = trends.detect(H, old, segs, as_of).set_index("scope").loc["__market__"]
    assert m2["trend"] == "Insufficient evidence" and m2["launch_activity"] in ("Falling", "Steady")


def test_trends_api(client, market):
    t = client.get(f"/api/v2/markets/{market}/trends").json()
    assert t["market"]["trend"] == "Growing" and t["market"]["periods"] == 8
    assert t["market"]["evidence"] and all(s["segment_label"] for s in t["segments"])
    rows = client.get("/api/v2/trends").json()
    assert rows[0]["market"] == market and rows[0]["trend"] == "Growing"


# ---------------------------------------------------------------- 5. competitors
def test_competitor_profiles_and_changes():
    from dip.intelligence import competitors

    P = pd.DataFrame({"product_id": ["a", "b", "c", "d"], "brand": ["Big", "Big", "Cheap", "Small"],
                      "segment_id": ["S", "T", "S", "S"], "price": [150.0, 150.0, 70.0, 100.0],
                      "monthly_revenue": [6000.0, 2000.0, 1500.0, 500.0], "monthly_sales": [40.0, 13.0, 21.0, 5.0],
                      "rating": [4.0, 4.0, 4.6, 4.7], "listing_count": [1, 1, 1, 1]})
    S = pd.DataFrame({"segment_id": ["S", "T", "U"], "segment_label": ["s", "t", "u"], "price_median": [100.0, 100.0, 100.0]})
    as_of = pd.Timestamp("2026-01-01")
    L = pd.DataFrame({"id": ["A", "B", "C", "D"], "brand": ["Big", "Big", "Cheap", "Small"],
                      "launch_date": [pd.Timestamp("2020-01-01")] * 3 + [pd.Timestamp("2025-10-01")]})
    H = pd.DataFrame({"id": ["A", "C", "A", "C", "D"], "brand": ["Big", "Cheap", "Big", "Cheap", "Small"],
                      "period": [pd.Timestamp("2025-11-01")] * 2 + [pd.Timestamp("2025-12-01")] * 3,
                      "price": [140.0, 70.0, 150.0, 70.0, 100.0], "sales": [50.0, 10.0, 40.0, 21.0, 5.0],
                      "revenue": [np.nan] * 5, "rating": [4.0] * 5, "reviews": [np.nan] * 5})
    out = competitors.profile(P, L, S, as_of, H).set_index("brand")
    assert out.loc["Big", "position"] == "Leader" and out.loc["Big", "rank"] == 1
    assert out.loc["Big", "share"] == 0.8
    assert any("High price" in w for w in json.loads(out.loc["Big", "weaknesses"]))
    assert "Affordable alternative" in json.loads(out.loc["Big", "opportunities"])[0]
    assert any("Lower rating" in w for w in json.loads(out.loc["Big", "weaknesses"]))
    assert any("No new listings" in w for w in json.loads(out.loc["Big", "weaknesses"]))
    ch = json.loads(out.loc["Big", "changes"])
    assert ch["price_change"] == pytest.approx(150 / 140 - 1, abs=1e-3) and ch["share_change"] < 0
    assert json.loads(out.loc["Small", "changes"])["new_listings"] == ["D"]


def test_competitors_curated_table(client, market):
    # the v1 endpoints are retired (410); the curated competitors table they served is still built
    assert client.get(f"/api/v2/markets/{market}/competitors").status_code == 410
    assert client.get("/api/v2/competitors/Luxor").status_code == 410
    from dip.api.util import clean
    from dip.storage import lake

    rows = clean(lake.read_curated("competitors", market, order="share DESC"))
    assert rows and rows[0]["rank"] == 1 and rows[0]["position"] in {"Leader", "Challenger"}
    assert {"weaknesses", "opportunities", "changes"} <= set(rows[0])
    # the rejected negative-price listing never appears as a competitor's new launch
    assert all("B0BADPRICE1" not in (r["changes"] or {}).get("new_listings", []) for r in rows)


# ---------------------------------------------------------------- 3. launch evaluation
def test_launch_evaluation(client, market):
    # POST /launch/evaluate is retired (410, use /launch/simulate); the evaluation module itself is still tested
    assert client.post("/api/v2/launch/evaluate", json={"title": "x", "price": 10}).status_code == 410
    from dip.intelligence.launch import LaunchIdea, evaluate

    r = evaluate(LaunchIdea(title="Brushless micromotor 50000 rpm dental lab handpiece with cooling fan", price=340.0,
                            unit_cost=120.0, market=market))
    assert r["market"] == market and 0 <= r["market_attractiveness"] <= 100
    assert r["verdict"] in {"Attractive", "Moderate", "Unattractive"}
    assert r["expected_positioning"] in {"Budget", "Mid-range", "Mid-premium", "Premium"}
    assert "brushless" in r["segment"]["label"] or "handpiece" in r["segment"]["label"]
    assert {"demand", "competition", "differentiation", "customer_pain"} <= set(r["fit"])
    # the synthetic reviews complain about overheating -> the strategy says so, with the number
    assert any("cooling" in s.lower() and "%" in s for s in r["recommended_strategy"])
    assert r["economics"]["unit_margin"] == pytest.approx(340 * 0.85 - 120, abs=0.01)
    assert r["simulation"]["status"] == "ok" and r["suppliers"]          # two suppliers were imported
    # market chosen automatically when not given
    auto = evaluate(LaunchIdea(title="brushless dental lab micromotor", price=300))
    assert auto["market"] == market


# ---------------------------------------------------------------- 11. events, alerts, connectors
def test_change_events_route_alerts_to_owner(client, market):
    from test_engine_smoke import synthetic_dataset

    own = client.post("/api/v2/ownership", json={"employee": "Ni Zheng", "category_label": "Micromotor lab",
                                                  "market": market}).json()
    emp = own["employee_id"]
    df = synthetic_dataset()
    df.loc[df["Maker"] == "Luxor", "Cost to customer"] *= 1.25            # a competitor raises prices
    snaps = sorted(df["Snapshot"].unique())
    nova = pd.DataFrame([{"Item Code": "B0NOVA0001", "Product Name": "Nova Brushless Micromotor 50000 RPM Dental Lab Handpiece Kit",
                          "Maker": "Nova", "Cost to customer": 299.0, "Units/Month": 400, "Col_X": 299.0 * 400, "stars": 4.7,
                          "Snapshot": d, "Node": "Dental Lab Equipment"} for d in snaps])
    r = client.post("/api/v2/datasets", files={"file": ("mm2.csv", pd.concat([df, nova]).to_csv(index=False), "text/csv")},
                    data={"market": market, "marketplace": "US"})
    job = _wait(client, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    assert any(s["name"] == "events" for s in job["stages"])
    kinds = {e["kind"]: e for e in client.get(f"/api/v2/events?market={market}").json()}
    assert "dataset.processed" in kinds
    assert kinds["competitor.new_brand"]["subject"] == "Nova"
    assert kinds["competitor.price_change"]["subject"] == "Luxor"
    assert kinds["competitor.price_change"]["payload"]["change"] == pytest.approx(0.25, abs=0.01)
    alerts = client.get(f"/api/v2/alerts?employee_id={emp}").json()
    assert {a["event"]["kind"] for a in alerts} >= {"competitor.new_brand", "competitor.price_change"}
    assert all(a["event"]["severity"] != "info" for a in alerts)          # info events never become alerts
    assert client.post(f"/api/v2/alerts/{alerts[0]['id']}", json={"status": "read"}).json()["saved"]
    assert client.get(f"/api/v2/alerts?employee_id={emp}&status=read").json()[0]["id"] == alerts[0]["id"]


def test_reprocessing_unchanged_data_raises_no_change_events():
    from dip import events

    before = {"exists": True, "competitors": pd.DataFrame({"brand": ["A"], "share": [0.5], "median_price": [10.0], "position": ["Leader"]}),
              "segments": pd.DataFrame({"segment_id": ["S"], "segment_label": ["x"], "opportunity_score": [50.0]}),
              "listing_ids": {"L1"}, "trend": "Stable"}
    comp = before["competitors"].assign(products=1)
    assert events.detect_changes("m", before, comp, before["segments"],
                                 pd.DataFrame({"id": ["L1"], "title": ["t"], "brand": ["A"], "price": [10.0], "launch_date": [pd.NaT]}),
                                 "Stable", pd.Timestamp("2026-01-01")) == []


def test_inbound_events_and_connectors(client, market, tmp_path, monkeypatch):
    r = client.post("/api/v2/events", json={"kind": "news.item", "subject": "New brushless dental lab micromotor handpiece recall",
                                            "payload": {"url": "https://example.org/n1"}})
    ids = r.json()["event_ids"]
    ev = [e for e in client.get("/api/v2/events?kind=news.item").json() if e["id"] in ids]
    assert ev and ev[0]["market_name"] == market                          # matched to the market by text
    assert client.post("/api/v2/events", json={"kind": "dataset.available", "payload": {"path": "/etc/passwd"}}).status_code == 400
    conns = {c["name"]: c for c in client.get("/api/v2/connectors").json()}
    assert set(conns) == {"sellersprite_api", "amazon_sp_api", "supplier_feed", "news_feed"}
    assert client.post("/api/v2/connectors/news_feed/poll").json()["status"] == "not_configured"
    rss = tmp_path / "feed.xml"
    rss.write_text("<rss><channel><item><title>Dental lab micromotor maker expands brushless handpiece line</title>"
                   "<link>https://example.org/n2</link><description>brushless micromotor</description></item></channel></rss>")
    monkeypatch.setenv("DIP_NEWS_FEED_URL", rss.as_uri())
    assert client.post("/api/v2/connectors/news_feed/poll").json() == {"connector": "news_feed", "status": "ok", "items": 1}
    csv = tmp_path / "sup.csv"
    csv.write_text("Supplier Name,Country,Main Products\nNingbo Motor Works,China,brushless micromotor handpiece\n")
    monkeypatch.setenv("DIP_SUPPLIER_FEED_URL", csv.as_uri())
    assert client.post("/api/v2/connectors/supplier_feed/poll").json()["imported"] == 1
    assert any(s["name"] == "Ningbo Motor Works" for s in client.get("/api/v2/suppliers").json())


# ---------------------------------------------------------------- 12. roles
def test_product_manager_and_customer_roles(client, market, monkeypatch):
    from dip.auth import create_user
    from dip.pipeline import runner
    from dip.storage import business as b

    from test_engine_smoke import synthetic_dataset
    runner.process_dataset(_csv(synthetic_dataset(months=4)), "other_market", source_name="o.csv")
    emp = b.stable_id("emp", "Ni Zheng")                                   # owns 'micromotor' (previous test)
    _, pm = create_user("pm@example.com", "PM", "product_manager", employee_id=emp)
    _, cust = create_user("c@example.com", "Customer", "customer")
    _, admin = create_user("admin2@example.com", "Admin", "admin")
    monkeypatch.setenv("DIP_AUTH", "on")
    H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    names = lambda t: {m["name"] for m in client.get("/api/v2/markets", headers=H(t)).json()}  # noqa: E731
    assert names(admin) >= {market, "other_market"}
    assert names(pm) == {market}
    assert client.get(f"/api/v2/markets/{market}/trends", headers=H(pm)).status_code == 200
    assert client.get("/api/v2/markets/other_market", headers=H(pm)).status_code == 403
    assert client.get("/api/v2/markets/other_market/competitors-v3", headers=H(pm)).status_code == 403
    assert {r["market"] for r in client.get("/api/v2/trends", headers=H(pm)).json()} == {market}
    assert client.get("/api/v2/auth/me", headers=H(pm)).json()["markets"] == [market]
    # customers: shopping mode only
    assert client.get("/api/v2/markets", headers=H(cust)).status_code == 403
    assert client.post("/api/v2/launch/simulate", json={"title": "x", "price": 10}, headers=H(cust)).status_code == 403
    assert client.post("/api/v2/shopping/recommend-v3", json={"need": "brushless micromotor", "budget_max": 500},
                       headers=H(cust)).status_code == 200


def _csv(df):
    import tempfile

    p = Path(tempfile.mkdtemp()) / "data.csv"
    df.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------- 10. incremental processing and caching
def test_identical_upload_is_skipped_and_force_reruns(client, market):
    from test_engine_smoke import synthetic_dataset

    body = synthetic_dataset(months=3).to_csv(index=False)
    runs = []
    for force in (False, False, True):
        r = client.post("/api/v2/datasets", files={"file": ("same.csv", body, "text/csv")},
                        data={"market": "skip_market", "force": str(force).lower()})
        runs.append(_wait(client, r.json()["job_id"]))
    assert runs[0]["stages"][0]["name"] == "ingestion"
    assert [s["name"] for s in runs[1]["stages"]] == ["skipped"]                 # nothing recomputed
    assert runs[2]["stages"][0]["name"] == "ingestion"                           # force re-runs
    rel = next(s for s in runs[2]["stages"] if s["name"] == "relevance")["summary"]
    assert rel["cache"]["cached"] == rel["cache"]["texts"] > 0                   # every text scored before


def test_relevance_cache_gives_identical_results(tmp_path, monkeypatch):
    from dip.pipeline import relevance
    from dip import settings

    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    settings.get_settings.cache_clear()
    try:
        from test_engine_smoke import synthetic_dataset
        from dip.pipeline.ingestion import ingest_dataset
        p = _csv(synthetic_dataset(months=2))
        frame = ingest_dataset(p, "m", "d1").frame
        cold = relevance.classify(frame, None, None, "dental", use_cache=True)
        assert relevance.CACHE_STATS["cached"] == 0
        warm = relevance.classify(frame, None, None, "dental", use_cache=True)
        assert relevance.CACHE_STATS["cached"] == relevance.CACHE_STATS["texts"]
        plain = relevance.classify(frame, None, None, "dental", use_cache=False)
        for c in ("relevance_score", "relevance_status", "relevance_evidence", "is_relevant"):
            assert (cold[c] == warm[c]).all() and (plain[c] == warm[c]).all()
        # a human correction changes the model fingerprint -> fresh cache, no stale scores
        fb = pd.DataFrame({"text": ["acme brushless micromotor"], "label": [True]})
        relevance.classify(frame, fb, None, "dental", use_cache=True)
        assert relevance.CACHE_STATS["cached"] == 0
    finally:
        settings.get_settings.cache_clear()


def test_response_cache_follows_data_version(client, market):
    from dip import cache

    cache.clear()
    a = client.get("/api/v2/trends").json()
    v1 = cache.data_version()
    assert client.get("/api/v2/trends").json() == a
    client.post("/api/v2/events", json={"kind": "external.signal", "market": market, "subject": "x", "severity": "info"})
    assert cache.data_version() != v1                                            # any new data invalidates


def test_batch_folder_uses_snapshot_dates(tmp_path):
    from dip.pipeline.runner import snapshot_date_from_name

    assert snapshot_date_from_name("micromotor_2025-03.xlsx") == "2025-03-01"
    assert snapshot_date_from_name("mm_20250315.csv") == "2025-03-15"
    assert snapshot_date_from_name("dental_models_sellersprite_v1.xlsx") is None


# ---------------------------------------------------------------- 8. employee intelligence
def test_market_brief_and_employee_focus(client, market):
    br = client.get(f"/api/v2/markets/{market}/brief").json()
    assert br["market_size"]["monthly_revenue"] > 0 and br["products"] > 0
    assert br["opportunity"]["level"] in {"High", "Medium", "Low"}
    assert br["suggested_development"] and br["competitors"] and br["trend"]["trend"] == "Growing"
    from dip.storage import business as b
    emp = b.stable_id("emp", "Ni Zheng")
    f = client.get(f"/api/v2/employees/{emp}/focus").json()
    assert f["name"] == "Ni Zheng" and market in [m["market"] for m in f["markets"]]
    acts = f["recommended_actions"]
    assert acts and all(a["reason"] for a in acts)
    prio = [a["priority"] for a in acts]
    assert prio == sorted(prio, key=["high", "medium", "low"].index)
    # every high-opportunity segment among the market's top 3 becomes a launch evaluation, and nothing else does;
    # since M1 the top 3 are ranked by the explainable opportunity score (knowledge layer), not the older index
    opp = client.get(f"/api/v2/markets/{market}/opportunities/explained", params={"scope": "segment"}).json()["rows"]
    top3 = sorted([o for o in opp if o["opportunity_score"] is not None], key=lambda o: -o["opportunity_score"])[:3]
    expected = {o["label"] for o in top3 if o["opportunity_score"] >= 55}
    got = {a["action"].split("'")[1] for a in acts if a["action"].startswith("Evaluate a launch") and a["market"] == market}
    assert got == expected
    assert any(a["action"].startswith(f"Upload next month's {market}") for a in acts) is False  # 8 snapshots already
    assert client.get("/api/v2/employees/nobody/focus").status_code == 404


# ---------------------------------------------------------------- 6. analyst agent
def test_analyst_intents():
    from dip.intelligence.analyst import intent_of

    assert intent_of("Show me opportunities in dental laboratory equipment.") == "opportunities"
    assert intent_of("Which markets are growing?") == "growth"
    assert intent_of("Who are our competitors?") == "competitors"
    assert intent_of("Who can manufacture this product?") == "suppliers"
    assert intent_of("What happens if we launch a scaler at $99?") == "launch"
    assert intent_of("What should Ni Zheng focus on?") == "employee"
    assert intent_of("How big is the micromotor market?") == "size"


def test_analyst_answers_from_the_database(client, market, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # POST /analyst/ask is retired (410, use /analyst/ask-v3); the v1 analyst module is exercised directly
    from dip.intelligence.analyst import ask as analyst_ask
    from dip.storage import business as b

    with b.session() as s:
        names = [m.name for m in s.query(b.Market).all()]
    ask = lambda q, **kw: analyst_ask(q, names, kw.get("use_ai", False))  # noqa: E731
    g = ask("Which markets are growing?")
    mk = [i for i in g["items"] if "segment" not in i]
    assert g["intent"] == "growth" and {i["market"]: i["trend"] for i in mk}[market] == "Growing"
    order = ["Growing", "Emerging", "Stable", "Mature", "Declining", None, "Insufficient evidence"]
    ranks = [order.index(i["trend"]) if i["trend"] in order else len(order) for i in mk]
    assert ranks[0] == 0                                             # growing markets are listed first
    o = ask("What are the best opportunities in the micromotor market?")
    assert o["intent"] == "opportunities" and o["scope"]["markets"] == [market]
    assert o["items"][0]["reasons"] and "/100" in o["answer"] and o["sources"]
    c = ask("Tell me about Luxor")
    assert c["intent"] == "competitors" and c["scope"]["brand"] == "Luxor" and c["items"][0]["brand"] == "Luxor"
    s = ask("Who can manufacture a brushless micromotor?")
    assert s["intent"] == "suppliers" and s["items"][0]["name"] in {"Shenzhen Dental Motor Co", "Ningbo Motor Works"}
    ln = ask("What happens if we launch a brushless dental lab micromotor at $330?")
    assert ln["intent"] == "launch" and "Market attractiveness" in ln["answer"] and ln["items"][0]["market"] == market
    e = ask("What should Ni Zheng focus on?")
    assert e["intent"] == "employee" and "Ni Zheng" in e["answer"]
    # AI requested but not configured: offline answer kept, the attempt is traced
    a = ask("Which markets are growing?", use_ai=True)
    assert a["mode"] == "offline" and a["ai_status"] == "unavailable" and a["answer"]
    tr = client.get("/api/v2/ai/traces").json()[0]
    assert tr["purpose"] == "analyst.answer" and tr["status"] == "unavailable" and tr["model"] and tr["prompt_version"]
    assert client.post("/api/v2/analyst/ask", json={"question": "x"}).status_code == 410


# ---------------------------------------------------------------- 9. shopping map
def test_v1_shopping_is_retired(client, market):
    # the v1 recommender + map were removed; /shopping/recommend-v3 is tested in test_metrics_engine.py
    r = client.post("/api/v2/shopping/recommend", json={"need": "brushless micromotor"})
    assert r.status_code == 410 and r.json()["detail"]["use_instead"] == "POST /api/v2/shopping/recommend-v3"

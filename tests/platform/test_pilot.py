"""Pilot tooling (Master Prompt 4): data inventory, stable product IDs across snapshots,
stratified labelling samples, accuracy metrics from labels, coverage sensitivity, backtest,
feedback and usage."""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_pilot")
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


def _wait(c, job_id):
    for _ in range(240):
        j = c.get(f"/api/v2/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def _upload(c, df, market, name, snapshot_date):
    r = c.post("/api/v2/datasets", files={"file": (name, df.to_csv(index=False), "text/csv")},
               data={"market": market, "snapshot_date": snapshot_date})
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    return j


def _last_month(df):
    return df[df["Snapshot"] == df["Snapshot"].max()].drop(columns=["Snapshot"])


@pytest.fixture(scope="module")
def two_snapshots(env):
    """Snapshot 1 lacks the duplicate listing B0DUPLIC8X; in snapshot 2 it joins the Zeno product."""
    from test_engine_smoke import synthetic_dataset

    c, _, _ = env
    full = _last_month(synthetic_dataset(months=3))
    noise = pd.DataFrame([{"Item Code": f"B0JEWEL{i:03d}", "Product Name": t, "Maker": "Glam", "Cost to customer": 19.99,
                           "Units/Month": 40, "Col_X": 799.6, "stars": 4.2, "Node": "Jewelry"}
                          for i, t in enumerate(["Sterling Silver Tooth Pendant Necklace Jewelry", "Tooth Fairy Earrings Jewelry Gift",
                                                 "Dog Toy Chew Bone Pet Dental Toy"])])
    full = pd.concat([full, noise], ignore_index=True)
    first = full[full["Item Code"] != "B0DUPLIC8X"]
    _upload(c, first, "mm", "mm_2025-02.csv", "2025-02-01")
    from dip.storage import lake
    before = lake.read_curated("listings", "mm")[["id", "product_id"]]
    _upload(c, full.assign(**{"Units/Month": full["Units/Month"] * 1.1}), "mm", "mm_2025-03.csv", "2025-03-01")
    after = lake.read_curated("listings", "mm")[["id", "product_id"]]
    return before, after


def test_product_ids_survive_a_new_listing_joining(env, two_snapshots):
    before, after = two_snapshots
    zeno_before = before.set_index("id")["product_id"]
    joined = after[after["id"] == "B0DUPLIC8X"]
    assert len(joined) == 1, "the duplicate listing must be resolved into an existing product"
    pid_now = joined["product_id"].iloc[0]
    partners = after[(after["product_id"] == pid_now) & (after["id"] != "B0DUPLIC8X")]["id"]
    assert len(partners) >= 1
    assert all(zeno_before[i] == pid_now for i in partners)          # same ID as before the listing joined
    common = after.set_index("id")["product_id"].reindex(zeno_before.index).dropna()
    assert (common == zeno_before.loc[common.index]).all()            # no product changed ID


def test_stabilize_unit_split_and_new():
    from dip.pipeline.product_resolution.stable_ids import stabilize

    prev = pd.DataFrame({"id": ["a", "b", "c", "d", "e"], "product_id": ["P1", "P1", "P1", "P2", "P3"]})
    # P1 splits into {a,b} and {c}; P2 unchanged but hashed differently; P3 gone; f is new
    frame = pd.DataFrame({"id": ["a", "b", "c", "d", "f"], "product_id": ["H1", "H1", "H2", "H3", "H4"]})
    products = pd.DataFrame({"product_id": ["H1", "H2", "H3", "H4"]})
    f2, p2, st = stabilize(frame, products, prev)
    m = dict(zip(f2["id"], f2["product_id"]))
    assert m["a"] == m["b"] == "P1"            # larger part of the split keeps the ID
    assert m["c"] == "H2"                       # smaller part gets a new ID
    assert m["d"] == "P2" and m["f"] == "H4"
    assert set(p2["product_id"]) == {"P1", "H2", "P2", "H4"}
    assert st["previous_products_split"] == 1 and st["products_carried"] == 2


# ---------------------------------------------------------------- inventory
def test_inventory_flags_byte_identical_files(tmp_path):
    from dip.pilot.inventory import raw_files

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.csv").write_text("id,title\n1,t\n")
    (tmp_path / "b" / "y.csv").write_text("id,title\n1,t\n")
    (tmp_path / "b" / "z.csv").write_text("id,title\n2,u\n")
    df = raw_files(tmp_path).set_index("file")
    assert df.loc["b/y.csv", "byte_identical_to"] == "a/x.csv"
    assert df.loc["b/z.csv", "byte_identical_to"] == "" and df.loc["a/x.csv", "byte_identical_to"] == ""


# ---------------------------------------------------------------- labelling + metrics
def test_sample_is_stratified_and_reproducible(env, two_snapshots):
    from dip.pilot.labels import draw_sample, items

    a = draw_sample("mm", n=12, n_excluded=3, seed=11)
    b_ = draw_sample("mm", n=12, n_excluded=3, seed=11)
    ia, ib = items(a["id"]), items(b_["id"])
    assert [x["ref_id"] for x in ia] == [x["ref_id"] for x in ib]            # same seed, same sample
    d = a["design"]
    assert sum(v["drawn"] for v in d["strata"].values()) == d["n_products"] == len([x for x in ia if x["kind"] == "product"])
    assert all(v["drawn"] <= v["size"] for v in d["strata"].values())
    assert d["n_excluded"] == len([x for x in ia if x["kind"] == "excluded_listing"]) > 0   # noise rows are excluded


def test_metrics_from_known_labels(env, two_snapshots):
    from dip.pilot.labels import draw_sample, items, save_label
    from dip.pilot.metrics import evaluate

    smp = draw_sample("mm", n=10, n_excluded=2, seed=3)
    its = items(smp["id"])
    prods = [x for x in its if x["kind"] == "product"]
    multi = next(x for x in prods if len(x["snapshot"]["listings"]) >= 2)
    n_listings = sum(len(x["snapshot"]["listings"]) for x in prods)
    for x in prods:
        ids = [y["id"] for y in x["snapshot"]["listings"]]
        bad = [ids[0]] if x is multi else []
        save_label(x["id"], "relevance", {"irrelevant_listings": bad}, labeller="t")
        save_label(x["id"], "entity", {"wrong_listings": bad, "missing_listings": []}, labeller="t")
        save_label(x["id"], "segment", {}, correct=x is not prods[0], labeller="t")
    ex = [x for x in its if x["kind"] == "excluded_listing"]
    save_label(ex[0]["id"], "relevance", {"is_relevant": True}, labeller="t")        # the system wrongly dropped one
    for x in ex[1:]:
        save_label(x["id"], "relevance", {"is_relevant": False}, labeller="t")
    e = evaluate(smp["id"], "snapshot")
    assert e["relevance"]["precision"]["value"] == pytest.approx((n_listings - 1) / n_listings)
    assert 0 < e["relevance"]["recall"]["value"] < 1
    k = len(multi["snapshot"]["listings"])
    assert e["entity"]["pairs"]["fp"] == k - 1                                     # the wrong listing paired with each other
    assert e["entity"]["products_exactly_right"]["value"] == pytest.approx((len(prods) - 1) / len(prods))
    assert e["segment"]["value"] == pytest.approx((len(prods) - 1) / len(prods))
    assert e["segment"]["low"] < e["segment"]["value"] < e["segment"]["high"] or e["segment"]["value"] == 1
    # relevance labels become relevance feedback for the pipeline
    from dip.settings import get_settings
    from dmie.engine import store as core_store
    con = core_store.connect(get_settings().analytics_path)
    try:
        fb = core_store.read_feedback(con, "dental")
    finally:
        con.close()
    assert len(fb) >= n_listings
    with pytest.raises(ValueError):
        save_label(prods[0]["id"], "entity", {"wrong_listings": ["NOT-IN-PRODUCT"]}, labeller="t")


def test_labelling_api_and_sheet_roundtrip(env, two_snapshots):
    c, _, _ = env
    smp = c.post("/api/v2/labels/samples", json={"market": "mm", "n": 6, "n_excluded": 1, "seed": 5}).json()
    its = c.get(f"/api/v2/labels/samples/{smp['id']}/items").json()
    first = next(x for x in its if x["kind"] == "product")
    assert c.post(f"/api/v2/labels/items/{first['id']}", json={"check": "best_listing", "correct": True}).status_code == 200
    assert c.post(f"/api/v2/labels/items/{first['id']}", json={"check": "nonsense"}).status_code == 409
    sheet = pd.read_csv(io.StringIO(c.get(f"/api/v2/labels/samples/{smp['id']}/sheet").text), dtype=str)
    sheet.loc[sheet["kind"] == "product", "segment_correct"] = "yes"
    sheet.loc[sheet["kind"] == "product", "relevance_irrelevant_listings"] = "none"
    sheet.loc[sheet["kind"] == "excluded_listing", "excluded_is_relevant"] = "no"
    r = c.post(f"/api/v2/labels/samples/{smp['id']}/sheet", files={"file": ("s.csv", sheet.to_csv(index=False), "text/csv")})
    assert r.status_code == 200 and r.json()["labels_saved"] == 2 * (sheet["kind"] == "product").sum() + 1
    m = c.get(f"/api/v2/labels/samples/{smp['id']}/metrics").json()
    assert m["segment"]["value"] == 1 and m["relevance"]["precision"]["value"] == 1
    cur = c.get(f"/api/v2/labels/samples/{smp['id']}/metrics?against=current").json()
    assert cur["against"] == "current" and cur["segment"]["n"] + cur["unverified_after_change"]["segment"] == m["segment"]["n"]


# ---------------------------------------------------------------- sensitivity + backtests
def test_coverage_sensitivity(env, two_snapshots):
    from dip.pilot.sensitivity import analyse, to_markdown

    r = analyse("mm")
    assert r["status"] == "ok"
    mr = r["market_revenue"]
    assert mr["observed"] <= mr["p25"] <= mr["p50"] <= mr["p75"]
    assert "Sales-coverage sensitivity" in to_markdown([r])


def test_backtests_on_dated_history(env):
    from test_engine_smoke import synthetic_dataset

    from dip.pilot.backtest import run, to_markdown

    c, _, _ = env
    r = c.post("/api/v2/datasets", files={"file": ("ts.csv", synthetic_dataset(months=8).to_csv(index=False), "text/csv")},
               data={"market": "ts"})
    assert _wait(c, r.json()["job_id"])["status"] == "done"
    bt = run("ts")
    f = bt["forecasts"]
    assert f["status"] == "ok" and f["points"] > 0 and f["wape"] is not None
    assert f["direction_accuracy"]["n"] > 0
    assert bt["alerts"]["status"] == "ok"
    assert bt["launch"]["status"] in ("ok", "insufficient_data", "no_launch_dates", "no_data")
    one = run("mm")["forecasts"]
    assert one["status"] == "insufficient_history" and one["needed"] >= 4        # never extrapolated
    assert "Backtest report" in to_markdown([bt])


# ---------------------------------------------------------------- feedback + usage
def test_feedback_and_usage(env, two_snapshots):
    c, _, _ = env
    fb = c.post("/api/v2/feedback", json={"target_kind": "market", "target_id": "mm", "market": "mm", "field": "monthly_revenue",
                                          "shown_value": "$1", "comment": "Too low", "page": "/markets/mm"}).json()
    assert fb["status"] == "new"
    assert c.post("/api/v2/feedback", json={"target_kind": "market", "comment": " "}).status_code == 409
    assert c.post(f"/api/v2/feedback/{fb['id']}", json={"triage": "bogus"}).status_code == 409
    t = c.post(f"/api/v2/feedback/{fb['id']}", json={"triage": "data_bug", "resolution": "coverage note"}).json()
    assert t["status"] == "triaged" and t["triage"] == "data_bug"
    assert any(x["id"] == fb["id"] for x in c.get("/api/v2/feedback").json())
    for path in ("/markets/mm", "/markets/mm", "/products/P123abc?x=1"):
        c.post("/api/v2/telemetry/view", json={"path": path})
    u = c.get("/api/v2/usage/pages").json()
    pages = {p["page"]: p["views"] for p in u["pages"]}
    assert pages["/markets/mm"] == 2 and pages["/products/:id"] == 1 and u["views"] == 3

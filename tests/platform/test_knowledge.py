"""Product knowledge layer: unit normalisation, attribute extraction, component role, Dental Confidence,
provenance (observations with value kinds + evidence) and the "why" API."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))

from dip.knowledge import applications, attributes, provenance, units  # noqa: E402


# ---------------------------------------------------------------- units (spec 67)
@pytest.mark.parametrize("text,quantity,value,unit", [
    ("50,000 rpm", "rpm", 50000, "rpm"), ("50000 RPM", "rpm", 50000, "rpm"), ("50k RPM", "rpm", 50000, "rpm"),
    ("50 KRPM", "rpm", 50000, "rpm"), ("5万转", "rpm", 50000, "rpm"), ("2.8 N.cm", "torque", 2.8, "N·cm"),
    ("0.028 N·m", "torque", 2.8, "N·cm"), ("1.2 kg", "mass", 1200, "g"), ("6.86 cm", "length", 68.6, "mm"),
    ("pack of 50", "count", 50, "count"), ("65W", "watt", 65, "W"), ("110V", "volt", 110, "V"),
])
def test_units_normalise_to_canonical(text, quantity, value, unit):
    q = [x for x in units.find(text) if x.quantity == quantity]
    assert q and q[0].unit == unit and q[0].value == pytest.approx(value)
    assert text[q[0].start:q[0].end] == q[0].raw          # the span points at the source words


def test_units_keep_every_voltage_and_pick_max_rpm():
    t = "110V/220V micromotor 35000 rpm, max 50k RPM"
    assert [x.value for x in units.find(t) if x.quantity == "volt"] == [110, 220]
    assert units.best(t, "rpm").value == 50000
    assert units.best("speed 999999 rpm", "rpm", (1000, 200000)) is None     # implausible value rejected


# ---------------------------------------------------------------- attributes + role (spec 10, 19, 101)
def test_micromotor_configuration_attributes_and_key():
    r = attributes.extract_row({"title": "Brushless Dental Lab Micromotor 50,000 RPM N3 Control Box with H37L1 Handpiece"},
                               "micromotor")
    a = r["attributes"]
    assert a["technology"]["value"] == "brushless" and a["max_rpm"]["value"] == 50000
    assert a["control_unit"]["value"] == "N3" and a["handpiece"]["value"] == "H37L1"
    assert a["max_rpm"]["span"] == "50,000 RPM" and a["max_rpm"]["field"] == "title"
    assert r["role"] == "configuration" and r["configuration_key"] == "control_unit=N3|handpiece=H37L1"


def test_configurations_differ_by_component():
    a = attributes.extract_row({"title": "Marathon N3 micromotor with H37L1 handpiece"}, "micromotor")
    b = attributes.extract_row({"title": "Marathon N3 micromotor with 102L handpiece"}, "micromotor")
    assert a["configuration_key"] != b["configuration_key"]          # spec 105: N3+H37L1 is not N3+102L


def test_roles_accessory_bundle_base():
    assert attributes.extract_row({"title": "Replacement carbon brush for N3 micromotor"}, "micromotor")["role"] == "accessory"
    assert attributes.extract_row({"title": "Denture repair bundle kit with free cleaning tablets"}, "denture_base")["role"] == "bundle"
    assert attributes.extract_row({"title": "Denture Base Resin powder"}, "denture_base")["role"] == "base"


def test_conflicting_enum_values_are_reported_not_resolved():
    r = attributes.extract_row({"title": "Soft and hard denture reline kit"}, "denture_base")
    h = r["attributes"]["hardness"]
    assert h.get("conflict") and set([h["value"], *h["alternatives"]]) == {"soft", "hard"}
    assert "hardness" in r["conflicts"]


def test_unknown_market_uses_generic_schema_and_flags_llm():
    r = attributes.extract_row({"title": "Something unrelated"}, "no_such_market")
    assert attributes.schema_name("no_such_market") == "generic" and r["needs_llm"] is True and r["role"] == "base"


# ---------------------------------------------------------------- Dental Confidence (spec 11, 13.1)
def test_dental_confidence_bands_and_applications():
    dental = applications.score_row({"title": "Dental lab micromotor handpiece for crown polishing", "relevance_score": 95})
    assert dental["dental_band"] == "strong" and dental["dental_confidence"] >= 85
    assert "dental_laboratory" in dental["applications"] and "prosthetics" in dental["applications"]
    nondental = applications.score_row({"title": "Nail drill for manicure and pedicure, jewelry engraving", "relevance_score": 5})
    assert nondental["dental_band"] in ("non_dental", "probably_non_dental")
    assert nondental["primary_application"] == "non_dental"


def test_missing_evidence_is_not_non_dental():
    r = applications.score_row({"title": "Model XK-200 unit"})       # no vocabulary, no model score
    assert r["dental_confidence"] is None and r["dental_band"] == "review" and r["primary_application"] == "ambiguous"


def test_one_component_is_capped():
    r = applications.score_row({"title": "dental teeth tooth"})   # text only (no application term)
    assert r["dental_confidence"] <= 80                                 # cap_by_components {1: 80}


def test_human_decision_wins():
    r = applications.score_row({"title": "nail drill manicure", "relevance_status": "human_relevant"})
    assert r["dental_band"] == "verified_dental" and r["dental_verified"] is True


# ---------------------------------------------------------------- provenance (spec 14, 80)
def test_observation_kinds_follow_how_values_were_produced():
    L = pd.DataFrame({"id": ["a"], "record_id": ["r1"], "price": [10.0], "rating": [4.5], "sales": [100.0], "revenue": [1000.0],
                      "units_est": [120.0], "units_lo": [100.0], "units_hi": [150.0], "revenue_est": [1200.0], "data_confidence": [80]})
    P = pd.DataFrame({"product_id": ["p"], "units_est": [120.0], "units_lo": [100.0], "units_hi": [150.0], "listing_count": [1]})
    S = pd.DataFrame({"segment_id": ["s"], "revenue_est": [1200.0], "revenue_lo": [1000.0], "revenue_hi": [1500.0]})
    o = provenance.observations(L, P, S, {"revenue_month": {"estimate": 1200, "low": 1000, "high": 1500}}, "m", "sellersprite", "d1", "2026-09-01")
    k = {(r.entity_type, r.metric): r.kind for r in o.itertuples()}
    assert k[("listing", "price")] == "observed" and k[("listing", "sales")] == "estimated"   # spec 92: SellerSprite = estimate
    assert k[("listing", "units_est")] == "modeled" and k[("product", "listings")] == "derived"
    assert k[("category", "revenue_est")] == "modeled"
    u = o[(o.entity_type == "listing") & (o.metric == "units_est")].iloc[0]
    assert (u.lo, u.hi, u.source_record_id) == (100.0, 150.0, "r1")


def test_history_appends_and_replaces_same_dataset():
    cur = pd.DataFrame([{**{c: None for c in provenance.OBS_COLUMNS}, "entity_type": "product", "entity_id": "p",
                         "metric": "units_est", "value": 1.0, "dataset_id": "d1"}])
    h1 = provenance.append_history(None, cur)
    h2 = provenance.append_history(h1, cur.assign(dataset_id="d2", value=2.0))
    h3 = provenance.append_history(h2, cur.assign(dataset_id="d2", value=3.0))     # re-run of d2 replaces it
    assert h2["value"].tolist() == [1.0, 2.0] and h3["value"].tolist() == [1.0, 3.0]


# ---------------------------------------------------------------- pipeline + API
MARKET = "micromotor"


@pytest.fixture(scope="module")
def c(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_knowledge")
    mp = pytest.MonkeyPatch()
    mp.setenv("DIP_DATA_DIR", str(d / "platform"))
    mp.setenv("DIP_LAKE_DIR", str(d / "lake"))
    mp.setenv("DIP_AUTH", "off")
    mp.setenv("DIP_FORECAST_WORKERS", "1")
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
    from dip.pipeline import runner
    p = d / "m.csv"
    synthetic_dataset(months=3).to_csv(p, index=False)
    runner.process_dataset(p, MARKET, source_name="m.csv")
    yield TestClient(app)
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def test_pipeline_writes_knowledge_and_provenance(c):
    s = c.get(f"/api/v2/markets/{MARKET}/knowledge").json()
    assert s["schema_name"] == "micromotor" and s["records"] > 0
    assert sum(s["dental_band"].values()) == s["records"]                  # every record classified (spec 168 A)
    assert {"observed", "modeled", "derived"} <= set(s["observations_by_kind"])
    rows = c.get(f"/api/v2/markets/{MARKET}/knowledge/records", params={"limit": 500}).json()
    assert rows["total"] == s["records"]
    brushless = [r for r in rows["rows"] if (r["attributes"].get("technology") or {}).get("value") == "brushless"]
    assert brushless and all(r["attributes"]["max_rpm"]["value"] >= 1000 for r in brushless if "max_rpm" in r["attributes"])


def test_why_returns_observations_history_and_evidence(c):
    from dip.storage import lake
    lid = str(lake.read_curated("listings", MARKET, columns=["id"])["id"].iat[0])
    w = c.get(f"/api/v2/markets/{MARKET}/why", params={"entity_type": "listing", "entity_id": lid}).json()
    kinds = {o["metric"]: o["kind"] for o in w["current"]}
    assert kinds.get("price") == "observed" and kinds.get("units_est") == "modeled"
    assert w["evidence"] and all(e["entity_id"] == lid for e in w["evidence"])
    assert any(h["metric"] == "price" for h in w["history"])
    pid = str(lake.read_curated("products", MARKET, columns=["product_id"])["product_id"].iat[0])
    wp = c.get(f"/api/v2/markets/{MARKET}/why", params={"entity_type": "product", "entity_id": pid, "metric": "units_est"}).json()
    assert wp["current"] and wp["current"][0]["kind"] == "modeled"
    assert c.get(f"/api/v2/markets/{MARKET}/why", params={"entity_type": "listing", "entity_id": "nope"}).status_code == 404
    assert c.get(f"/api/v2/markets/{MARKET}/why", params={"entity_type": "bogus", "entity_id": "x"}).status_code == 422


def test_products_carry_rolled_up_knowledge(c):
    from dip.storage import lake
    p = lake.read_curated("products", MARKET)
    assert {"dental_confidence", "dental_band", "primary_application", "kn_attributes", "attribute_agreement"} <= set(p.columns)
    agree = [json.loads(x) for x in p["attribute_agreement"].dropna()]
    assert agree and all(0 < v <= 1 for a in agree for v in a.values())


# ---------------------------------------------------------------- identity (spec 19 stage 5, 95-96, 132, 147)
from dip.knowledge import identity  # noqa: E402


def _frame(rows):
    base = {"brand": "Acme", "price": 100.0, "sales": None, "reviews": None, "rating": None, "seller": None, "image": None,
            "url": None, "revenue": None, "segment_id": "s1", "segment_label": "micromotors", "family_id": "f1", "data_confidence": 80}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_configuration_split_separates_component_sets():
    f = _frame([{"id": "a", "product_id": "P1", "title": "N3 micromotor H37L1"},
                {"id": "b", "product_id": "P1", "title": "N3 micromotor H37L1 handpiece"},
                {"id": "c", "product_id": "P1", "title": "N3 micromotor 102L"},
                {"id": "d", "product_id": "P1", "title": "N3 micromotor"}])
    keys = pd.Series(["control_unit=N3|handpiece=H37L1", "control_unit=N3|handpiece=H37L1", "control_unit=N3|handpiece=102L", None])
    out, changes = identity.regroup(f, keys)
    pid = dict(zip(out["id"], out["product_id"]))
    assert pid["a"] == pid["b"] != pid["c"]
    assert pid["d"] == pid["a"] and out.set_index("id").at["d", "configuration_uncertain"]    # unknown -> majority config, flagged
    assert changes and changes[0]["change"] == "configuration_split"
    frame, products = identity.rebuild(out)
    assert len(products) == 2 and set(products["listing_count"]) == {3, 1}
    assert frame["is_best_listing"].sum() == 2


def test_human_merge_and_keep_separate():
    f = _frame([{"id": "a", "product_id": "P1", "title": "x"}, {"id": "b", "product_id": "P2", "title": "y"},
                {"id": "c", "product_id": "P3", "title": "z"}, {"id": "e", "product_id": "P3", "title": "z2"}])
    dec = pd.DataFrame([{"listing_a": "a", "listing_b": "b", "decision": "merge"},
                        {"listing_a": "c", "listing_b": "e", "decision": "keep_separate"}])
    out, changes = identity.regroup(f, pd.Series([None] * 4), dec)
    pid = dict(zip(out["id"], out["product_id"]))
    assert pid["a"] == pid["b"] and pid["c"] != pid["e"]
    assert {c["change"] for c in changes} == {"human_merge", "human_keep_separate"}
    assert pid["a"] == identity._pid(["a", "b"])          # resolver's id formula kept


def test_identity_confidence_components():
    f = _frame([{"id": "a", "product_id": "P1", "title": "t", "specs": {"rpm": 50000, "model_tokens": ["N3"]}},
                {"id": "b", "product_id": "P1", "title": "t", "specs": {"rpm": 50000, "model_tokens": ["N3"]}},
                {"id": "c", "product_id": "P2", "title": "u", "specs": {}}])
    edges = pd.DataFrame({"listing_a": ["a"], "listing_b": ["b"], "score": [0.9], "decision": ["match"]})
    ic = identity.identity_confidence(f, edges).set_index("product_id")
    comps = json.loads(ic.at["P1", "identity_components"])
    assert comps == {"model_number": 1.0, "specification": 1.0, "match_score": 0.9, "brand": 1.0}
    assert ic.at["P1", "identity_confidence"] == 97.5 and pd.isna(ic.at["P2", "identity_confidence"])


def test_evaluation_precision_recall():
    edges = pd.DataFrame({"listing_a": ["a", "c", "e"], "listing_b": ["b", "d", "f"], "decision": ["match", "match", "no_match"], "score": [.9, .8, .5]})
    dec = pd.DataFrame({"listing_a": ["b", "c", "e", "g"], "listing_b": ["a", "d", "f", "h"],
                        "decision": ["merge", "keep_separate", "merge", "needs_evidence"]})
    ev = identity.evaluate(edges, dec)
    assert (ev["tp"], ev["fp"], ev["fn"], ev["labelled_pairs"]) == (1, 1, 1, 3)
    assert ev["precision"] == 0.5 and ev["recall"] == 0.5


def test_duplicate_decision_api_roundtrip(c):
    from dip.storage import lake
    ids = lake.read_curated("listings", MARKET, columns=["id", "product_id"]).drop_duplicates("product_id")
    a, b = str(ids["id"].iat[0]), str(ids["id"].iat[1])
    assert c.post(f"/api/v2/markets/{MARKET}/duplicates/decision", json={"listing_a": a, "listing_b": b, "decision": "bogus"}).status_code == 422
    r = c.post(f"/api/v2/markets/{MARKET}/duplicates/decision", json={"listing_a": a, "listing_b": b, "decision": "merge", "reason": "same model"})
    assert r.status_code == 200 and r.json()["saved"]
    ev = c.get(f"/api/v2/markets/{MARKET}/identity/evaluation").json()
    assert ev["labelled_pairs"] == 1
    assert c.get(f"/api/v2/markets/{MARKET}/duplicates").status_code == 200
    # the decision is applied on the next run: both listings end in one product
    from dip.pipeline import runner
    from test_engine_smoke import synthetic_dataset
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m2.csv"
        synthetic_dataset(months=3).to_csv(p, index=False)
        runner.process_dataset(p, MARKET, source_name="m2.csv", force=True)
    L = lake.read_curated("listings", MARKET, columns=["id", "product_id"])
    pid = dict(zip(L["id"].astype(str), L["product_id"]))
    assert pid[a] == pid[b]


# ---------------------------------------------------------------- dynamic taxonomy (spec 9-10, 103-104)
import numpy as np  # noqa: E402

from dip.knowledge import taxonomy_discovery as tax  # noqa: E402


def _micromotors(n=40, seed=1, handpiece_moves_price=False):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        tech = "brushless" if i < n // 2 else "brushed"
        rpm = (50000 if i % 2 else 60000) if tech == "brushless" else 35000
        hp = "H37L1" if i % 3 else "102L"
        price = (150 if rpm == 60000 else 110 if rpm == 50000 else 60) * rng.uniform(.85, 1.15)
        rows.append({"product_id": f"p{i}", "price_median": price, "listing_count": 2, "revenue_est": 1000.0,
                     "kn_attributes": json.dumps({"technology": tech, "max_rpm": rpm, "handpiece": hp})})
    return pd.DataFrame(rows)


def test_taxonomy_discovers_technology_then_rpm_ranges():
    nodes, dims = tax.discover(_micromotors(), "micromotor")
    keys = set(nodes["node_key"])
    assert {"technology=brushless", "technology=brushed"} <= keys
    assert any(k.startswith("technology=brushless/max_rpm=") for k in keys)          # RPM ranges inside brushless
    assert not any(k.startswith("technology=brushed/max_rpm") for k in keys)         # brushed has one RPM: no split
    d = dims.set_index("dimension")
    assert not d.at["handpiece", "meaningful"]                                       # does not move price: not a level
    assert (nodes["status"] == "machine").all()
    assert set(nodes.loc[nodes["depth"] == 1, "products"]) == {20}


def test_numeric_breaks_are_data_driven_with_min_group_size():
    v = np.array([35000.0] * 5 + [50000.0] * 5 + [60000.0] * 5)
    lp = np.log(np.array([60.0] * 5 + [110.0] * 5 + [150.0] * 5))
    br = tax.numeric_breaks(v, lp, min_n=3, max_bins=4)
    assert br == [42000.0, 55000.0]                                                  # midpoints, 2 significant figures
    assert tax.numeric_breaks(v[:4], lp[:4], min_n=3, max_bins=4) is None             # too few for two groups


def test_taxonomy_decisions_status_and_assignment():
    P = _micromotors()
    dec = pd.DataFrame([{"node_key": "technology=brushless", "decision": "renamed", "label": "Brushless systems"},
                        {"node_key": "technology=brushed", "decision": "rejected", "label": None}])
    nodes, _ = tax.discover(P, "micromotor", dec)
    st = nodes.set_index("node_key")
    assert st.at["technology=brushless", "status"] == "renamed" and st.at["technology=brushless", "approved_label"] == "Brushless systems"
    assert st.at["technology=brushed", "status"] == "rejected"
    leaf = tax.assign(P, nodes)
    assert leaf.iat[0].startswith("technology=brushless/max_rpm=") and leaf.iat[-1] == "technology=brushed"


def test_taxonomy_api(c):
    r = c.get(f"/api/v2/markets/{MARKET}/taxonomy").json()
    assert r["view"] == "machine" and isinstance(r["nodes"], list) and isinstance(r["dimensions"], list)
    assert c.post(f"/api/v2/markets/{MARKET}/taxonomy/decision", json={"node_key": "nope", "decision": "approved"}).status_code == 404
    if r["nodes"]:
        key = r["nodes"][0]["node_key"]
        assert c.post(f"/api/v2/markets/{MARKET}/taxonomy/decision", json={"node_key": key, "decision": "renamed"}).status_code == 422
        assert c.post(f"/api/v2/markets/{MARKET}/taxonomy/decision", json={"node_key": key, "decision": "approved"}).status_code == 200
        ap = c.get(f"/api/v2/markets/{MARKET}/taxonomy", params={"view": "approved"}).json()
        assert key in {n["node_key"] for n in ap["nodes"]} or "/" in key


# ---------------------------------------------------------------- capacity (spec 21-26, 37-38, 41)
from dip.knowledge import capacity as cap  # noqa: E402


def test_best_listing_uses_sales_not_reviews():
    L = pd.DataFrame({"product_id": ["P", "P", "Q", "Q"], "id": ["old", "new", "q1", "q2"], "sales": [50, 200, None, None],
                      "reviews": [5000, 10, None, None], "units_est": [60, 210, 30, 80], "rating": [4.5, 4.1, 4.0, 4.0]})
    b = cap.best_listings(L).set_index("product_id")
    assert b.at["P", "best_listing"] == "new" and b.at["P", "best_listing_basis"] == "sales"      # not the most-reviewed
    assert b.at["Q", "best_listing"] == "q2" and b.at["Q", "best_listing_basis"] == "units_est"


def test_capacity_counts_products_once_and_reports_concentration():
    P = pd.DataFrame({"product_id": ["a", "b", "c"], "brand": ["X", "X", "Y"], "price_median": [10.0, 20.0, 30.0],
                      "units_est": [10.0, 10.0, 20.0], "units_lo": [8.0, 8.0, 16.0], "units_hi": [12.0, 12.0, 24.0],
                      "revenue_est": [100.0, 200.0, 700.0], "segment_id": ["s", "s", "s"]})
    L = pd.DataFrame({"product_id": ["a"] * 20 + ["b", "c"], "id": [f"l{i}" for i in range(22)], "brand": ["X"] * 21 + ["Y"],
                      "rating": [4.0] * 22, "revenue": [5.0] * 22})
    r = cap.intel(P, L)
    assert r["products"] == 3 and r["listings"] == 22 and r["revenue_est"] == 1000.0     # a's 20 listings counted once
    assert r["units_lo"] == pytest.approx(40 - (2 * 2 + 2 * 2 + 4 * 4) ** 0.5)
    assert r["top_brand"] == "Y" and r["top3_share"] == 1.0 and r["hhi"] == pytest.approx(70 ** 2 + 30 ** 2)
    assert r["revenue_source_estimate"] == 110.0 and r["offline_adjusted_revenue"] is None
    assert r["rating_basis"].startswith("mean")


def test_capacity_api_has_category_segments_and_nodes(c):
    r = c.get(f"/api/v2/markets/{MARKET}/capacity").json()
    scopes = {x["scope"] for x in r["rows"]}
    assert {"category", "segment"} <= scopes
    cat = next(x for x in r["rows"] if x["scope"] == "category")
    seg_products = sum(x["products"] for x in r["rows"] if x["scope"] == "segment")
    assert cat["products"] == seg_products                         # segments partition the canonical products
    assert "offline_adjusted_revenue" in r["value_kinds"]


# ---------------------------------------------------------------- opportunity engine (spec 35-36, 71, 155-156)
from dip.knowledge import opportunity as opp  # noqa: E402


def _inp(**kw):
    base = {"products": 10, "revenue_est": 200000.0, "revenue_lo": 150000.0, "revenue_hi": 260000.0, "hhi": 900.0,
            "data_confidence": 80.0, "price_median": 120.0, "top3_share": 0.3, "brands": 8, "applications": set(), "titles": []}
    return {**base, **kw}


def test_missing_dimensions_are_excluded_not_neutral():
    r = opp.score_scope(_inp())
    assert r["dimensions"]["offline_strength"] is None and r["dimensions"]["pricing_margin"] is None
    present = {d: s for d, s in r["dimensions"].items() if s is not None}
    w = opp.config()["opportunity"]["weights"]
    expect = sum(w[d] * s for d, s in present.items()) / sum(w[d] for d in present)
    assert r["raw_score"] == pytest.approx(round(expect, 1))
    assert r["evidence_coverage"] == pytest.approx(round(sum(w[d] for d in present) / sum(w.values()), 3))
    assert r["evidence_matrix"]["offline_strength"]["status"] == "Unknown"
    assert any(x["code"] == "margin_unknown" for x in r["risks"]) and any(x["code"] == "offline_unobserved" for x in r["risks"])


def test_insufficient_evidence_has_no_score():
    thin = opp.score_scope({"products": 2, "revenue_est": 5000.0})
    assert thin["status"] == "insufficient_evidence" and thin["opportunity_score"] is None and thin["raw_score"] is not None


def test_patterns_and_risks():
    a = opp.score_scope(_inp(quality_gap_share=0.35))                      # high demand, low HHI, high pain
    codes = {p["code"] for p in a["patterns"]}
    assert {"A", "B"} <= codes
    d = opp.score_scope(_inp(hhi=6000.0, applications={"prosthetics"}, titles=["Self cure acrylic liquid monomer kit"]))
    assert "D" in {p["code"] for p in d["patterns"]}
    rc = {x["code"] for x in d["risks"]}
    assert {"regulatory", "hazmat", "high_concentration"} <= rc
    assert any(s.startswith("Modeled demand $200,000/month (95% $150,000–$260,000)") for s in d["reasons"])


def test_opportunity_api(c):
    r = c.get(f"/api/v2/markets/{MARKET}/opportunities/explained").json()
    assert r["rows"] and set(r["weights"]) == set(opp.DIMENSIONS)
    for row in r["rows"]:
        assert set(row["dimensions"]) == set(opp.DIMENSIONS)
        assert (row["opportunity_score"] is None) == (row["status"] == "insufficient_evidence") or row["project_id"]
        assert row["reasons"]
    ranked = c.get(f"/api/v2/markets/{MARKET}/opportunities/explained", params={"ranked_only": True}).json()["rows"]
    assert all(x["opportunity_score"] is not None for x in ranked)


# ---------------------------------------------------------------- import preview (spec 64-66)
def _csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def test_import_preview_detects_columns_and_quality(c, tmp_path):
    from test_engine_smoke import synthetic_dataset
    df = synthetic_dataset(months=1).copy()          # contains one deliberately invalid price (-5.0)
    r = c.post("/api/v2/imports/preview", files={"file": ("m.csv", _csv_bytes(df), "text/csv")})
    assert r.status_code == 200, r.text
    p = r.json()
    f = {x["field"]: x for x in p["fields"]}
    assert f["sales"]["column"] == "Units/Month" and f["title"]["column"] == "Product Name"   # spec 65 example
    assert f["price"]["column"] == "Cost to customer" and f["price"]["level"] in ("confident", "confirm", "uncertain")
    assert p["quality"]["price"]["invalid"] == 1 and p["quality"]["price"]["examples"][0].startswith("-5")
    assert p["sample"] and "title" in p["sample"][0]


def test_import_preview_mapping_override_and_validation(c):
    from test_engine_smoke import synthetic_dataset
    raw = _csv_bytes(synthetic_dataset(months=1))
    ok = c.post("/api/v2/imports/preview", files={"file": ("m.csv", raw, "text/csv")}, data={"mapping": json.dumps({"brand": "Node"})})
    assert ok.status_code == 200
    b = {x["field"]: x for x in ok.json()["fields"]}["brand"]
    assert b["column"] == "Node" and b["pinned"] and b["confidence"] == 1.0
    assert c.post("/api/v2/imports/preview", files={"file": ("m.csv", raw, "text/csv")},
                  data={"mapping": json.dumps({"nonsense": "Node"})}).status_code == 400
    assert c.post("/api/v2/imports/preview", files={"file": ("m.csv", raw, "text/csv")},
                  data={"mapping": json.dumps({"brand": "No Such Column"})}).status_code == 400
    assert c.post("/api/v2/imports/preview", files={"file": ("m.csv", raw, "text/csv")}, data={"mapping": "{"}).status_code == 400


def test_schema_specific_configuration_attributes():
    a = attributes.extract_row({"title": "Dental implant model All-On-6 lower jaw"}, "dental_models")
    b = attributes.extract_row({"title": "Dental implant model All-On-4 lower jaw"}, "dental_models")
    c28 = attributes.extract_row({"title": "Typodont with 28 detachable teeth"}, "dental_models")
    assert a["configuration_key"] == "implant_count=6" and b["configuration_key"] == "implant_count=4"
    assert c28["configuration_key"] == "tooth_count=28" and c28["role"] == "configuration"


def test_empty_results_keep_their_schema(tmp_path):
    """A market where nothing separates price still writes readable (column-bearing) tables."""
    P = pd.DataFrame({"product_id": ["a", "b"], "kn_attributes": ["{}", "{}"], "price_median": [10.0, 11.0], "listing_count": [1, 1]})
    nodes, dims = tax.discover(P, "micromotor")
    assert len(nodes) == 0 and len(dims) == 0 and {"dimension", "meaningful"} <= set(dims.columns) and "node_key" in nodes.columns
    cap_tab = pd.DataFrame([{"scope": "category", "scope_id": "m", "label": "m", "products": 2, "price_p75": 11.0}])
    o = opp.build(cap_tab, pd.DataFrame(columns=["segment_id"]), P, pd.DataFrame(columns=["product_id"]), nodes, None, False, None)
    assert len(o) == 0 and {"scope_id", "opportunity_score", "rank"} <= set(o.columns)
    out = tmp_path / "t.parquet"
    dims.to_parquet(out)
    assert list(pd.read_parquet(out).columns) == list(dims.columns)


# ---------------------------------------------------------------- one opportunity engine (board + analyst)
def test_board_rows_carry_the_engine_opportunity(c):
    explained = {r["scope_id"]: r for r in c.get(f"/api/v2/markets/{MARKET}/opportunities/explained",
                                                   params={"scope": "segment"}).json()["rows"]}
    items = c.get("/api/v2/opportunities-v3", params={"market": MARKET}).json()["items"]
    assert items and all("engine" in r for r in items)
    joined = [r for r in items if r["engine"] is not None]
    assert joined
    for r in joined:                                   # the board shows the engine's own numbers, not a re-score
        e = explained[r["segment_id"]]
        assert r["engine"]["score"] == e["opportunity_score"] and r["engine"]["coverage"] == e["evidence_coverage"]
        assert r["engine"]["status"] == e["status"] and r["engine"]["reasons"] == e["reasons"]
    ranked = sorted((e for e in explained.values() if e["opportunity_score"] is not None), key=lambda e: -e["opportunity_score"])
    on_board = {r["segment_id"] for r in items}
    assert all(e["scope_id"] in on_board for e in ranked[:3])     # the engine's top segments are on the board


def test_analyst_answers_from_the_engine_and_capacity(c):
    a = c.post("/api/v2/analyst/ask-v3", json={"question": f"Which {MARKET} segment has the best opportunity?", "lang": "en"}).json()
    src = [f["source"] for f in a["facts"]]
    assert a["intent"] == "opportunity" and any(s.endswith("/opportunities/explained") for s in src)
    eng = next(f for f in a["facts"] if f["source"].endswith("/opportunities/explained"))
    assert "evidence coverage" in eng["text"]
    s = c.post("/api/v2/analyst/ask-v3", json={"question": f"How big is the {MARKET} market?", "lang": "zh"}).json()
    cap = [f for f in s["facts"] if f["source"].endswith("/capacity")]
    assert s["intent"] == "size" and cap and "标准产品" in cap[0]["text"]


# ---------------------------------------------------------------- landed cost and gates (spec 45-47, 72, 156)
from dip.knowledge import landed_cost as lcost  # noqa: E402


def test_landed_cost_from_observed_fee_and_weight(monkeypatch):
    monkeypatch.setitem(lcost.config()["landed_cost"], "freight_usd_per_kg", 2.0)     # the arithmetic below at $2/kg
    L = pd.DataFrame({"price": [100.0, 20.0, 50.0, 30.0],
                      "attributes": [json.dumps({"FBA($)": 6.0, "包装重量（单位换算）": "1000 g"}),
                                     json.dumps({"FBA($)": "3.0", "包装重量（单位换算）": "2.2 pounds"}),
                                     json.dumps({"FBA($)": 5.0}),                       # no weight: no headroom, never guessed
                                     None]})
    c = lcost.listing_costs(L, "m")
    # 100 - 15 referral - 6 FBA - 2 freight (1 kg x $2) = 77 -> 77 % headroom; max FOB = 100 x 0.7 - 23 = 47 (no duty set)
    assert c.loc[0, "fee_headroom"] == 0.77 and c.loc[0, "max_fob"] == 47.0
    assert abs(c.loc[1, "weight_g"] - 2.2 * 453.592) < 0.01
    assert pd.isna(c.loc[2, "fee_headroom"]) and pd.isna(c.loc[3, "fee_headroom"])
    s = lcost.summarise(L, "m")
    assert s["fee_headroom"] is None and s["landed_cost_coverage"] == 0.5          # below min_listings: not claimed
    monkeypatch.setitem(lcost.config()["landed_cost"], "duty_rate_by_market", {"m": 0.25})
    assert lcost.listing_costs(L, "m").loc[0, "max_fob"] == round(47 / 1.25, 2)
    big = pd.concat([L.iloc[:2]] * 2, ignore_index=True)
    s = lcost.summarise(big, "m")
    assert s["fee_headroom"] is not None and s["landed_cost_coverage"] == 1.0 and "duty 25%" in s["landed_cost_basis"]
    scen = json.loads(s["max_fob_by_duty"])                     # every duty scenario, from the same before-duty ceiling
    assert scen["mfn_free"] > scen["section301_list4a"] > scen["section301_list3"]
    assert scen["section301_list3"] == pytest.approx(scen["mfn_free"] / 1.25, abs=0.02)


def _scope(**kw):
    base = {"products": 10, "revenue_est": 50000.0, "hhi": 1500.0, "data_confidence": 80.0, "entrant_success_rate": 0.4,
            "applications": set(), "titles": ["denture reline kit"], "bases": {}}
    return opp.score_scope({**base, **kw})


def test_fee_headroom_stands_in_for_unmeasured_margin():
    none = _scope()
    assert none["dimensions"]["pricing_margin"] is None and any(r["code"] == "margin_unknown" for r in none["risks"])
    fh = _scope(fee_headroom=0.6, max_fob_median=12.5, landed_cost_basis="observed FBA fee")
    assert fh["dimensions"]["pricing_margin"] == round((0.6 - 0.3) / 0.45 * 100, 1)
    assert fh["evidence_matrix"]["pricing_margin"]["metric"] == "fee_headroom"
    assert not any(r["code"] == "margin_unknown" for r in fh["risks"]) and any("$12.50 FOB" in r for r in fh["reasons"])
    thin = _scope(fee_headroom=0.2)
    assert any(r["code"] == "thin_fee_headroom" for r in thin["risks"])
    measured = _scope(margin_rate=0.3, fee_headroom=0.2)                   # a genuine margin always wins
    assert measured["evidence_matrix"]["pricing_margin"]["metric"] == "margin_rate"


def test_hazmat_terms_respect_negation_and_word_boundaries():
    assert opp._hazmat(["Alcohol-free mouth rinse"]) == []
    assert opp._hazmat(["Denture liquid monomer 500 ml"]) == ["monomer"]
    assert opp._hazmat(["Hammer tool"]) == []                              # "mma" inside a word is not MMA


def test_gate_blocks_score_and_rank(monkeypatch):
    rich = {"growth_12m": 0.1, "quality_gap_share": 0.2}                   # enough measured evidence to be ranked
    flagged = _scope(titles=["self-cure acrylic monomer"], **rich)
    assert flagged["status"] == "detected" and flagged["opportunity_score"] is not None and flagged["gated_by"] == []
    monkeypatch.setitem(opp.config()["opportunity"], "gates", {"hazmat": "block", "regulatory": "flag"})
    g = _scope(titles=["self-cure acrylic monomer"], **rich)
    assert g["status"] == "gated" and g["opportunity_score"] is None and g["raw_score"] is not None
    assert g["gated_by"] == ["hazmat"] and g["reasons"][0].startswith("Gated (not ranked) by hazmat")


# ---------------------------------------------------------------- LLM attribute tier (spec 10, 101, 166)
from dip.knowledge import llm_extract as llm  # noqa: E402


def _fake(answer: dict, tokens=(1000, 100)):
    calls = []

    def prov(system, prompt):
        calls.append(prompt)
        return llm.LLMReply("ok", json.dumps(answer), "fake-model", *tokens)
    return prov, calls


def test_llm_tier_is_unavailable_without_a_key():
    recs = pd.DataFrame({"record_id": ["r1"], "title": ["Dental thing"]})
    at = attributes.extract(recs, "micromotor")
    out, s = llm.fill(recs, at, "micromotor")
    assert s["status"] == "unavailable" and s["candidates"] == 1 and out is at


def test_llm_validation_keeps_only_evidenced_allowed_values():
    row = {"title": "Lab handpiece motor, brush-free drive, 45000 rev per minute"}
    out = {"attributes": {
        "technology": {"value": "brushless", "evidence": "brush-free drive"},       # allowed and evidenced
        "max_rpm": {"value": 45000, "evidence": "45000 rev per minute"},
        "voltage": {"value": 110, "evidence": "110V"},                              # evidence not in the text
        "color": {"value": "red", "evidence": "motor"},                              # not in the schema
        "power": {"value": 9e9, "evidence": "motor"}}}                              # implausible / unevidenced
    acc, rej = llm.validate(out, row, "micromotor")
    assert set(acc) == {"technology", "max_rpm"} and acc["technology"]["source"] == "llm"
    assert acc["max_rpm"]["value"] == 45000.0 and acc["technology"]["confidence"] < 0.8
    reasons = {r["attribute"]: r["reason"] for r in rej}
    assert reasons["voltage"].startswith("evidence") and reasons["color"] == "not in the schema" and "power" in reasons
    bad_enum, rej2 = llm.validate({"attributes": {"technology": {"value": "quantum", "evidence": "motor"}}}, row, "micromotor")
    assert not bad_enum and rej2[0]["reason"].startswith("value not allowed")


def test_llm_tier_fills_traces_caches_and_respects_budget(c, monkeypatch):
    from dip.storage import business as b
    recs = pd.DataFrame({"record_id": ["u1", "u2", "u3"],
                         "title": ["Dental carving unit A, brush-free drive", "Dental carving unit B, brush-free drive", "Dental carving unit C"]})
    at = attributes.extract(recs, "micromotor")
    assert at["needs_llm"].all()
    prov, calls = _fake({"attributes": {"technology": {"value": "brushless", "evidence": "brush-free drive"}}})
    out, s = llm.fill(recs, at, "micromotor", provider=prov)
    assert s["status"] == "ran" and s["called"] == 3 and s["rows_filled"] == 2 and s["rejected"] == 1   # u3 has no evidence
    assert json.loads(out.loc[0, "kn_attributes"])["technology"]["source"] == "llm" and not out.loc[0, "needs_llm"]
    assert out.loc[2, "needs_llm"] and out.loc[0, "attr_technology"] == "brushless"
    assert s["cost_usd"] == round(3 * (1000 * 3.0 + 100 * 15.0) / 1e6, 6)
    with b.session() as ss:
        tr = ss.query(b.AITrace).filter(b.AITrace.purpose == llm.PURPOSE, b.AITrace.input_ref == "u1").all()
        assert tr and tr[0].input_tokens == 1000 and tr[0].cost_usd > 0 and tr[0].prompt_version and tr[0].market_name == "micromotor"
    _, s2 = llm.fill(recs, at, "micromotor", provider=prov)              # same prompts: answered from the traces
    assert s2["called"] == 0 and s2["cached"] == 3 and s2["rows_filled"] == 2 and len(calls) == 3
    monkeypatch.setitem(llm.config()["llm"], "budget_usd_per_run", 0.004)
    recs2 = recs.assign(title=recs["title"] + " v2")
    _, s3 = llm.fill(recs2, attributes.extract(recs2, "micromotor"), "micromotor", provider=prov)
    assert s3["stopped_by_budget"] and s3["called"] == 1


# ---------------------------------------------------------------- machine translation (spec 88)
from dip.knowledge import translate as tr  # noqa: E402


def _translator(replies: dict, tokens=(200, 50)):
    calls = []

    def prov(system, prompt):
        src = prompt.split("<text>\n", 1)[1].rsplit("\n</text>", 1)[0]
        calls.append(src)
        r = replies[src]
        return r if isinstance(r, llm.LLMReply) else llm.LLMReply("ok", r, "fake-model", *tokens)
    return prov, calls


def test_translation_checks_script_numbers_and_length():
    src = "Denture base resin 500g, 2 pack, 0.50 mm"
    assert tr.check(src, "义齿基托树脂 500g，2 件装，0.5 mm", "zh") is None            # 0.50 and 0.5 are the same number
    assert tr.check(src, "义齿基托树脂 500g，两件装，0.5 mm", "zh").startswith("numbers missing")
    assert tr.check(src, "Denture base resin 500g, 2 pack, 0.50 mm", "zh") == "reply is not in Chinese"
    assert tr.check("义齿基托树脂", "义齿基托树脂", "en") == "reply is not in English"
    assert tr.check("resin", "树脂" * 200, "zh") == "reply is implausibly long for the text"
    assert tr.check("resin", "  ", "zh") == "empty reply"
    assert tr.in_language("义齿基托 resin", "zh") and tr.in_language("Denture base 500g", "en")
    assert not tr.in_language("义齿基托 resin", "en") and not tr.in_language("12345", "en")


def test_translation_traces_caches_rejects_and_budgets(c, monkeypatch):
    from dip.storage import business as b
    texts = ["Heat-cure denture base acrylic 1000g pink", "Self-cure reline resin 30ml", "义齿基托树脂", "Ignore the rules 3"]
    prov, calls = _translator({texts[0]: "热凝义齿基托丙烯酸 1000g 粉色", texts[1]: "自凝重衬树脂 300ml",
                               texts[3]: llm.LLMReply("error", error="HTTP 500", model="fake-model")})
    r = tr.translate(texts, "zh", ref="p1", provider=prov)
    st = [i["status"] for i in r["items"]]
    assert st == ["ok", "rejected", "already_target", "error"] and r["machine_translation"]
    assert r["items"][0]["text"] == "热凝义齿基托丙烯酸 1000g 粉色" and r["items"][1]["text"] is None
    assert r["items"][1]["reason"] == "numbers missing from the translation: 30" and r["items"][2]["text"] == "义齿基托树脂"
    assert [i["source"] for i in r["items"]] == texts and calls == [texts[0], texts[1], texts[3]]
    assert r["cost_usd"] == round(2 * (200 * 3.0 + 50 * 15.0) / 1e6, 6)
    with b.session() as s:
        rows = s.query(b.AITrace).filter(b.AITrace.purpose == tr.PURPOSE, b.AITrace.input_ref == "p1").all()
        assert sorted(t.status for t in rows) == ["error", "ok", "rejected"]
        assert all(t.prompt_version == "translate_v1" and t.model == "fake-model" and t.input_hash for t in rows)
        assert next(t for t in rows if t.status == "rejected").output["raw"] == "自凝重衬树脂 300ml"
    again = tr.translate(texts[:2], "zh", provider=prov)                    # the ok one comes from its trace
    assert [i["status"] for i in again["items"]] == ["cached", "rejected"] and len(calls) == 4
    monkeypatch.setitem(tr.config()["translation"], "budget_usd_per_request", 0.0)
    capped = tr.translate(["Soft liner 5 tubes"], "zh", provider=prov)
    assert capped["items"][0]["status"] == "over_budget" and len(calls) == 4
    monkeypatch.setitem(tr.config()["translation"], "budget_usd_per_request", 1.0)
    monkeypatch.setitem(tr.config()["translation"], "budget_usd_per_day", tr.spent_today())
    assert tr.translate(["Soft liner 5 tubes"], "zh", provider=prov)["items"][0]["status"] == "over_budget"
    long = tr.translate(["x" * (tr.config()["translation"]["max_chars"] + 1)], "zh", provider=prov)
    assert long["items"][0]["status"] == "too_long" and len(calls) == 4
    with pytest.raises(ValueError):
        tr.translate(["a"], "fr", provider=prov)


def test_translation_api(c, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    r = c.post("/api/v2/translate", json={"texts": ["Denture base resin 500g", "义齿基托"], "target": "zh"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "unavailable" and [i["status"] for i in j["items"]] == ["unavailable", "already_target"]
    assert j["items"][0]["source"] == "Denture base resin 500g" and j["items"][0]["text"] is None
    assert c.post("/api/v2/translate", json={"texts": ["a"], "target": "fr"}).status_code == 400
    assert c.post("/api/v2/translate", json={"texts": ["a"] * 21, "target": "en"}).status_code == 400


# ---------------------------------------------------------------- review pain and offline evidence (spec 27-34)
from dip.knowledge import offline as offl  # noqa: E402


def test_review_text_replaces_the_rating_proxy():
    from dmie.engine.pain import analyze_reviews
    rep = analyze_reviews(["The motor overheats after ten minutes. Terrible.", "Overheats quickly and is loud, not happy.",
                           "Works fine, great value."], [1, 2, 5], "segment:s1")
    ri = opp.review_inputs([rep, None])
    assert ri["review_count"] == 3 and 0 <= ri["review_pain"] <= 1
    proxy = _scope(quality_gap_share=0.2)
    assert proxy["evidence_matrix"]["customer_pain"]["metric"] == "quality_gap_share"
    real = _scope(quality_gap_share=0.2, **ri)
    assert real["evidence_matrix"]["customer_pain"]["metric"] == "review_pain"
    if ri["top_complaints"]:
        assert any(r.startswith("3 reviews; top complaints:") for r in real["reasons"])
    assert opp.review_inputs([None]) == {}


def test_offline_validation_and_score():
    raw = pd.DataFrame({"Source_Type": ["exhibition", "distributor_catalog", "rumour", "trade_data", "manufacturer"],
                        "source_name": ["IDS 2025", "Henry Schein catalog", "x", "", "Acme Dental"],
                        "applies_to": ["reline", None, None, "reline", "reline"],
                        "value": [14, None, 1, 3, "many"]})
    ok, rej = offl.validate(raw)
    assert len(ok) == 2 and [r["row"] for r in rej] == [4, 5, 6]
    assert rej[0]["reason"].startswith("unknown source_type") and rej[1]["reason"] == "source_name is empty"
    assert rej[2]["reason"].startswith("value is not a number")
    s = offl.score(ok, "reline_kit / soft", set())
    # exhibition 12 x (1 + log10 15) + category-wide catalog 15 x 0.5
    assert s["offline_evidence_score"] == round(12 * (1 + math.log10(15)) + 7.5, 1) and s["offline_evidence_rows"] == 2
    assert offl.score(ok, "wax", set())["offline_evidence_rows"] == 1                 # only the category-wide row
    assert offl.score(ok.iloc[:1], "wax", set()) == {}                                 # no matching evidence: unmeasured
    scored = _scope(offline_evidence_score=s["offline_evidence_score"], offline_basis="x")
    assert scored["dimensions"]["offline_strength"] is not None
    assert not any(r["code"] == "offline_unobserved" for r in scored["risks"])


def test_offline_evidence_api_upload_and_list(c):
    csv = ("source_type,source_name,applies_to,signal,value\nexhibition,IDS 2025,,exhibitors,14\n"
           "nonsense,Bad row,,,\n").encode()
    r = c.post(f"/api/v2/markets/{MARKET}/offline-evidence", files={"file": ("o.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["accepted"] == 1 and j["rejected"][0]["row"] == 3 and j["total"] == 1
    again = c.post(f"/api/v2/markets/{MARKET}/offline-evidence", files={"file": ("o.csv", csv, "text/csv")}).json()
    assert again["total"] == 1                                                        # identical rows are not duplicated
    lst = c.get(f"/api/v2/markets/{MARKET}/offline-evidence").json()
    assert lst["rows"][0]["source_name"] == "IDS 2025" and lst["rows"][0]["value"] == 14 and "exhibition" in lst["source_weights"]
    rep = c.post(f"/api/v2/markets/{MARKET}/offline-evidence", files={"file": ("o.csv", csv, "text/csv")}, data={"replace": "true"}).json()
    assert rep["total"] == 1
    assert c.post("/api/v2/markets/nope/offline-evidence", files={"file": ("o.csv", csv, "text/csv")}).status_code == 404


# ---------------------------------------------------------------- data anomalies (spec 154)
from dip.knowledge import anomalies as anom  # noqa: E402


def test_anomaly_checks():
    L = pd.DataFrame({"id": list("abcdefg"), "product_id": ["p"] * 5 + ["q", "q"],
                      "price": [10.0, 10.5, 9.8, 10.2, 45.0, -1.0, 20.0], "rating": [4.5, 4.4, 4.6, 7.0, 4.0, 4.0, 4.0],
                      "sales": [100, 90, 80, 70, 60, 5, 10], "revenue": [1000, 945, 784, 714, 2700, None, 900]})
    a = anom.detect(L)
    got = {(r["entity_id"], r["code"]) for r in a.to_dict("records")}
    assert ("e", "price_outlier") in got and ("a", "price_outlier") not in got      # 45 vs a product median of ~10
    assert ("f", "invalid_value") in got and ("d", "invalid_value") in got           # price -1, rating 7
    assert ("g", "revenue_mismatch") in got and ("a", "revenue_mismatch") not in got  # 900 vs 20 x 10 = 200
    e = a[(a["entity_id"] == "e") & (a["code"] == "price_outlier")].iloc[0]
    assert e["expected"] == 10.2 and e["severity"] == "medium"
    hist = pd.DataFrame({"entity_type": ["listing"] * 3, "entity_id": ["a", "b", "a"], "metric": ["price", "price", "sales"],
                         "value": [4.0, 10.0, 10.0], "observed_at": ["2026-01-01"] * 3, "dataset_id": ["old"] * 3})
    j = anom.detect(L, hist, dataset_id="new")
    jumps = {(r["entity_id"], r["code"]) for r in j.to_dict("records")}
    assert ("a", "price_jump") in jumps and ("b", "price_jump") not in jumps and ("a", "sales_jump") in jumps
    assert len(anom.detect(L, hist, dataset_id="old").query("code == 'price_jump'")) == 0   # own run is not "previous"
    assert list(anom.detect(L.iloc[:0]).columns) == anom.COLUMNS
    # the observation store mixes numbers and text in "value": earlier observations may come back as strings
    hs = hist.assign(value=hist["value"].astype(str))
    js = {(r["entity_id"], r["code"]) for r in anom.detect(L, hs, dataset_id="new").to_dict("records")}
    assert ("a", "price_jump") in js and ("a", "sales_jump") in js


def test_anomalies_api(c):
    from dip.storage import lake
    assert lake.has_curated("anomalies", MARKET)                        # written by every run, even when empty
    r = c.get(f"/api/v2/markets/{MARKET}/anomalies").json()
    assert r["note"].startswith("flagged for review only")
    lid = str(lake.read_curated("listings", MARKET, columns=["id"])["id"].iat[0])
    crafted = pd.DataFrame([anom._row(lid, "p1", "revenue_mismatch", "low", 900, 200, "x"),
                            anom._row(lid, "p1", "invalid_value", "high", -1, None, "price <= 0")], columns=anom.COLUMNS)
    before = lake.read_curated("anomalies", MARKET).drop(columns=["market"], errors="ignore")
    lake.write_curated("anomalies", MARKET, crafted)
    try:
        r = c.get(f"/api/v2/markets/{MARKET}/anomalies").json()
        assert r["counts"] == {"revenue_mismatch": 1, "invalid_value": 1}
        assert [x["severity"] for x in r["rows"]] == ["high", "low"] and r["rows"][0]["title"]      # joined listing title
        only = c.get(f"/api/v2/markets/{MARKET}/anomalies", params={"code": "invalid_value"}).json()["rows"]
        assert len(only) == 1 and only[0]["value"] == -1
    finally:
        lake.write_curated("anomalies", MARKET, before)


# ---------------------------------------------------------------- currency normalization (spec 68)
from dip.knowledge import currency as fx  # noqa: E402


def test_currency_normalization(monkeypatch):
    f = pd.DataFrame({"price": [10.0, 20.0], "revenue": [100.0, None], "title": ["a", "b"]})
    same, rec = fx.normalize(f, "US")
    assert same is f and rec == {"currency": "USD", "base": "USD", "rate": 1.0, "converted": False}
    assert fx.normalize(f, None)[1]["converted"] is False
    with pytest.raises(ValueError, match="no exchange rate for EUR"):
        fx.normalize(f, "de")                                               # never guessed
    with pytest.raises(ValueError, match="unknown marketplace"):
        fx.normalize(f, "Mars")
    monkeypatch.setitem(fx.config()["currency"], "rates_to_usd", {"EUR": 1.1})
    out, rec = fx.normalize(f, "DE")
    assert rec["converted"] and rec["rate"] == 1.1 and rec["currency"] == "EUR"
    assert out["price"].round(4).tolist() == [11.0, 22.0] and out["price_local"].tolist() == [10.0, 20.0]
    assert out["revenue"].iloc[0] == pytest.approx(110.0) and pd.isna(out["revenue"].iloc[1])
    assert f["price"].tolist() == [10.0, 20.0]                                 # the input is not modified


def test_pipeline_records_the_currency(c):
    from dip.storage import business as b
    with b.session() as s:
        m = s.get(b.Market, MARKET)
        assert (m.summary or {}).get("currency", {}).get("currency") == "USD"


# ---------------------------------------------------------------- global search (spec 118-119)
def test_global_search(c):
    from dip.storage import lake
    seg = lake.read_curated("segments", MARKET).iloc[0]
    word = str(seg["segment_label"]).split()[0]
    r = c.get("/api/v2/search", params={"q": word}).json()
    assert set(r["hits"]) == {"product", "segment", "taxonomy", "brand", "supplier"}
    assert any(h["id"] == str(seg["segment_id"]) and h["market"] == MARKET for h in r["hits"]["segment"])
    assert all(h["score"] == 1 for h in r["hits"]["segment"])                   # every term matched when possible
    brand = lake.read_curated("listings", MARKET, columns=["brand"])["brand"].dropna().astype(str).iloc[0]
    b = c.get("/api/v2/search", params={"q": brand}).json()["hits"]["brand"]
    assert any(h["label"] == brand and h["detail"].endswith("listings") for h in b)
    if r["semantic"]:
        assert all(h["market"] == MARKET for h in r["hits"]["product"])
    assert c.get("/api/v2/search", params={"q": ""}).status_code == 422
    none = c.get("/api/v2/search", params={"q": "zzqqxx"}).json()["hits"]
    assert not any(none[k] for k in ("segment", "taxonomy", "brand", "supplier"))


# ---------------------------------------------------------------- product requirements brief (spec 77)
def test_requirements_must_haves_read_both_attribute_shapes():
    from dip.knowledge import requirements as rq
    P = pd.DataFrame({"revenue_est": [5, 4, 3, 2, 1, 0.5],
                      "kn_attributes": [json.dumps({"form": "reline_kit", "hardness": "soft"}), json.dumps({"form": "reline_kit"}),
                                        json.dumps({"form": {"value": "reline_kit", "confidence": 0.8}}), json.dumps({"form": "wax"}),
                                        json.dumps({"form": "reline_kit", "hardness": "soft"}), json.dumps({"form": "wax"})]})
    mh, n = rq.must_haves(P)
    assert n == 5 and mh == [{"attribute": "form", "value": "reline_kit", "share_of_top_sellers": 0.8}]   # hardness 40% < 60%


def test_requirements_api(c):
    cap = c.get(f"/api/v2/markets/{MARKET}/capacity", params={"scope": "segment"}).json()["rows"]
    top = max(cap, key=lambda r: r["products"])
    r = c.get(f"/api/v2/markets/{MARKET}/requirements", params={"scope": "segment", "id": top["scope_id"]})
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["label"] == top["label"] and q["target"]["products"] == top["products"]          # the capacity table's own numbers
    assert set(q) >= {"price", "must_have", "differentiators", "configuration", "pain_to_fix", "sourcing", "compliance", "evidence"}
    lo, hi = q["price"]["band"]
    assert lo is None or lo <= hi
    opp_rows = c.get(f"/api/v2/markets/{MARKET}/opportunities/explained", params={"scope": "segment"}).json()["rows"]
    assert q["evidence"]["reasons"] == next(o["reasons"] for o in opp_rows if o["scope_id"] == top["scope_id"])
    md = c.get(f"/api/v2/markets/{MARKET}/requirements", params={"scope": "segment", "id": top["scope_id"], "format": "md"})
    assert md.headers["content-type"].startswith("text/markdown") and "filename*=UTF-8''requirements_" in md.headers["content-disposition"]
    assert md.text.startswith(f"# Product requirements: {top['label']}") and "## Sourcing" in md.text
    assert c.get(f"/api/v2/markets/{MARKET}/requirements", params={"scope": "segment", "id": "nope"}).status_code == 404
    assert c.get(f"/api/v2/markets/{MARKET}/requirements", params={"scope": "category", "id": MARKET}).status_code == 422


# ---------------------------------------------------------------- shopping requirement extraction (spec 43-44)
from dip.knowledge import needs as nd  # noqa: E402


def test_needs_parse_and_check():
    p = nd.parse("brushless micromotor, at least 50k rpm, under $300, 4+ stars", "micromotor")
    reqs = {r["attribute"]: r for r in p["requirements"]}
    assert p["budget_max"] == 300 and p["budget_min"] is None and p["min_rating"] == 4.0
    assert reqs["technology"]["op"] == "eq" and reqs["technology"]["value"] == "brushless"
    assert reqs["max_rpm"]["op"] == "min" and reqs["max_rpm"]["value"] == 50000
    assert nd.parse("micromotor between $100 and $250", "micromotor")["budget_min"] == 100
    assert nd.parse("micromotor up to 35000 rpm", "micromotor")["requirements"][-1]["op"] == "max"
    assert nd.parse("micromotor 35000 rpm", "micromotor")["requirements"][-1]["op"] == "about"
    r = reqs["max_rpm"]
    assert nd.check(r, {"max_rpm": 50000}) == "met" and nd.check(r, {"max_rpm": {"value": 35000}}) == "failed"
    assert nd.check(r, {}) == "unknown" and nd.check(reqs["technology"], {"technology": "Brushless"}) == "met"
    about = {"attribute": "max_rpm", "op": "about", "value": 35000}
    assert nd.check(about, {"max_rpm": 38000}) == "met" and nd.check(about, {"max_rpm": 50000}) == "failed"
    ev = nd.evaluate(pd.DataFrame({"kn_attributes": [json.dumps({"max_rpm": 50000}), json.dumps({"max_rpm": 30000}), "{}"]}), [r])
    assert ev["requirements_failed"].tolist() == [0, 1, 0] and ev["requirement_share"].tolist()[:2] == [1.0, 0.0]
    assert pd.isna(ev["requirement_share"].iloc[2])                            # unknown is not a failure


def test_shopping_reads_requirements_from_the_need(c):
    body = {"need": "micromotor under $1000000", "market": MARKET}
    r = c.post("/api/v2/shopping/recommend-v3", json=body).json()
    assert r["parsed"]["budget_max"] == 1000000
    explicit = c.post("/api/v2/shopping/recommend-v3", json={**body, "budget_max": 5.0}).json()
    assert all(x["price"] <= 5.0 for x in explicit["results"])                   # an explicit field wins
    off = c.post("/api/v2/shopping/recommend-v3", json={**body, "extract_requirements": False}).json()
    assert off["parsed"] is None
    from dip.storage import lake
    P = lake.read_curated("products", MARKET)
    tech = [json.loads(a).get("technology") for a in P["kn_attributes"].dropna()]
    if "brushless" in tech and "brushed" in tech:
        b = c.post("/api/v2/shopping/recommend-v3", json={"need": "brushless micromotor", "market": MARKET, "limit": 50}).json()
        assert b["filtered_out"].get("requirements", 0) >= 1
        assert all(x["requirement_status"] != ["failed"] for x in b["results"])


# ---------------------------------------------------------------- category boundary: enforce mode and impact (M3)
def test_boundary_enforce_and_impact(monkeypatch):
    from dip.pipeline import scope
    defin = {"description": "Dental implant devices and placement instruments, elevators, periotomes. "
                            "Excludes: floss, water flossers, toothbrushes."}
    frame = pd.DataFrame({"category": ["Water Flossers"] * 6 + ["Implant Tools"] * 4,
                          "title": ["cordless water flosser for braces and bridges"] * 6 + ["implant periotome elevator set"] * 4,
                          "revenue": [1000.0] * 6 + [50.0] * 4})
    monkeypatch.setattr(scope, "definitions", lambda: {"mk": defin})
    t = scope.classify(frame, "mk").set_index("category")
    assert t.loc["Water Flossers", "decision"] == "review"                     # 60 % of listings: never auto-excluded
    imp = scope.impact(frame, t.reset_index())
    assert imp["if_enforced"]["categories"] == ["Water Flossers"]
    assert imp["if_enforced"]["listing_share_removed"] == 0.6 and imp["if_enforced"]["revenue_share_removed"] == round(6000 / 6200, 3)
    monkeypatch.setattr(scope, "definitions", lambda: {"mk": {**defin, "boundary": "enforce"}})
    t2 = scope.classify(frame, "mk").set_index("category")
    assert t2.loc["Water Flossers", "decision"] == "out" and "enforced" in t2.loc["Water Flossers", "reason"]
    assert t2.loc["Implant Tools", "decision"] == "in"
    assert scope.impact(frame, t2.reset_index())["by_decision"]["out"]["listing_share"] == 0.6


def test_offline_reference_files_are_valid_and_apply_until_an_upload(tmp_path, monkeypatch):
    """Every curated reference row validates and carries its public source URL."""
    from pathlib import Path
    ref = Path(__file__).resolve().parents[2] / "data" / "reference" / "offline_evidence"
    files = sorted(ref.glob("*.csv"))
    assert files
    for f in files:
        raw = pd.read_csv(f)
        ok, rej = offl.validate(raw)
        assert not rej and len(ok) == len(raw) and ok["url"].str.startswith("https://").all(), f.name
    from dip.storage import lake
    monkeypatch.setattr(lake, "has_curated", lambda name, market: False)
    assert len(offl.load(files[0].stem)) == len(pd.read_csv(files[0]))       # no upload yet: the reference file applies
    assert len(offl.load("no_such_market")) == 0


# ---------------------------------------------------------------- accuracy and ranking stability (M2, spec 130-134)
from dip.knowledge import evaluation as evl  # noqa: E402


def test_confusion_counts_review_apart_and_gates_rates(monkeypatch):
    truth = pd.Series([True] * 20 + [False] * 5)
    band = pd.Series(["strong"] * 16 + ["review"] * 2 + ["non_dental"] * 2 + ["non_dental"] * 4 + ["probable"])
    r = evl.confusion(truth, band)
    assert (r["true_dental"], r["missed_dental"], r["review"], r["true_non_dental"], r["false_dental"]) == (16, 2, 2, 4, 1)
    assert r["dental_recall"] == round(16 / 18, 3)                       # among decided dental rows
    assert r["dental_precision"] == round(16 / 17, 3) and r["non_dental_precision"] == round(4 / 6, 3)
    monkeypatch.setitem(evl.config()["evaluation"], "min_labels", 100)
    assert evl.confusion(truth, band)["dental_recall"] is None             # too few labels: counts only


def test_listing_key_matches_the_v1_listing_id():
    from dmie.cleaning.normalize import make_listing_id
    assert evl.listing_key("B0ABC12345") == make_listing_id("B0ABC12345", "US")


def test_ranking_stability_is_deterministic_and_bounded():
    dims = {f"dim_{d}": v for d, v in zip(opp.DIMENSIONS, [90, None, 50, 40, 70, None, None, 60, 80])}
    rows = [{"scope": "segment", "scope_id": f"s{i}", "label": f"s{i}", "opportunity_score": 80.0 - i * 7,
             **{k: (None if v is None else v - i * 7) for k, v in dims.items()}} for i in range(6)]
    o = pd.DataFrame(rows)
    w = opp.config()["opportunity"]["weights"]
    a, b2 = evl.ranking_stability(o, w), evl.ranking_stability(o, w)
    assert a == b2 and a["status"] == "ok" and a["ranked"] == 6
    assert a["median_spearman"] == 1.0                                   # uniformly separated scopes never swap
    assert a["scopes"][0]["top3_share"] == 1.0 and a["scopes"][-1]["top3_share"] == 0.0
    assert evl.ranking_stability(o.head(2), w)["status"] == "too_few_ranked"


def test_dental_label_check_and_accuracy_api(c):
    from dip.pilot import labels
    smp = labels.draw_sample(MARKET, n=6, n_excluded=0, seed=11)
    its = [it for it in labels.items(smp["id"]) if it["kind"] == "product"]
    assert its and "dental_band" in its[0]["snapshot"] and "taxonomy_node" in its[0]["snapshot"]
    decided = [it for it in its if it["snapshot"]["dental_band"] in ("strong", "probable", "verified_dental")]
    if decided:
        lab = labels.save_label(decided[0]["id"], "dental", {"is_dental": True}, labeller="t")
        assert lab["correct"] is True
    with pytest.raises(ValueError):
        labels.save_label(its[0]["id"], "dental", {}, labeller="t")
    a = c.get(f"/api/v2/markets/{MARKET}/knowledge/accuracy").json()
    assert set(a) >= {"dental", "identity", "taxonomy_decisions", "ranking_stability"}
    assert a["dental"]["gold_benchmark"] is None                        # no gold file for this market
    if decided:
        assert a["dental"]["human_labels"]["true_dental"] >= 1
    rs = a["ranking_stability"]
    assert rs["status"] in ("ok", "too_few_ranked")
    if rs["status"] == "ok":
        assert -1 <= rs["median_spearman"] <= 1 and all(0 <= s["top3_share"] <= 1 for s in rs["scopes"])


# ---------------------------------------------------------------- freshness (M4)
def test_freshness_report_ages_snapshots_and_history_needs(c):
    from datetime import date

    from dip import freshness
    from dip.storage import business as b
    with b.session() as s:
        for d in ("2026-06-01", "2026-07-01"):
            s.add(b.Dataset(id=b.new_id(), market_name=MARKET, source_name=f"{d}.csv", source_kind="test",
                            content_hash=f"h{d}", snapshot_date=d))
    rep = {r["market"]: r for r in freshness.report(today=date(2026, 9, 1))}[MARKET]
    assert rep["latest"] >= "2026-07-01" and rep["dated"] >= 2
    assert rep["snapshots"] >= 3 and rep["growth"]["ready"] and not rep["seasonality"]["ready"]
    old = {r["market"]: r for r in freshness.report(today=date(2027, 6, 1))}[MARKET]
    assert old["stale"] and old["age_days"] > 35 and old["next"]
    api = c.get("/api/v2/freshness").json()
    assert any(r["market"] == MARKET and "inbox" in r for r in api)
    with b.session() as s:
        s.add(b.Market(name="undated_mk", summary={}))
        s.add(b.Dataset(id=b.new_id(), market_name="undated_mk", source_name="x.csv", source_kind="test", content_hash="hx"))
    und = {r["market"]: r for r in freshness.report(today=date(2026, 9, 1))}["undated_mk"]
    assert und["stale"] is None and und["dated"] == 0 and und["next"]              # no snapshot date: age unknown

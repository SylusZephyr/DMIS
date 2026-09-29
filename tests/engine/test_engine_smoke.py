"""Smoke tests for the Universal Market Intelligence Engine: every module
runs and connects to the next. Deliberately basic -- see
docs/universal_engine_architecture.md."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dmie.engine.assistant import ask, explain_market
from dmie.engine.graph import to_cypher
from dmie.engine.ingestion import detect_schema, ingest
from dmie.engine.pain import analyze_reviews
from dmie.engine.pipeline import run_engine, save_suppliers
from dmie.engine.recommend import parse_query, recommend
from dmie.engine.relevance import score_texts
from dmie.engine.simulation import SimulationInput, simulate
from dmie.engine import store

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "data" / "raw" / "dental_models" / "dental_models_sellersprite.xlsx"

TEMPLATES = [
    # (title, brand, base price, base sales, monthly growth)
    ("{b} Brushless Micromotor 50000 RPM Dental Lab Polishing Handpiece", 380, 40, 0.06),
    ("{b} Brushless Micromotor 60000 RPM Dental Lab Handpiece Motor", 520, 25, 0.10),
    ("{b} Brushed Dental Lab Micromotor 35000 RPM N3 H37L1 Handpiece", 95, 120, -0.02),
    ("{b} Denture Base Plate Wax Sheets 20 pcs Dental Lab", 12, 300, 0.01),
    ("{b} Dental Impression Trays Plastic 50 pcs Disposable", 15, 200, 0.02),
]
BRANDS = ["Acme", "Zeno", "Marathon", "Dentix", "Luxor", "Orion"]
NOISE = [("Jewelry Engraving Rotary Tool Kit 35000 RPM", "Craftor", 45, 80),
         ("Electric Nail Drill Manicure Pedicure File 30000 RPM", "Nailpro", 30, 150),
         ("Tooth Shaped Earrings for Women Dangle Jewelry", "Glam", 9, 60)]


def synthetic_dataset(months: int = 8) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    dates = pd.date_range("2025-01-01", periods=months, freq="MS")
    n = 0
    for t_i, (tpl, price, sales, g) in enumerate(TEMPLATES):
        for b_i, brand in enumerate(BRANDS):
            asin = f"B0SYN{t_i}{b_i:02d}XY"
            for m_i, d in enumerate(dates):
                p = round(price * (1 + 0.08 * (b_i - 2.5) / 2.5), 2)
                s = max(1, round(sales * (1 + g) ** m_i * (1 + 0.3 * rng.standard_normal() * 0.3)))
                rows.append({"Item Code": asin, "Product Name": tpl.format(b=brand), "Maker": brand,
                             "Cost to customer": p, "Units/Month": s, "Col_X": round(p * s, 2),
                             "stars": round(4.6 - 0.1 * b_i, 1), "Snapshot": d.strftime("%Y-%m-%d"),
                             "Node": "Dental Lab Equipment"})
                n += 1
    for i, (title, brand, price, sales) in enumerate(NOISE):
        for d in dates:
            rows.append({"Item Code": f"B0NOISE{i}XYZ", "Product Name": title, "Maker": brand, "Cost to customer": price,
                         "Units/Month": sales, "Col_X": price * sales, "stars": 4.1, "Snapshot": d.strftime("%Y-%m-%d"),
                         "Node": "Jewelry & Crafts" if i != 1 else "Nail Care"})
    # an obvious data error, and a duplicate listing of a real product (different ASIN, same product)
    rows.append({"Item Code": "B0BADPRICE1", "Product Name": "Acme Brushless Micromotor 50000 RPM Dental Lab Handpiece",
                 "Maker": "Acme", "Cost to customer": -5, "Units/Month": 10, "Col_X": None, "stars": 9.0,
                 "Snapshot": dates[-1].strftime("%Y-%m-%d"), "Node": "Dental Lab Equipment"})
    rows.append({"Item Code": "B0DUPLIC8X", "Product Name": "Zeno Brushless Micromotor 50000 RPM Dental Lab Polishing Handpiece (Pack of 1)",
                 "Maker": "Zeno", "Cost to customer": 368.0, "Units/Month": 12, "Col_X": 4416.0, "stars": 4.5,
                 "Snapshot": dates[-1].strftime("%Y-%m-%d"), "Node": "Dental Lab Equipment"})
    return pd.DataFrame(rows)


REVIEWS = pd.DataFrame({
    "asin": ["B0SYN000XY"] * 4 + ["B0SYN101XY"] * 3 + ["B0SYN200XY"] * 3,
    "text": [
        "The handpiece gets very hot after ten minutes. Overheating is a real problem.",
        "Great torque and very quiet. Love it.",
        "Motor overheats constantly, I wish it had a cooling fan.",
        "Stopped working after two weeks, broken switch. Terrible.",
        "Excellent power, smooth and reliable.",
        "Too hot to hold after long use.",
        "Works great, easy to use.",
        "Very noisy and vibrates a lot.",
        "Cheap build, flimsy chuck broke quickly.",
        "Good value for a student lab.",
    ],
    "rating": [2, 5, 2, 1, 5, 3, 5, 2, 1, 4],
})

SUPPLIERS = pd.DataFrame({
    "Supplier Name": ["Shenzhen Dental Motor Co", "Global Dental Trading Ltd"],
    "Country": ["China", "Hong Kong"],
    "Business Type": ["Manufacturer", "Trading company"],
    "OEM": ["yes", "no"],
    "ODM": ["yes", "no"],
    "Certifications": ["ISO 13485, CE, FDA", "ISO 9001"],
    "Main Products": ["brushless micromotor, dental lab handpiece motor", "impression trays, dental wax"],
})


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    db = tmp_path_factory.mktemp("engine") / "engine.duckdb"
    save_suppliers(SUPPLIERS, db_path=db)
    r = run_engine(synthetic_dataset(), "micromotor_test", reviews=REVIEWS, db_path=db)
    return r, db


def test_schema_detector_maps_unfamiliar_headers():
    det = detect_schema(synthetic_dataset(3))
    assert det.mapping["title"] == "Product Name"
    assert det.mapping["brand"] == "Maker"
    assert det.mapping["sales"] == "Units/Month"
    assert det.mapping["rating"] == "stars"
    assert det.mapping["timestamp"] == "Snapshot"
    # price/revenue found from the revenue = price x sales identity, not from their headers
    assert det.mapping["price"] == "Cost to customer"
    assert det.mapping["revenue"] == "Col_X"


def test_parent_level_columns_never_count_a_family_once_per_child():
    """A parent's total repeats on every child row: it must not become the listing's id / sales / revenue while the
    file has child-level columns; a file of parent rows only may use it, with a warning."""
    n = 40
    rng = np.random.default_rng(0)
    kids = rng.integers(50, 500, n).astype(float)
    fam = pd.Series(kids).groupby(np.arange(n) // 4).transform("sum").to_numpy()
    price = rng.uniform(10, 50, n).round(2)
    df = pd.DataFrame({"父ASIN": [f"B0PARENT{i // 4:02d}" for i in range(n)], "ASIN": [f"B0CHILD{i:03d}" for i in range(n)],
                       "商品标题": [f"Denture reline kit {i} with acrylic powder and liquid" for i in range(n)],
                       "价格($)": price, "父体销量": fam, "子体销量": kids, "子体销售额($)": kids * price, "父体销售额($)": fam * price})
    det = detect_schema(df)
    assert (det.mapping["id"], det.mapping["sales"], det.mapping["revenue"]) == ("ASIN", "子体销量", "子体销售额($)")
    assert not any("parent-level" in w for w in det.warnings)
    en = df.rename(columns={"父ASIN": "Parent ASIN", "商品标题": "Title", "价格($)": "Price", "父体销量": "Parent Sales",
                            "子体销量": "Monthly Sales"})[["Parent ASIN", "ASIN", "Title", "Price", "Parent Sales", "Monthly Sales"]]
    det = detect_schema(en)
    assert det.mapping["sales"] == "Monthly Sales" and det.mapping.get("revenue") != "Parent Sales"
    parents_only = df[["父ASIN", "商品标题", "价格($)", "父体销量"]].drop_duplicates("父ASIN")
    det = detect_schema(parents_only)
    assert det.mapping["sales"] == "父体销量" and any("parent-level" in w for w in det.warnings)


def test_ingestion_accepts_json_and_csv(tmp_path):
    df = synthetic_dataset(3).head(40)
    df.to_csv(tmp_path / "x.csv", index=False)
    df.to_json(tmp_path / "x.json", orient="records")
    assert len(ingest(tmp_path / "x.csv").frame) == 40
    assert len(ingest(tmp_path / "x.json").frame) == 40
    assert len(ingest({"data": df.to_dict("records")}).frame) == 40


def test_relevance_scores_examples():
    dental, jewelry = score_texts(["Dental laboratory polishing motor", "Jewelry engraving motor"])
    assert dental >= 80 and jewelry <= 30


def test_quality_flags_bad_record(result):
    r, _ = result
    bad = r.records[r.records["id"] == "B0BADPRICE1"].iloc[0]
    assert "invalid_price" in bad["quality_issues"] and "invalid_rating" in bad["quality_issues"]
    assert not bad["usable_for_market"] and bad["excluded_reason"]


def test_identical_content_on_distinct_asins_stays_a_listing():
    """Listing != product: two ASINs with the same title, brand and price are separate listings (often variation
    children with their own sales) and stay usable, flagged; the same ASIN twice in a snapshot is one row too many."""
    from dmie.engine.quality import assess_quality
    from dmie.engine.records import make_record_id

    n = 12
    df = pd.DataFrame({"id": [f"B0VAR{i:05d}" for i in range(n)], "title": [f"Dental lab micromotor model {i}" for i in range(n)],
                       "brand": "Acme", "price": np.linspace(20, 80, n), "sales": [100.0] * n, "revenue": np.nan, "rating": 4.5,
                       "image": "x", "timestamp": pd.Timestamp("2025-01-01"), "attributes": [{} for _ in range(n)]})
    df.loc[1, ["title", "price"]] = df.loc[0, ["title", "price"]].to_numpy()          # B0VAR00001: same content, own ASIN
    df = pd.concat([df, df.iloc[[2]]], ignore_index=True)                              # B0VAR00002 twice
    no_id = df.iloc[[0]].assign(id=None)                                              # same content, no native id
    df = pd.concat([df, no_id], ignore_index=True)
    df["record_id"] = [make_record_id("t", i, k) for k, i in enumerate(df["id"])]
    df.loc[df["id"].isna(), "id"] = df.loc[df["id"].isna(), "record_id"]
    out = assess_quality(df).frame
    twin = out.iloc[1]
    assert "content_duplicate" in twin["quality_issues"] and "duplicate_record" not in twin["quality_issues"]
    assert twin["usable_for_market"]
    assert "duplicate_record" in out.iloc[n]["quality_issues"] and not out.iloc[n]["usable_for_market"]      # repeated ASIN
    assert "duplicate_record" in out.iloc[n + 1]["quality_issues"] and not out.iloc[n + 1]["usable_for_market"]  # no own id


def test_irrelevant_listings_kept_with_reason(result):
    r, _ = result
    noise = r.records[r.records["id"].str.startswith("B0NOISE")]
    assert len(noise) and (~noise["is_relevant"]).all()
    assert noise["excluded_reason"].str.startswith("not relevant").all()


def test_discovery_and_dedup(result):
    r, _ = result
    assert r.summary["discovery"]["segments"] >= 3
    s = r.summary["dedup"]
    assert s["products"] < s["listings"]  # the duplicate listing merged into its product
    dup = r.records[r.records["id"] == "B0DUPLIC8X"]["product_id"].iloc[0]
    orig = r.records[r.records["id"] == "B0SYN001XY"]["product_id"].dropna().iloc[0]
    assert dup == orig
    # the 60k-rpm and 50k-rpm motors must never merge (spec conflict)
    assert r.records.loc[r.records["id"] == "B0SYN101XY", "product_id"].dropna().iloc[0] != orig


def test_market_forecast_opportunity(result):
    r, _ = result
    c = r.summary["category"]
    assert c["monthly_revenue"] > 0 and c["products"] > 0
    assert r.summary["forecast"]["status"] == "ok"
    assert r.summary["forecast"]["periods_observed"] == 8
    assert r.segments["opportunity_score"].between(0, 100).all()


def test_pain_analysis_finds_overheating(result):
    r, _ = result
    rep = r.pain["__market__"]
    assert rep.status == "ok"
    aspects = [c["aspect"] for c in rep.complaints]
    assert "overheating" in aspects
    assert any("cooling" in m["feature"] for m in rep.missing_features)


def test_suppliers_graph_and_persistence(result):
    r, db = result
    assert len(r.suppliers) == 2
    assert r.suppliers.iloc[0]["name"] == "Shenzhen Dental Motor Co"
    kinds = {d["kind"] for _, d in r.graph.nodes(data=True)}
    assert {"category", "family", "segment", "product", "listing", "brand", "supplier"} <= kinds
    assert "MERGE" in to_cypher(r.graph)
    con = store.connect(db)
    try:
        assert "micromotor_test" in store.list_markets(con)
        assert len(store.read_table(con, "mi_products", "micromotor_test")) == len(r.products)
    finally:
        con.close()


def test_simulation_and_recommendation(result):
    r, _ = result
    out = simulate(r.products, SimulationInput("brushless micromotor 50000 rpm", 399.0, 150.0))
    assert out.status == "ok" and out.verdict in {"High success", "Medium success", "Failure risk"}
    loss = simulate(r.products, SimulationInput("brushless micromotor", 50.0, 80.0))
    assert loss.verdict == "Failure risk"
    pq = parse_query("I need a micromotor under $400")
    assert pq.max_price == 400 and "micromotor" in pq.keywords
    _, recs = recommend(r.products, "I need a micromotor under $400")
    assert len(recs) and (recs["price"] <= 400).all()


def test_assistant_offline(result):
    r, _ = result
    assert "real products" in explain_market(r.summary, r.segments)
    assert ask("What is the best opportunity?", r.summary, r.segments, use_ai=False)["mode"] == "offline"


def test_pain_without_reviews_is_honest():
    assert analyze_reviews([]).status == "no_review_data"


@pytest.mark.skipif(not REAL.exists(), reason="real SellerSprite export not present")
def test_real_sellersprite_export_runs_end_to_end():
    r = run_engine(REAL, "dental_models", persist=False)
    assert r.summary["ingestion"]["adapter"] == "sellersprite"
    assert r.summary["dedup"]["products"] < r.summary["dedup"]["listings"]
    assert r.summary["relevance"]["irrelevant"] > 0  # earrings / wall art / toys are filtered
    assert r.summary["forecast"]["status"] == "insufficient_history"  # one snapshot: never a fabricated forecast

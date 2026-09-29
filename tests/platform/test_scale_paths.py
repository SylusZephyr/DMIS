"""Scale paths (used above DIRECT_LIMIT current listings) must stay faithful
to the v1 algorithms they replace."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dmie.engine.dedup import build_product_master, resolve_products
from dmie.engine.discovery import discover_segments
from dmie.engine.ingestion import ingest
from dmie.engine.quality import assess_quality
from dmie.engine.relevance import classify_relevance
from dip.pipeline.clustering import discover
from dip.pipeline.product_resolution.fast import build_product_master_fast, resolve_at_scale
from dip.pipeline.relevance import classify

ROOT = Path(__file__).resolve().parents[2]
MARKETS = [p.parent.name for p in sorted((ROOT / "data" / "raw").glob("*/*_sellersprite.xlsx"))]


@pytest.fixture(scope="module", params=MARKETS)
def market(request):
    f = ROOT / "data" / "raw" / request.param / f"{request.param}_sellersprite.xlsx"
    q = assess_quality(ingest(f).frame).frame
    return request.param, q


def test_unique_text_relevance_matches_v1(market):
    _, q = market
    a, b = classify_relevance(q), classify(q)
    assert (a["relevance_status"].to_numpy() == b["relevance_status"].to_numpy()).all()
    assert (a["relevance_score"] - b["relevance_score"]).abs().max() <= 1.0


def test_vectorised_product_master_equals_v1(market):
    _, q = market
    c = classify_relevance(q)
    v1 = resolve_products(discover_segments(c[c.is_relevant & c.usable_for_market]).frame)
    a, _ = build_product_master(v1.frame)
    b, _ = build_product_master_fast(v1.frame)
    a, b = a.set_index("product_id").sort_index(), b.set_index("product_id").sort_index()
    assert list(a.index) == list(b.index)
    for col in a.columns:
        if col == "listing_ids":
            assert (a[col].map(sorted) == b[col].map(sorted)).all()
        elif col == "attributes":
            assert (a[col] == b[col]).all()
        else:
            x, y = a[col], b[col]
            num = pd.to_numeric(x, errors="coerce").notna().any()
            ok = np.isclose(pd.to_numeric(x, errors="coerce"), pd.to_numeric(y, errors="coerce"), equal_nan=True) if num \
                else ((x == y) | (x.isna() & y.isna())).to_numpy()
            assert ok.all(), col


def test_scale_resolution_agrees_with_v1(market):
    name, q = market
    c = classify_relevance(q)
    d = discover_segments(c[c.is_relevant & c.usable_for_market]).frame
    v1, v2 = resolve_products(d), resolve_at_scale(d)
    g1 = v1.frame.groupby("product_id")["id"].apply(frozenset).to_dict()
    g2 = v2.frame.groupby("product_id")["id"].apply(frozenset).to_dict()
    m1 = v1.frame.set_index("id")["product_id"].map(g1)
    m2 = v2.frame.set_index("id")["product_id"].map(g2).reindex(m1.index)
    assert (m1 == m2).mean() >= 0.95, name


def test_identical_listings_always_one_product():
    base = pd.DataFrame({
        "id": [f"B0TEST{i:04d}" for i in range(6)],
        "title": ["Acme Brushless Micromotor 50000 RPM"] * 3 + ["Zeta Denture Base Wax 20 pcs"] * 3,
        "brand": ["Acme"] * 3 + ["Zeta"] * 3,
        "image": ["https://m.media-amazon.com/images/I/AAA.jpg"] * 3 + ["https://m.media-amazon.com/images/I/BBB.jpg"] * 3,
        "price": [399.0, 409.0, 389.0, 12.0, 12.5, 11.9], "sales": [10, 20, 5, 100, 90, 80],
        "revenue": np.nan, "rating": 4.5, "reviews": np.nan, "seller": None, "url": None,
        "segment_id": ["S0"] * 3 + ["S1"] * 3, "segment_label": ["m"] * 3 + ["w"] * 3, "family_id": "F0",
        "data_confidence": 90.0,
    })
    r = resolve_at_scale(base)
    assert r.frame.groupby("title")["product_id"].nunique().eq(1).all()
    assert r.products["product_id"].nunique() == 2


def test_sampled_discovery_assigns_every_listing():
    q = assess_quality(ingest(ROOT / "data" / "raw" / "dental_models" / "dental_models_sellersprite.xlsx").frame).frame
    c = classify_relevance(q)
    cur = c[c.is_relevant & c.usable_for_market]
    res = discover(cur, direct_limit=0, sample_size=150)  # force the scale path with a small fit sample
    assert res.frame["segment_id"].notna().all() and res.frame["segment_label"].notna().all()
    assert res.segments["listings"].sum() == len(cur)
    assert "nearest centroid" in res.method["scale"]

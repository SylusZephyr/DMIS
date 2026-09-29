"""Exports (Phase D): CSV / XLSX tables and the decision memo, with the same auth and market scope as the pages."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))

MARKET = "exp_market"


@pytest.fixture(scope="module")
def c(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_exports")
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

    from dip.pipeline import runner
    from dip.api.app import app
    p = d / "m.csv"
    synthetic_dataset(months=3).to_csv(p, index=False)
    runner.process_dataset(p, MARKET, source_name="m.csv")
    yield TestClient(app), mp
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def _csv(r) -> pd.DataFrame:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")                        # BOM: Chinese opens correctly in spreadsheets
    return pd.read_csv(io.BytesIO(r.content), encoding="utf-8-sig")


def test_segments_csv_matches_api(c):
    client, _ = c
    api = client.get(f"/api/v2/markets/{MARKET}/segments-v3").json()
    df = _csv(client.get(f"/api/v2/markets/{MARKET}/export/segments?format=csv"))
    assert len(df) == len(api) > 0
    assert list(df["segment_id"]) == [s["segment_id"] for s in api]      # same rows, same order as the page
    by_id = {s["segment_id"]: s for s in api}
    for _, row in df.iterrows():
        assert row["revenue_est"] == pytest.approx(by_id[row["segment_id"]]["revenue_est"])
        assert row["revenue_lo"] <= row["revenue_est"] <= row["revenue_hi"]


def test_brands_and_products_xlsx(c):
    client, _ = c
    r = client.get(f"/api/v2/markets/{MARKET}/export/brands?format=xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    brands = pd.read_excel(io.BytesIO(r.content), engine="openpyxl")
    api = client.get(f"/api/v2/markets/{MARKET}/competitors-v3?limit=1000").json()["brands"]
    assert len(brands) == len(api) > 0
    assert brands["share_est"].sum() == pytest.approx(sum(b["share_est"] for b in api), rel=1e-6)

    prods = pd.read_excel(io.BytesIO(client.get(f"/api/v2/markets/{MARKET}/export/products?format=xlsx").content), engine="openpyxl")
    total = client.get(f"/api/v2/markets/{MARKET}/products?limit=1").json()["total"]
    assert len(prods) == total and "image" not in prods.columns
    assert {"product_id", "title", "revenue_est", "revenue_lo", "revenue_hi"} <= set(prods.columns)


def test_bad_table_format_and_market(c):
    client, _ = c
    assert client.get(f"/api/v2/markets/{MARKET}/export/segments?format=pdf").status_code == 400
    assert client.get(f"/api/v2/markets/{MARKET}/export/listings").status_code == 404
    assert client.get("/api/v2/markets/no_such_market/export/segments").status_code == 404


def test_opportunity_board_export_matches_board(c):
    client, _ = c
    items = client.get("/api/v2/opportunities-v3").json()["items"]
    df = _csv(client.get("/api/v2/export/opportunities?format=csv"))
    assert len(df) == len(items) > 0
    first = items[0]
    row = df[(df["market"] == first["market"]) & (df["segment_id"] == first["segment_id"])].iloc[0]
    assert row["opportunity_index"] == pytest.approx(first["opportunity_index"])
    if (first.get("engine") or {}).get("score") is not None:                  # the explainable score travels with the row
        assert row["opportunity_score"] == pytest.approx(first["engine"]["score"])
    if "units" in (first.get("launch") or {}):
        assert row["units_p10"] <= row["units_median"] <= row["units_p90"]
        assert row["revenue_median"] == pytest.approx(first["launch"]["revenue"]["median"])
    one = _csv(client.get(f"/api/v2/export/opportunities?format=csv&market={MARKET}"))
    assert set(one["market"]) == {MARKET}


def test_decision_memo_markdown_and_html(c):
    client, _ = c
    row = client.get("/api/v2/opportunities-v3").json()["items"][0]
    q = f"market={row['market']}&segment_id={row['segment_id']}"
    md = client.get(f"/api/v2/export/memo?{q}&format=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    text = md.text
    assert text.startswith("# Decision memo") and row["segment_label"] in text and row["market"] in text
    for needle in ("Opportunity score", "Opportunity index", "Evidence grade", "Methodology references", "M12.1", "M7.2", "Risks"):
        assert needle in text
    if "units" in (row.get("launch") or {}):
        assert f"{row['launch']['units']['median']:,.0f}" in text          # the board's own number, not re-computed
    zh = client.get(f"/api/v2/export/memo?{q}&format=html&lang=zh")
    assert zh.status_code == 200 and zh.headers["content-type"].startswith("text/html")
    assert "决策备忘录" in zh.text and "<script" not in zh.text
    assert client.get(f"/api/v2/export/memo?market={row['market']}&segment_id=nope").status_code == 404
    assert client.get(f"/api/v2/export/memo?{q}&format=docx").status_code == 400


def test_cells_that_look_like_formulas_are_neutralised():
    from dip.api.routes.exports import _cell

    assert _cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    assert _cell("@SUM(A1)") == "'@SUM(A1)"
    assert _cell("plain title") == "plain title" and _cell(-3.5) == -3.5
    assert _cell(["a", "b"]) == '["a", "b"]'


def test_exports_enforce_auth_and_market_scope(c):
    client, mp = c
    from dip.auth import create_user

    _, admin = create_user("exp-admin@example.com", "Admin", "admin")
    _, viewer = create_user("exp-viewer@example.com", "Viewer", "viewer")
    _, pm = create_user("exp-pm@example.com", "PM", "product_manager", employee_id="emp_without_categories")
    _, cust = create_user("exp-cust@example.com", "Customer", "customer")
    mp.setenv("DIP_AUTH", "on")
    try:
        H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
        seg = f"/api/v2/markets/{MARKET}/export/segments?format=csv"
        seg_id = client.get("/api/v2/opportunities-v3", headers=H(admin)).json()["items"][0]["segment_id"]
        memo = f"/api/v2/export/memo?market={MARKET}&segment_id={seg_id}"
        assert client.get(seg).status_code == 401                                   # no token
        assert client.get(memo).status_code == 401
        for tok in (admin, viewer):
            assert client.get(seg, headers=H(tok)).status_code == 200
            assert client.get(memo, headers=H(tok)).status_code == 200
        # a product manager without this market's category sees none of it
        assert client.get(seg, headers=H(pm)).status_code == 403
        assert client.get(f"/api/v2/markets/{MARKET}/export/products", headers=H(pm)).status_code == 403
        assert client.get(memo, headers=H(pm)).status_code == 403
        assert client.get(f"/api/v2/export/opportunities?market={MARKET}", headers=H(pm)).status_code == 403
        pm_all = _csv(client.get("/api/v2/export/opportunities", headers=H(pm)))
        assert len(pm_all) == 0                                                      # nothing outside their scope
        # customers: shopping mode only
        assert client.get(seg, headers=H(cust)).status_code == 403
        assert client.get("/api/v2/export/opportunities", headers=H(cust)).status_code == 403
    finally:
        mp.setenv("DIP_AUTH", "off")

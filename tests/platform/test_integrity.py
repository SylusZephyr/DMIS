"""Truth harness (src/dip/metrics/integrity.py): it must catch the inconsistencies it exists for -- a number stored
twice with different values, an estimate outside its bounds, a trend without history, an unvalidated model -- and
stay quiet about what is consistent."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "engine"))
MARKET = "micromotor"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    d = tmp_path_factory.mktemp("integrity")
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
    synthetic_dataset(months=1).to_csv(p, index=False)
    runner.process_dataset(p, MARKET, source_name="m.csv", snapshot_date="2025-01-01")
    yield TestClient(app)
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def _by_id(rep: dict) -> dict:
    return {c["id"]: c for c in rep["checks"]}


def test_processing_records_the_harness_and_the_api_serves_it(api):
    from dip.storage import business as b

    with b.session() as s:
        m = s.get(b.Market, MARKET)
        job = s.get(b.Job, m.run_id)
        integ = (m.summary or {}).get("integrity")
    assert integ and integ["status"] in ("pass", "warn", "fail") and sum(integ["counts"].values()) >= 8
    assert any(x["name"] == "integrity" and x["status"] == "done" for x in job.stages)
    rep = api.get(f"/api/v2/markets/{MARKET}/integrity").json()
    checks = _by_id(rep)
    assert {"market_revenue_single_value", "segments_add_up", "estimates_within_bounds", "demand_model_validated",
            "trend_needs_history", "snapshot_date_declared"} <= set(checks)
    assert checks["segments_add_up"]["status"] == "pass"                  # additivity holds by construction
    assert checks["single_interval"]["status"] == "pass"                  # capacity uses the engine's joint interval
    assert checks["market_revenue_single_value"]["status"] == "pass", checks["market_revenue_single_value"]["detail"]
    assert checks["segment_table_revenue_meaning"]["status"] == "pass", checks["segment_table_revenue_meaning"]["detail"]
    assert checks["one_opportunity_score"]["status"] == "pass", checks["one_opportunity_score"]["detail"]
    with b.session() as s:
        su = s.get(b.Market, MARKET).summary
    top = su["metrics_v3"]["top_segment"]
    # the market's best segment carries the engine's score (opportunity_engine.top_score also covers taxonomy nodes)
    assert su["opportunity"]["top_score"] == checks["one_opportunity_score"]["values"]["engine_best"]
    assert su["opportunity"]["top_score"] <= su["opportunity_engine"]["top_score"]
    assert top is None or top["ranked_by"] in ("opportunity_score", "opportunity_index")
    assert checks["snapshot_date_declared"]["status"] == "pass"           # the snapshot date was given at import
    assert api.get("/api/v2/markets/nope/integrity").status_code == 404
    # every market-size surface reads the same headline (observed floor) with the modelled estimate beside it
    rm = su["metrics_v3"]["revenue_month"]
    row = next(m for m in api.get("/api/v2/markets").json() if m["name"] == MARKET)
    assert row["revenue_headline"] == rm["headline"] == rm["floor"] and row["revenue_est"] == rm["estimate"]
    assert row["model_validated"] == rm["model_validated"] and row["revenue_basis"].startswith("observed")
    und = rm["undecided"]                        # E4: revenue awaiting a scope decision is disclosed, never dropped
    assert und is not None and und["floor"] <= rm["floor"] + 1e-6 and und["decided_floor"] == pytest.approx(rm["floor"] - und["floor"], abs=0.02)
    ov = api.get("/api/v2/overview").json()
    assert ov["revenue_headline"] == pytest.approx(rm["floor"]) and ov["models_validated"] + ov["models_not_validated"] == 1


def test_harness_catches_planted_inconsistencies(api, monkeypatch):
    from dip.metrics import integrity
    from dip.storage import business as b

    with b.session() as s:
        summary = dict(s.get(b.Market, MARKET).summary)
    v3 = dict(summary["metrics_v3"])
    rev = dict(v3["revenue_month"])
    # an estimate below its own floor, a trend from one snapshot, an unvalidated model, an undeclared snapshot date
    v3["revenue_month"] = {**rev, "floor": rev["estimate"] * 2, "low": rev["estimate"] * 0.5}
    v3["demand_model"] = {**v3.get("demand_model", {}), "crossvalidation": {"badged_listings": 3, "auc_badged_vs_unbadged": 0.4,
                                                                           "coverage_80": 0.1, "coverage_95": 0.2}}
    v3["as_of_basis"] = "latest launch date in the data (no snapshot date declared)"
    bad = {**summary, "metrics_v3": v3, "trend": {"trend": "Mature"},
           "forecast": {"history": [["2025-01-01", rev["estimate"] * 3]]}}
    c = _by_id(integrity.check_market(MARKET, bad))
    assert c["estimates_within_bounds"]["status"] == "fail"
    assert c["trend_needs_history"]["status"] == "fail" and "1 snapshot" in c["trend_needs_history"]["detail"]
    dm = c["demand_model_validated"]
    assert dm["status"] == "fail" and "only 3 listings" in dm["detail"] and "AUC 0.40" in dm["detail"]
    assert c["snapshot_date_declared"]["status"] == "warn"
    assert c["market_revenue_single_value"]["status"] == "fail" and "forecast_history_latest" in c["market_revenue_single_value"]["detail"]
    # an open top rung (no ceiling) must not switch the bounds check off: implants had low < floor unnoticed
    open_top = {**rev, "ceiling": None, "floor": rev["low"] + 1.0}
    c2 = _by_id(integrity.check_market(MARKET, {**summary, "metrics_v3": {**summary["metrics_v3"], "revenue_month": open_top}}))
    assert c2["estimates_within_bounds"]["status"] == "fail" and "floor" in c2["estimates_within_bounds"]["detail"]
    # and a consistent market passes the same checks
    ok = _by_id(integrity.check_market(MARKET, {**summary, "trend": {"trend": "Insufficient evidence"}}))
    assert ok["trend_needs_history"]["status"] == "pass"


def test_segment_additivity_is_checked_against_the_stored_rows(api, monkeypatch):
    from dip.metrics import integrity

    real = integrity._read

    def tampered(table, market):
        df = real(table, market)
        if table == "metrics":
            df = df.copy()
            m = (df["scope"] == "segment") & (df["metric"] == "revenue_month")
            df.loc[df[m].index[0], "value"] = df.loc[df[m].index[0], "value"] + 1e6   # one segment inflated
        return df

    monkeypatch.setattr(integrity, "_read", tampered)
    c = _by_id(integrity.check_market(MARKET))
    assert c["segments_add_up"]["status"] == "fail"
    assert isinstance(pd.Series([c["segments_add_up"]["values"]["segment_sum"]]).iat[0], float)


def test_one_reader_for_market_revenue():
    """Maps, trend lists and the analyst read the market revenue through dip.metrics.facts, never the older
    category sum under the same name."""
    from dip.metrics.facts import market_revenue

    v3 = {"metrics_v3": {"revenue_month": {"estimate": 171500.0, "low": 90000.0, "high": 260000.0, "floor": 64900.0,
                                           "headline": 64900.0, "headline_basis": "observed: floors", "model_validated": False}},
          "category": {"monthly_revenue": 63700.0}}
    r = market_revenue(v3)
    assert r["headline"] == 64900.0 and r["estimate"] == 171500.0 and r["model_validated"] is False
    old = market_revenue({"category": {"monthly_revenue": 63700.0}})
    assert old["headline"] == 63700.0 and "before metrics v3" in old["basis"] and old["estimate"] is None
    assert market_revenue(None)["headline"] is None


def test_trend_and_forecast_report_one_growth_number(api):
    """Three dated snapshots of a growing market: the trend's expected 12-month growth is the forecast's (v3
    revenue history), never a second number from the older forecast on badge floors."""
    import tempfile

    from test_engine_smoke import synthetic_dataset

    from dip.metrics import integrity
    from dip.pipeline import runner
    from dip.storage import business as b

    base = synthetic_dataset(months=1)
    col = next(c for c in ("Units/Month", "Monthly Sales", "sales") if c in base.columns)
    for i, day in enumerate(("2025-01-01", "2025-02-01", "2025-03-01")):
        df = base.copy()
        df[col] = (pd.to_numeric(df[col], errors="coerce") * 1.1 ** i).round()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            df.to_csv(fh.name, index=False)
        runner.process_dataset(fh.name, "growmkt", source_name=f"g{i}.csv", snapshot_date=day)
    with b.session() as s:
        su = s.get(b.Market, "growmkt").summary
    fc, tr = su["forecast"], su["trend"]
    assert fc["status"] == "ok" and fc["periods"] == 3 and fc["growth_12m"] > 0
    assert tr["expected_growth_12m"] == pytest.approx(fc["growth_12m"], abs=1e-3)
    assert "v3" in tr["expected_growth_basis"]
    c = _by_id(integrity.check_market("growmkt"))
    assert c["growth_single_value"]["status"] == "pass"


def test_excluded_sales_are_disclosed():
    """Observed sales on a relevant listing that is in no total (e.g. no price) are disclosed; a repeated row of a
    counted listing is not (its sales are counted once); irrelevant listings are out of scope for the check."""
    import pandas as pd

    from dip.metrics.integrity import _excluded_sales

    li = pd.DataFrame({"id": ["A", "B"], "record_id": ["r1", "r2"], "sales": [100.0, 300.0]})
    rec = pd.DataFrame({"id": ["A", "B", "C", "B", "D"], "record_id": ["r1", "r2", "r3", "r4", "r5"],
                        "sales": [100.0, 300.0, 50.0, 300.0, 80.0], "is_relevant": [True, True, True, True, False],
                        "rejection_reasons": ["[]", "[]", '["missing price"]', "[]", "[]"],
                        "quality_issues": ["[]", "[]", "[]", '["duplicate_record"]', "[]"]})
    chk = _excluded_sales(rec, li, 0.0)
    assert chk["status"] == "warn" and chk["values"]["listings"] == 1 and chk["values"]["units"] == 50.0
    assert chk["values"]["share"] == round(50 / 450, 4) and "missing price x1" in chk["detail"]
    ok = _excluded_sales(rec[rec["id"] != "C"], li, 0.0)
    assert ok["status"] == "pass" and ok["values"]["listings"] == 0

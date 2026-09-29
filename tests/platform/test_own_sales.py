"""Own sales (src/dip/own_sales.py): Business Report import (English and Chinese headers), monthly normalisation,
exact project actuals, and the check of market estimates against your real sales."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "engine"))

MARKET = "micromotor"
ASIN = "B0SYN000XY"


def en_report(units: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({
        "(Parent) ASIN": [ASIN, "B0PARENT01", ""], "(Child) ASIN": [ASIN, "B0OTHER001", "Total"],
        "Title": ["Acme micromotor", "Other", ""], "Sessions - Total": ["1,200", "300", "1,500"],
        "Page Views - Total": ["1,800", "450", "2,250"], "Units Ordered": [units, 7, units + 7],
        "Unit Session Percentage": ["8.33%", "2.33%", ""], "Ordered Product Sales": ["$38,000.00", "$99.00", ""],
        "Total Order Items": [96, 7, 103]})


def test_read_maps_english_and_chinese_business_reports():
    from dip.own_sales import read

    df, meta = read(en_report())
    assert meta["mapping"]["asin"] == "(Child) ASIN" and meta["mapping"]["units"] == "Units Ordered"
    assert meta["mapping"]["revenue"] == "Ordered Product Sales" and meta["mapping"]["parent_asin"] == "(Parent) ASIN"
    assert len(df) == 2 and any("without an ASIN" in n for n in meta["notes"])          # the totals line is not a listing
    r = df.set_index("asin").loc[ASIN]
    assert r["units"] == 100 and r["revenue"] == 38000 and r["sessions"] == 1200 and r["conversion"] == pytest.approx(0.0833)
    zh = pd.DataFrame({"（子）ASIN": [ASIN], "商品名称": ["x"], "已订购商品数量": ["1,234"], "已订购商品销售额": ["US$5,000.00"],
                       "会话次数 – 总计": [900]})
    df2, meta2 = read(zh)
    assert meta2["mapping"]["units"] == "已订购商品数量" and df2["units"].iat[0] == 1234 and df2["sessions"].iat[0] == 900
    with pytest.raises(ValueError, match="units"):
        read(pd.DataFrame({"(Child) ASIN": [ASIN], "Sessions": [3]}))


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    d = tmp_path_factory.mktemp("own")
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
    df = synthetic_dataset(months=1)
    p = d / "m.csv"
    df.to_csv(p, index=False)
    runner.process_dataset(p, MARKET, source_name="m.csv", snapshot_date="2025-01-01")
    est = float(df.loc[df["Item Code"] == ASIN, "Units/Month"].iat[0])
    yield TestClient(app), est
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def _upload(c, units, start="2024-12-18", end="2025-01-16"):              # 30 days around the snapshot date
    buf = io.BytesIO()
    en_report(units).to_csv(buf, index=False)
    return c.post("/api/v2/own-sales", files={"file": ("BusinessReport.csv", buf.getvalue())},
                  data={"period_start": start, "period_end": end})


def test_import_estimate_check_and_exact_actuals(api):
    from dip import own_sales
    from dip.projects import actuals

    c, est = api
    units = round(2 * est * 30 / 30.44, 4)                                  # twice the estimate, per month
    r = _upload(c, units)
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 2 and r.json()["period"] == ["2024-12-18", "2025-01-16"]
    again = _upload(c, units).json()
    assert again["replaced"] == 2                                            # same ASIN + period: replaced, not doubled
    rows = c.get("/api/v2/own-sales").json()["rows"]
    mine = next(x for x in rows if x["asin"] == ASIN)
    assert len(rows) == 2 and mine["days"] == 30 and mine["units_month"] == pytest.approx(2 * est, abs=0.01)
    chk = c.get("/api/v2/own-sales/estimate-check").json()
    row = next(x for x in chk["rows"] if x["asin"] == ASIN)
    assert row["status"] == "compared" and row["market"] == MARKET and row["estimate"] == est
    assert row["ratio_actual_to_estimate"] == pytest.approx(2.0, abs=0.01) and row["estimate_kind"] != "badge"
    assert next(x for x in chk["rows"] if x["asin"] == "B0OTHER001")["status"] == "not_observed"
    assert chk["compared"] == 1 and chk["median_ratio"] == pytest.approx(2.0, abs=0.01)
    # project actuals: your exact sales replace the market's number for that month; other months keep the market's
    act = actuals(MARKET, [ASIN])
    assert list(act["source"]) == ["own_sales"] and act["sales"].iat[0] == pytest.approx(2 * est, abs=0.01)
    assert own_sales.monthly(["B0NOTHERE1"]).empty
    assert _upload(c, 1, start="2025-02-01", end="2025-01-01").status_code == 400    # period ends before it starts
    # the truth harness compares your sales with the model's estimate for the same listing (the only truth for
    # listings without a badge); one listing is reported but too few to judge
    from dip.metrics import integrity
    chk = next(x for x in integrity.check_market(MARKET)["checks"] if x["id"] == "model_vs_own_sales")
    assert chk["status"] == "warn" and chk["values"]["listings"] == 1 and "too few" in chk["detail"]
    assert chk["values"]["median_ratio"] is not None


def test_model_vs_own_sales_verdicts(monkeypatch):
    """Enough of your listings: pass when the model's 95 % intervals hold your actual units, fail when they do not;
    report periods far from the snapshot are ignored."""
    from dip import own_sales as osm
    from dip.metrics import integrity

    as_of = pd.Timestamp("2025-01-01")
    li = pd.DataFrame({"id": ["b1", "b2", "b3", "b4"], "units_est": [10.0, 20.0, 30.0, 40.0],
                       "units_lo": [5.0, 10.0, 15.0, 20.0], "units_hi": [15.0, 30.0, 45.0, 60.0],
                       "sales": [None, 20.0, None, 40.0]})
    monkeypatch.setattr(integrity, "_read", lambda table, market: li)
    c = {**integrity._cfg(), "own_sales_min_listings": 3, "own_sales_max_days_apart": 45}

    def report(units, far=()):
        mid = [as_of + pd.Timedelta(days=200 if a in far else 3) for a in ("B1", "B2", "B3", "B4")]
        return pd.DataFrame({"asin": ["B1", "B2", "B3", "B4"], "units_month": units, "midpoint": mid})

    monkeypatch.setattr(osm, "_frame", lambda asins=None: report([12.0, 18.0, 33.0, 50.0]))
    ok = integrity._own_sales_vs_model("m", as_of, c)
    assert ok["status"] == "pass" and ok["values"]["listings"] == 4 and ok["values"]["coverage_95"] == 1.0
    assert ok["values"]["unbadged"] == 2 and ok["values"]["median_ratio"] == pytest.approx(1.15)   # ratios 1.2, 0.9, 1.1, 1.25

    monkeypatch.setattr(osm, "_frame", lambda asins=None: report([40.0, 90.0, 33.0, 200.0]))
    bad = integrity._own_sales_vs_model("m", as_of, c)
    assert bad["status"] == "fail" and bad["values"]["coverage_95"] == 0.25 and "need" in bad["detail"]

    # two of four reports are months away from the snapshot: only two listings compared, too few to judge
    monkeypatch.setattr(osm, "_frame", lambda asins=None: report([12.0, 18.0, 33.0, 50.0], far=("B3", "B4")))
    few = integrity._own_sales_vs_model("m", as_of, c)
    assert few["status"] == "warn" and few["values"]["listings"] == 2

    monkeypatch.setattr(osm, "_frame", lambda asins=None: pd.DataFrame())
    assert integrity._own_sales_vs_model("m", as_of, c) is None

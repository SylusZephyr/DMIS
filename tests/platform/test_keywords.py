"""Keyword intelligence (src/dip/keywords.py): header mapping (Chinese and English exports), parsing, sub-category
assignment through the market's listings, scoring, gaps and the API."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "engine"))

MARKET = "micromotor"


def zh_export() -> pd.DataFrame:
    """Shaped like a SellerSprite keyword-research export: Chinese headers, rates as percent text, a title line."""
    rows = [
        ["brushless micromotor", "无刷微型马达", "12,400", "620", "5.0%", "18", "12", "340", "18.3", "$1.85", "42.1%"],
        ["dental lab micromotor", "牙科技工室马达", "8,100", "486", "6.0%", "22", "3", "210", "25.0", "$1.20", "35.0%"],
        ["micromotor polishing handpiece", "抛光手机", "2,300", "92", "4.0%", "6", "2", "95", "24.2", "$0.95", "30.2%"],
        ["denture base plate wax", "基托蜡片", "5,600", "448", "8.0%", "15", "9", "150", "37.3", "$0.80", "28.0%"],
        ["dental impression trays", "印模托盘", "9,900", "396", "4.0%", "12", "14", "400", "24.8", "$1.10", "55.0%"],
        ["jewelry engraving pen", "雕刻笔", "3,000", "60", "2.0%", "3", "11", "900", "3.3", "$0.60", "61.0%"],
        ["", "", "", "", "", "", "", "", "", "", ""],                                    # an empty line
        ["Brushless Micromotor", "重复", "11,000", "500", "4.5%", "16", "12", "340", "18.3", "$1.85", "42.1%"],  # duplicate
    ]
    header = ["关键词", "关键词翻译", "月搜索量", "月购买量", "购买率", "SPR", "标题密度", "商品数", "供需比", "PPC竞价", "点击总占比"]
    title = [["关键词挖掘 - micromotor - 2026-09"] + [""] * (len(header) - 1)]
    return pd.DataFrame(title + [header] + rows)


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    d = tmp_path_factory.mktemp("kw")
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
    runner.process_dataset(p, MARKET, source_name="m.csv")
    yield TestClient(app)
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def test_read_maps_chinese_headers_and_parses_values():
    from dip.keywords import read

    kw, meta = read(zh_export())
    m = meta["mapping"]
    assert m["keyword"] == "关键词" and m["searches"] == "月搜索量" and m["purchase_rate"] == "购买率"
    assert m["title_density"] == "标题密度" and m["ppc_bid"] == "PPC竞价" and m["click_share"] == "点击总占比"
    assert m["translation"] == "关键词翻译"
    assert len(kw) == 6                                                     # blank line and duplicate merged, both noted
    assert any("without a keyword" in n for n in meta["notes"]) and any("more than once" in n for n in meta["notes"])
    r = kw.set_index("keyword").loc["brushless micromotor"]
    assert r["searches"] == 12400 and r["purchase_rate"] == pytest.approx(0.05) and r["ppc_bid"] == pytest.approx(1.85)
    assert r["click_share"] == pytest.approx(0.421)


def test_read_english_headers_and_plain_rates():
    from dip.keywords import read

    df = pd.DataFrame({"Keyword": ["a b", "c d"], "Searches": [100, 200], "Purchase Rate": [12.5, 3], "Title Density": [1, 2],
                       "Products": [50, 60], "PPC Bid": ["$0.5", "$0.7"], "Extra": [1, 2]})
    kw, meta = read(df)
    assert kw["purchase_rate"].tolist() == [0.125, 0.03] and meta["unmapped"] == ["Extra"]
    with pytest.raises(ValueError, match="searches"):
        read(pd.DataFrame({"Keyword": ["x"], "Something": [1]}))


def test_score_prefers_demand_with_less_competition_and_flags_gaps():
    from dip.keywords import read, score

    kw = score(read(zh_export())[0]).set_index("keyword")
    assert kw["coverage"].iat[0] == 1.0 and kw["score"].between(0, 100).all()
    assert kw.loc["dental lab micromotor", "score"] > kw.loc["jewelry engraving pen", "score"]
    # high demand, few listings naming it in their title -> gap; heavily targeted keywords are not
    assert kw.loc["dental lab micromotor", "is_gap"] and not kw.loc["dental impression trays", "is_gap"]
    assert not kw.loc["jewelry engraving pen", "is_gap"]                    # low demand is not a gap


def test_import_assigns_keywords_to_sub_categories_through_listings(api):
    from dip.storage import lake

    buf = io.BytesIO()
    zh_export().to_excel(buf, index=False, header=False)
    r = api.post(f"/api/v2/markets/{MARKET}/keywords", files={"file": ("kw.xlsx", buf.getvalue())})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["keywords"] == 6 and out["mapping"]["searches"] == "月搜索量"
    li = lake.read_curated("listings", MARKET, columns=["title", "segment_id"])
    wax_seg = li[li["title"].str.contains("Wax Sheets")]["segment_id"].mode().iat[0]
    rows = {x["keyword"]: x for x in api.get(f"/api/v2/markets/{MARKET}/keywords?limit=50").json()["rows"]}
    assert rows["denture base plate wax"]["segment_id"] == wax_seg and rows["denture base plate wax"]["assign_share"] == 1.0
    assert rows["jewelry engraving pen"]["segment_id"] is None                # matches no listing: kept, unassigned
    assert rows["dental lab micromotor"]["matched_listings"] >= 2
    s = api.get(f"/api/v2/markets/{MARKET}/keywords/summary").json()
    assert s["assigned"] + s["unassigned"] == 6 and s["segments"] and "searches" in s["columns"]
    wax = next(x for x in s["segments"] if x["segment_id"] == wax_seg)
    assert wax["searches"] == 5600 and wax["top_keywords"] == ["denture base plate wax"]
    gaps = api.get(f"/api/v2/markets/{MARKET}/keywords?gaps=true").json()
    assert gaps["total"] >= 1 and all(x["is_gap"] for x in gaps["rows"])
    un = api.get(f"/api/v2/markets/{MARKET}/keywords?segment=unassigned").json()["rows"]
    assert "jewelry engraving pen" in {x["keyword"] for x in un}
    by_search = api.get(f"/api/v2/markets/{MARKET}/keywords?sort=searches").json()["rows"]
    assert by_search[0]["keyword"] == "brushless micromotor" and isinstance(by_search[0]["components"], dict)
    bad = api.post(f"/api/v2/markets/{MARKET}/keywords", files={"file": ("x.csv", b"foo,bar\n1,2\n")})
    assert bad.status_code == 400 and "keywords.yaml" in bad.json()["detail"]

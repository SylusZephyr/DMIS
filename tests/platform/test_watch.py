"""Competitor watchlist (src/dip/watch.py): change rules, the snapshot path (processed uploads) and the live path
(Keepa, recorded response shapes, no network)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "engine"))
import acquire_fixtures as fx  # noqa: E402

WATCHED = "B0SYN000XY"                      # a listing of the synthetic micromotor export


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setenv("DIP_AUTH", "off")
    monkeypatch.setenv("DIP_FORECAST_WORKERS", "1")
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_JOB_MODE", "DIP_KEEPA_API_KEY",
                "DIP_SCRAPER_API_KEY", "DIP_SCRAPER_API_URL", "DIP_PAAPI_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()
    yield tmp_path
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def pt(day: str, **v) -> dict:
    return {"observed_at": day, "source": "snapshot:test", **{m: v.get(m) for m in ("price", "sales", "bsr", "rating", "reviews")}}


def test_changes_report_only_moves_that_matter():
    from dip.watch import changes

    got = {c["metric"]: c for c in changes([
        pt("2026-08-01", price=20.0, sales=100, bsr=5000, rating=4.5, reviews=100),
        pt("2026-09-01", price=17.0, sales=200, bsr=5500, rating=4.2, reviews=125),
    ])}
    assert set(got) == {"price", "sales", "rating", "reviews"}               # BSR +10 % is below the 30 % bar
    assert got["price"]["change"] == pytest.approx(-0.15) and got["sales"]["badge_tier"]
    assert got["rating"]["from"] == 4.5 and got["rating"]["to"] == 4.2
    # small moves are not noise-alerts; a rating that improves is not a drop
    assert changes([pt("a", price=20.0, rating=4.2), pt("b", price=20.6, rating=4.6)]) == []
    # a metric missing from the latest observation compares its own last two values
    assert [c["metric"] for c in changes([pt("a", price=10.0), pt("b", price=12.0), pt("c", rating=4.0)])] == ["price"]
    # a non-badge sales estimate needs a 25 % move
    assert changes([pt("a", sales=103), pt("b", sales=110)]) == []
    assert changes([pt("a", sales=103), pt("b", sales=140)])[0]["badge_tier"] is False


def test_watchlist_follows_processed_snapshots_and_alerts_once(env):
    from test_engine_smoke import synthetic_dataset

    from dip import watch
    from dip.pipeline import runner
    from dip.storage import business as b

    df = synthetic_dataset(months=1)
    p1 = env / "jan.csv"
    df.to_csv(p1, index=False)
    runner.process_dataset(p1, "micromotor", source_name="jan.csv", snapshot_date="2025-01-01")
    item = watch.add(WATCHED.lower(), "micromotor", by="tester")
    assert item["asin"] == WATCHED and item["label"]                        # label taken from the listing title
    assert watch.add(WATCHED, "micromotor")["id"] == item["id"]             # adding again keeps one item
    with pytest.raises(ValueError):
        watch.add("not-an-asin")

    feb = df.assign(Snapshot="2025-02-01")
    price0 = float(feb.loc[feb["Item Code"] == WATCHED, "Cost to customer"].iat[0])
    feb.loc[feb["Item Code"] == WATCHED, "Cost to customer"] = round(price0 * 0.8, 2)   # a 20 % price cut
    p2 = env / "feb.csv"
    feb.to_csv(p2, index=False)
    runner.process_dataset(p2, "micromotor", source_name="feb.csv", snapshot_date="2025-02-01")

    row = next(r for r in watch.items("micromotor") if r["asin"] == WATCHED)
    assert row["observations"] == 2 and row["latest"]["price"] == pytest.approx(price0 * 0.8, abs=0.01)
    ch = {c["metric"]: c for c in row["changes"]}
    assert ch["price"]["change"] == pytest.approx(-0.2, abs=0.001)
    with b.session() as s:
        evs = s.query(b.Event).filter(b.Event.kind == "watch.change").all()
        assert len(evs) == 1 and evs[0].severity == "important" and WATCHED in evs[0].payload["asin"]
        assert "price" in evs[0].subject and evs[0].market_name == "micromotor"
    assert watch.after_processing("micromotor") == 0                        # the same snapshot is not reported twice
    assert watch.remove(item["id"]) and all(r["asin"] != WATCHED for r in watch.items("micromotor"))


def test_live_refresh_observes_watched_listings_with_the_provider(env, monkeypatch):
    from dip import watch
    from dip.acquire import base
    from dip.storage import business as b

    monkeypatch.setenv("DIP_KEEPA_API_KEY", "k")
    monkeypatch.setitem(base.config()["run"], "cache_hours", 0)             # each refresh asks again
    asin = fx.PRODUCTS[1]["asin"]
    watch.add(asin, None, label="rival")
    assert watch.due()                                                     # never checked yet

    r1 = watch.refresh(provider_kw={"transport": fx.keepa_transport(), "sleep": lambda s: None})
    assert r1["status"] == "done" and r1["listings"] == 1 and r1["events"] == 0, r1
    assert not watch.due()

    cheaper = copy.deepcopy(fx.PRODUCTS)
    cheaper[1]["csv"][1][-1] = int(cheaper[1]["csv"][1][-1] * 0.7)          # the latest new-price point, 30 % lower
    r2 = watch.refresh(provider_kw={"transport": fx.keepa_transport(cheaper), "sleep": lambda s: None})
    assert r2["listings"] == 1 and r2["events"] == 1, r2
    pts = watch.history(asin)
    assert [p["source"] for p in pts] == ["live:keepa", "live:keepa"] and pts[1]["price"] < pts[0]["price"]
    with b.session() as s:
        run = s.get(b.AcquisitionRun, r2["id"])
        assert run.kind == "watchlist" and run.providers == {"amazon_detail": "keepa"} and run.ledger["requests"] >= 1


def test_refresh_without_a_provider_says_what_to_configure(env):
    from dip import watch

    watch.add(fx.PRODUCTS[0]["asin"])
    r = watch.refresh()
    assert r["status"] == "failed" and "provider" in r["error"]


def test_watchlist_api(env):
    from fastapi.testclient import TestClient

    from dip.api.app import app

    c = TestClient(app)
    assert c.post("/api/v2/watchlist", json={"asin": "B0BADASIN!", "market": None}).status_code == 400
    it = c.post("/api/v2/watchlist", json={"asin": fx.PRODUCTS[2]["asin"], "label": "rival two"}).json()
    rows = c.get("/api/v2/watchlist").json()["items"]
    assert [r["label"] for r in rows] == ["rival two"] and rows[0]["observations"] == 0
    h = c.get(f"/api/v2/watchlist/{fx.PRODUCTS[2]['asin']}/history").json()
    assert h["points"] == [] and h["changes"] == []
    assert c.post("/api/v2/watchlist/refresh").status_code == 409            # no provider: says so
    assert c.delete(f"/api/v2/watchlist/{it['id']}").json() == {"removed": True}
    assert c.delete(f"/api/v2/watchlist/{it['id']}").status_code in (200, 404)
    assert c.get("/api/v2/watchlist").json()["items"] == []

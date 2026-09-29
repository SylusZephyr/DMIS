"""Live data acquisition (src/dip/acquire): parsers, providers against recorded shapes, the market run end to end
through the real pipeline, and the guard rails (budget, cache, raw archive, robot check, off-by-default)."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import acquire_fixtures as fx  # noqa: E402

from dip.acquire.amazon import parse  # noqa: E402


@pytest.fixture()
def dip_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setenv("DIP_EVENTS_SYNC", "1")
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_KEEPA_API_KEY", "DIP_SCRAPER_API_KEY",
                "DIP_SCRAPER_API_URL", "DIP_PAAPI_ACCESS_KEY"):
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


NO_SLEEP = {"sleep": lambda s: None}


# ---------------------------------------------------------------- text parsers
def test_parsers_read_amazon_text():
    assert parse.bought_past_month("1K+ bought in past month") == 1000
    assert parse.bought_past_month("50+ bought in past month") == 50
    assert parse.bought_past_month("In stock") is None
    assert parse.money("$1,299.99") == 1299.99 and parse.money(None) is None
    assert parse.rating("4.4 out of 5 stars") == 4.4
    assert parse.integer("2,345 ratings") == 2345
    assert parse.weight_grams("5 x 3 x 2 inches; 4.8 Ounces") == pytest.approx(136.1, abs=0.1)
    assert parse.any_date("Reviewed in the United States on March 3, 2026") == date(2026, 3, 3)


# ---------------------------------------------------------------- keepa
def test_keepa_listing_and_history_follow_the_product_object():
    from dip.acquire.amazon.keepa import Keepa, month_ends

    p = fx.PRODUCTS[1]
    x = Keepa.listing(p)
    assert x.asin == p["asin"] and x.sales == fx.LADDER[1]
    assert x.price == pytest.approx((900 + 150 * 1 + 20 * 5) / 100)       # last new-price point, cents -> $
    assert x.rating == 4.1 and x.reviews == 50 + 10 + 25
    assert x.fba_fee == pytest.approx(3.23) and x.package_weight_g == 125
    assert x.image.endswith("img1.jpg") and x.category == "Denture Care"
    unbadged = Keepa.listing(fx.PRODUCTS[3])
    assert unbadged.sales is None                                         # no badge -> no value, never 0
    ends = month_ends(3, date(2026, 9, 20))
    assert ends == [date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31)]
    hist = Keepa.history(p, ends)
    assert [h.month for h in hist] == ends and all(h.price for h in hist)
    assert hist[0].price < hist[-1].price                                 # the synthetic price rises monthly


def test_keepa_provider_batches_and_archives_raw_responses(dip_env, monkeypatch):
    from dip.acquire import Ledger
    from dip.acquire.amazon.keepa import Keepa

    monkeypatch.setenv("DIP_KEEPA_API_KEY", "secret-key")
    calls: list = []
    k = Keepa(ledger=Ledger(), transport=fx.keepa_transport(calls=calls), **NO_SLEEP)
    assert k.search("denture reline", 0)[:2] == ["B0TEST0000", "B0TEST0001"]
    got = k.details([p["asin"] for p in fx.PRODUCTS])
    assert len(got) == 36
    k.details(["B0TEST0000"])
    k.details(["B0TEST0000"])                                             # identical request -> cache
    assert k.ledger.cached == 1
    landing = list((dip_env / "platform" / "landing" / "keepa").rglob("*.meta.json"))
    assert landing, "every response is archived"
    meta = json.loads(landing[0].read_text())
    assert meta["params"]["key"] == "***", "secrets never reach the archive"


def test_budget_stops_a_provider(dip_env, monkeypatch):
    from dip.acquire import BudgetExceeded, Ledger
    from dip.acquire.amazon.keepa import Keepa

    monkeypatch.setenv("DIP_KEEPA_API_KEY", "k")
    k = Keepa(ledger=Ledger(budget_usd=0.003), transport=fx.keepa_transport(), **NO_SLEEP)   # 1 request at $0.002
    k.search("a", 0)
    with pytest.raises(BudgetExceeded):
        k.search("b", 0)


# ---------------------------------------------------------------- vendor JSON API
def test_scraper_api_normalizes_search_product_and_reviews(dip_env, monkeypatch):
    from dip.acquire.amazon.scraper_api import ScraperAPI

    monkeypatch.setenv("DIP_SCRAPER_API_KEY", "k")
    monkeypatch.setenv("DIP_SCRAPER_API_URL", "https://api.vendor.test/request")
    s = ScraperAPI(transport=fx.scraper_transport(), **NO_SLEEP)
    found = s.search_listings("denture", 1)
    assert found[0].sales == 1000 and found[0].price == 14.99 and found[1].sales is None
    d = s.details(["B0VEND0001"])[0]
    assert d.bsr == 4321 and d.category == "Denture Care" and d.package_weight_g == pytest.approx(136.1, abs=0.1)
    assert d.launch_date == date(2024, 3, 3)
    revs = s.reviews("B0VEND0001")
    assert len(revs) == 2 and revs[0].rating == 2 and revs[0].verified and revs[0].date == date(2026, 3, 3)


# ---------------------------------------------------------------- amazon.com pages
def test_html_parsers_read_search_and_product_pages():
    from dip.acquire.amazon.html import parse_product, parse_search

    rows = parse_search(fx.SEARCH_HTML)
    assert [r.asin for r in rows] == ["B0HTML0001", "B0HTML0002"]           # the empty ad slot is skipped
    assert rows[0].sales == 1000 and rows[0].price == 19.99 and rows[0].reviews == 2345 and rows[0].rating == 4.4
    assert rows[1].sales is None
    x, revs = parse_product(fx.PRODUCT_HTML)
    assert x.asin == "B0HTML0001" and x.brand == "DentaFix" and x.title == "Denture Repair Kit, Complete Reline Set"
    assert x.bsr == 1234 and x.launch_date == date(2024, 3, 3) and x.category == "Denture Care"
    assert x.image.endswith("big.jpg") and x.package_weight_g == pytest.approx(136.1, abs=0.1)
    assert [r.review_id for r in revs] == ["RHTML1", "RHTML2"]
    assert revs[0].rating == 2.0 and revs[0].title == "Cracked quickly" and revs[0].verified and revs[0].helpful == 12
    assert revs[1].verified is False


def test_robot_check_stops_instead_of_bypassing():
    from dip.acquire.amazon.html import Blocked, parse_search

    with pytest.raises(Blocked):
        parse_search(fx.CAPTCHA_HTML)


def test_direct_html_is_off_by_default(dip_env):
    from dip.acquire import provider_for, status
    from dip.acquire.amazon.html import AmazonHTML

    assert not AmazonHTML.configured()
    assert provider_for("amazon_search") is None
    st = status()
    assert st["ready"] is False and st["direct_html_enabled"] is False


def test_paapi_requests_are_sigv4_signed():
    from dip.acquire.amazon.paapi import sigv4_headers

    h = sigv4_headers("webservices.amazon.com", "us-east-1", "/paapi5/searchitems",
                      "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems", '{"Keywords":"x"}', "AKID", "SECRET",
                      now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert h["x-amz-date"] == "20260901T000000Z"
    assert h["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/20260901/us-east-1/ProductAdvertisingAPI/aws4_request")
    again = sigv4_headers("webservices.amazon.com", "us-east-1", "/paapi5/searchitems",
                          "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems", '{"Keywords":"x"}', "AKID", "SECRET",
                          now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert again["Authorization"] == h["Authorization"]                    # deterministic for the same input


# ---------------------------------------------------------------- the market run, through the real pipeline
def test_market_run_builds_dated_snapshots_and_processes_them(dip_env, monkeypatch):
    from dip.acquire import base, run_market
    from dip.acquire.run import runs
    from dip.storage import business as b

    monkeypatch.setenv("DIP_KEEPA_API_KEY", "k")
    monkeypatch.setenv("DIP_SCRAPER_API_KEY", "v")                         # review text from a data API
    monkeypatch.setenv("DIP_SCRAPER_API_URL", "https://api.vendor.test/request")
    rc = base.config()["run"]
    monkeypatch.setitem(rc, "history_months", 2)
    monkeypatch.setitem(rc, "search_pages_per_term", 3)
    monkeypatch.setitem(rc, "reviews_listings", 5)
    r = run_market("denture_live", history=True, today=date(2026, 9, 20),
                   provider_kw={"transport": fx.combined_transport(), **NO_SLEEP})
    assert r["status"] in ("done", "partial"), r
    assert r["listings"] == 36 and r["badged"] == 27                   # a quarter show no badge
    assert r["reviews"] == 10                                            # 2 reviews x the 5 best sellers
    assert r["history_months"] == 2
    dates = [p["snapshot_date"] for p in r["processed"]]
    assert dates == ["2026-07-31", "2026-08-31", "2026-09-20"]           # oldest first, live snapshot last
    snap = pd.read_csv(Path(r["snapshots"][-1]["path"]))
    assert {"id", "title", "price", "sales", "fba_fee", "package_weight"} <= set(snap.columns)
    with b.session() as s:
        ds = s.query(b.Dataset).filter(b.Dataset.market_name == "denture_live").all()
        assert sorted(d.snapshot_date for d in ds) == dates
    rec = runs("denture_live")[0]
    assert rec["providers"]["amazon_detail"] == "keepa" and rec["providers"]["amazon_reviews"] == "scraper_api"
    assert rec["ledger"]["requests"] > 0 and rec["status"] == "done", rec["result"]["notes"]
    with b.session() as s:
        summary = s.get(b.Market, "denture_live").summary
    assert summary["pain"]["reviews"] > 0                               # acquired review text reached customer pain
    from dip.acquire.run import acquired_reviews
    assert len(acquired_reviews("denture_live")) == 10


def test_run_without_any_provider_fails_clearly(dip_env):
    from dip.acquire import run_market

    r = run_market("denture_live", process=False)
    assert r["status"] == "failed" and "DIP_KEEPA_API_KEY" in r["error"]


def test_search_terms_come_from_category_config():
    from dip.acquire.run import search_terms

    assert "denture reline kit" in search_terms("denture_base")
    assert search_terms("unknown_market") == ["unknown market"]

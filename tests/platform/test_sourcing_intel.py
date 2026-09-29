"""Sourcing intelligence (src/dip/sourcing_intel): platform signing and mapping, scoring (fit, landed cost,
margin, reliability, compliance, MOQ), Pareto and best pick, supplier merge across platforms, the run record,
the RFQ and reaching out."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import sourcing_fixtures as fx  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL"):
        monkeypatch.delenv(var, raising=False)
    for k, v in fx.ENV.items():
        monkeypatch.setenv(k, v)
    from dip import settings
    settings.get_settings.cache_clear()
    yield tmp_path
    settings.get_settings.cache_clear()


HTTP = {"sleep": lambda s: None}


def concept(**kw):
    from dip.sourcing_intel.concept import from_text

    c = from_text("denture_base", "denture reline kit soft", target_price=19.99, qty=kw.get("qty", 300))
    c.fba_fee = 3.5                     # (the market's median FBA fee when the market has listings)
    c.max_fob = kw.get("max_fob", 3.0)
    return c


def test_concept_builds_english_and_chinese_queries():
    from dip.sourcing_intel.concept import from_text, zh_for

    c = from_text("denture_base", "soft denture reline kit", target_price=19.99)
    assert "denture" in c.terms_en and "reline" in c.terms_en and "kit" not in c.terms_en    # "kit" is a stop word
    assert any("重衬" in q for q in c.terms_zh), c.terms_zh                                  # glossary / category zh terms
    assert c.required_certs == ["ISO 13485", "FDA"]                                          # config compliance for the market
    assert "义齿基托" in zh_for(["denture", "base"])


def test_signatures_follow_the_platform_rules():
    import hashlib
    import hmac

    from dip.sourcing_intel.platforms import sign_1688, sign_aliexpress

    p = {"b": "2", "a": "1"}
    assert sign_1688("param2/1/ns/api/k", p, "sec") == hmac.new(b"sec", b"param2/1/ns/api/ka1b2", hashlib.sha1).hexdigest().upper()
    assert sign_aliexpress(p, "sec") == hmac.new(b"sec", b"a1b2", hashlib.sha256).hexdigest().upper()


def test_offer_mapping_tiers_currency_and_scales(env):
    from dip.sourcing_intel.platforms import to_offer

    o = to_offer("1688", fx.OFFERS_1688[0], "q")
    assert o.currency == "CNY" and o.price == 12.5 and [t.min_qty for t in o.tiers] == [2, 500]
    assert o.unit_price_at(300) == 12.5 and o.unit_price_at(600) == 10.8
    assert o.usd(10.0) == pytest.approx(1.488)                    # configured, dated CNY rate
    assert o.repurchase_rate == pytest.approx(0.38) and o.verified is True and o.certifications == ["ISO 13485", "CE"]
    a = to_offer("alibaba", fx.ALIBABA_ITEMS[0], "q")
    assert a.currency == "USD" and a.rating == pytest.approx(4.8)   # 96/100 -> 4.8 of 5


def test_scoring_explains_fit_margin_and_flags(env):
    from dip.sourcing_intel import score as sc
    from dip.sourcing_intel.platforms import to_offer

    c = concept()
    good = sc.score_offer(c, to_offer("1688", fx.OFFERS_1688[0], "q"))
    toy = sc.score_offer(c, to_offer("1688", fx.OFFERS_1688[2], "q"))
    assert good.match and not toy.match and "keychain" in " ".join(toy.flags + toy.fit_detail["negative"])
    unit = 12.5 * 0.1488
    assert good.unit_usd == pytest.approx(unit, abs=1e-3)
    assert good.margin == pytest.approx((19.99 - 19.99 * 0.15 - 3.5 - good.landed_usd) / 19.99, abs=1e-6)
    assert any("FDA" in f for f in good.flags)                     # required but not stated -> verify, never a pass
    assert good.components["compliance"] == pytest.approx(50.0)    # ISO 13485 stated, FDA not
    assert any("before duty" in f for f in good.flags)             # no duty rate configured for the market
    moq_heavy = sc.score_offer(c, to_offer("1688", fx.OFFERS_1688[1], "q"))
    assert moq_heavy.components["moq_fit"] == pytest.approx(30.0)  # first order 300 vs MOQ 1000


def test_run_searches_all_platforms_merges_suppliers_and_picks_best(env):
    from dip.sourcing_intel import run_concept
    from dip.sourcing_intel.run import offers_of, runs

    calls: list = []
    c = concept()
    c.image_urls = ["https://m.media-amazon.com/images/I/x.jpg"]
    r = run_concept(c, http_kw={"transport": fx.transport(calls), **HTTP})
    assert r["status"] == "done", r["notes"]
    assert set(r["platforms"]) >= {"1688", "alibaba", "aliexpress"} and r["platforms"]["1688"] == 4
    assert any(q.startswith("image:") for q in r["queries"]["1688"])            # image search with the Amazon photo
    assert all(any("一" <= ch <= "鿿" for ch in q) for q in r["queries"]["1688"] if not q.startswith("image:"))
    # the same factory on 1688 and Alibaba.com is one supplier
    denta = [s for s in r["shortlist"] if "denta" in s["key"]]
    assert len(denta) == 1 and denta[0]["platforms"] == ["1688", "alibaba"]
    best = r["best"]
    assert best is not None and best["match"] and best["pareto"] and best["margin"] > 0
    assert "toy" not in best["title"].lower() and "keychain" not in (best["title_en"] or "").lower()
    stored = offers_of(r["id"])
    assert len(stored) == r["offers"] and {"score", "reasons", "components", "landed_usd"} <= set(stored[0])
    assert all(isinstance(o["offer_id"], str) for o in stored)                 # ids keep their type
    assert runs("denture_base")[0]["result"]["best"]["offer_id"] == best["offer_id"]


def test_run_without_platforms_says_what_to_configure(env, monkeypatch):
    from dip.sourcing_intel import run_concept

    for k in fx.ENV:
        monkeypatch.delenv(k)
    r = run_concept(concept())
    assert r["status"] == "failed" and "credentials" in r["notes"][0]


def test_rfq_and_reach_out_register_the_supplier_and_the_inquiry(env):
    from dip.sourcing_intel import reach_out, run_concept
    from dip.storage import business as b

    r = run_concept(concept(), http_kw={"transport": fx.transport(), **HTTP}, image_search=False)
    best = r["best"]
    out = reach_out(r["id"], best["offer_id"], best["platform"], by="tester")
    assert "300 units" in out["rfq"]["en"] and "首单数量：300" in out["rfq"]["zh"] and "$3.00" in out["rfq"]["en"]
    assert "ISO 13485" in out["rfq"]["en"]
    for text in out["rfq"].values():                                           # stored offers keep nulls as nulls
        assert "nan" not in text.lower().split() and "：nan" not in text and ": nan" not in text
    again = reach_out(r["id"], best["offer_id"], best["platform"])
    assert again["supplier_id"] == out["supplier_id"]                           # no duplicate supplier
    with b.session() as s:
        n = s.query(b.SupplierInteraction).filter(b.SupplierInteraction.supplier_id == out["supplier_id"]).count()
    assert n == 2


def test_unit_economics_is_computed_and_sourced():
    from dip.sourcing_intel import economics

    e = economics.unit(20.0, 2.0, fba_fee=3.5, weight_g=200, qty=300, ad_share=0.1, duty_scenario="section301_list3",
                       launch_costs=500, units_per_month=100)
    pu = e["per_unit"]
    assert pu["referral"] == pytest.approx(3.0) and pu["freight"] == pytest.approx(0.42) and pu["duty"] == pytest.approx(0.5)
    assert pu["landed"] == pytest.approx(2.92) and pu["contribution"] == pytest.approx(20 - 3 - 3.5 - 2.92)
    assert pu["profit"] == pytest.approx(pu["contribution"] - 2.0) and e["break_even_acos"] == pytest.approx(pu["contribution"] / 20)
    assert e["first_order_cost"] == pytest.approx(876.0) and e["cash_to_launch"] == pytest.approx(1376.0)
    assert e["monthly"]["payback_months"] == pytest.approx(1376.0 / (pu["profit"] * 100), abs=0.05)
    assert e["sources"]["duty"] == "scenario section301_list3"
    # the unit cost that leaves the target margin after ads: price x (1 - 30% - 10%) - fees - freight, before 25% duty
    assert e["max_unit_cost_for_target"] == pytest.approx((20 * 0.6 - 3 - 3.5 - 0.42) / 1.25, abs=1e-3)
    with pytest.raises(ValueError):
        economics.unit(20, 2, fba_fee=3, weight_g=100, duty_scenario="nope")

"""Metrics engine v3 (Phase 5.2): invariants, planted-answer synthetic markets, unit economics,
derived-cost detection, and the API contract."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dip.metrics.engine import compute, geo_index, transform

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "engine"))

LADDER = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 2000, 3000, 4000, 5000]


def _badge(u: np.ndarray) -> np.ndarray:
    lad = np.array(LADDER, dtype=float)
    i = np.searchsorted(lad, u, side="right") - 1
    return np.where(i >= 0, lad[np.clip(i, 0, None)], np.nan)


def planted_market(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Segment OPEN: many brands, recent entrants that sell well. Segment LOCKED: one dominant brand
    holding most demand, entrants that sell little. Same price level, same ratings."""
    rng = np.random.default_rng(seed)
    rows = []
    as_of = pd.Timestamp("2026-07-01")
    for seg, n, brands, ent_boost, dom in (("OPEN", 120, 30, 0.6, False), ("LOCKED", 120, 30, -1.2, True)):
        for i in range(n):
            entrant = i < n // 3
            age = rng.integers(30, 330) if entrant else rng.integers(400, 2500)
            brand = f"{seg}-B{rng.integers(0, brands)}"
            mu = 3.6 + (ent_boost if entrant else 0.0)
            if dom and i % 6 == 0 and not entrant:
                brand, mu = "Dominant", 6.5
            units = np.exp(mu + rng.normal(0, 0.7))
            price = float(np.exp(rng.normal(np.log(30), 0.3)))
            cost = price * rng.uniform(0.2, 0.5)                           # genuine, varying costs
            rows.append({"record_id": f"{seg}{i}", "id": f"B0{seg[:3]}{i:05d}", "product_id": f"P{seg}{i}", "segment_id": seg,
                         "segment_label": seg.lower(), "brand": brand, "price": round(price, 2),
                         "rating": round(float(np.clip(rng.normal(4.3, 0.3), 1, 5)), 1),
                         "launch_date": as_of - pd.Timedelta(days=int(age)), "category": seg, "sales": units,
                         "attributes": json.dumps({"unit_cost": round(cost, 2), "fba_fee": 4.0})})
    L = pd.DataFrame(rows)
    L["sales"] = _badge(L["sales"].to_numpy())
    P = L[["product_id", "segment_id", "title" if "title" in L else "brand"]].copy()
    S = pd.DataFrame({"segment_id": ["OPEN", "LOCKED"], "segment_label": ["open", "locked"]})
    return L, P, S


@pytest.fixture(scope="module")
def planted():
    L, P, S = planted_market()
    return compute(L, P, S, as_of=pd.Timestamp("2026-07-01"))


# ---------------------------------------------------------------- units
def test_transform_and_index():
    assert transform(3.0, {"x0": 3, "x1": 6, "scale": "log10"}) is not None
    assert transform(1000, {"x0": 3, "x1": 6, "scale": "log10"}) == 0.0
    assert transform(1e6, {"x0": 3, "x1": 6, "scale": "log10"}) == 1.0
    assert transform(0.0, {"x0": 0, "x1": 0.4, "y0": 0.5, "y1": 1.0}) == 0.5          # upside-only component
    assert transform(5000, {"x0": 5000, "x1": 1000}) == 0.0 and transform(1000, {"x0": 5000, "x1": 1000}) == 1.0
    idx, cov = geo_index({"a": 1.0, "b": 1.0, "c": None}, {"a": 1, "b": 1, "c": 2})
    assert idx == 100.0 and cov == 0.5
    assert geo_index({"a": None}, {"a": 1}) == (None, 0.0)


# ---------------------------------------------------------------- invariants
def test_invariants(planted):
    s = planted.summary
    seg = planted.segments.set_index("segment_id")
    rm = s["revenue_month"]
    assert rm["floor"] <= rm["low"] <= rm["estimate"] <= rm["high"] <= rm["ceiling"]
    assert seg["revenue_est"].sum() == pytest.approx(rm["estimate"], rel=1e-6)          # segments add up to the market
    # every level of the hierarchy reconciles: listings -> products -> brands -> market
    assert planted.listings["revenue_est"].sum() == pytest.approx(rm["estimate"], rel=1e-4)       # listing values are rounded
    assert planted.products["revenue_est"].sum() == pytest.approx(rm["estimate"], rel=1e-6)
    assert planted.brands["revenue_est"].sum() == pytest.approx(rm["estimate"], rel=1e-6)
    assert (planted.products["revenue_lo"] <= planted.products["revenue_est"] + 1e-6).all()
    assert (planted.products["revenue_est"] <= planted.products["revenue_hi"] + 1e-6).all()
    assert (seg["revenue_floor"] <= seg["revenue_est"]).all()
    assert (seg["revenue_lo"] <= seg["revenue_est"]).all() and (seg["revenue_est"] <= seg["revenue_hi"]).all()
    shares = planted.metrics.query("scope == 'segment' and metric == 'revenue_share'")["value"]
    assert shares.sum() == pytest.approx(1.0, abs=1e-6)
    assert seg["hhi_est"].between(0, 10000).all()
    assert seg["opportunity_index"].between(0, 100).all()
    L = planted.listings
    assert ((L["units_est"] >= L["units_floor"] - 1e-6) & (L["units_est"] <= L["units_ceiling"] + 1e-6)).all()
    m = planted.metrics
    assert m["formula"].str.match(r"M\d+\.\d+").all()                                   # every metric cites its formula
    assert {"value", "low", "high", "n", "unit", "basis", "caveat"} <= set(m.columns)


def test_headline_is_the_observed_floor_and_validation_is_recorded(planted):
    rm = planted.summary["revenue_month"]
    assert rm["headline"] == rm["floor"] and "observed" in rm["headline_basis"]
    um = planted.summary["units_month"]                  # units lead with their observed floor too
    assert um["headline"] == um["floor"] <= um["low"] + 1e-6 <= um["estimate"] + 2e-6
    assert planted.metrics.query("metric == 'units_month_floor'")["value"].iat[0] == pytest.approx(um["floor"], abs=0.1)
    assert rm["model_validated"] is (not rm["model_issues"])


def test_any_set_of_products_gets_its_interval_from_the_same_simulation(planted):
    """Capacity used to combine product intervals as if products were independent (sqrt of summed squares), giving
    the market a second interval. interval_for returns the stored market/segment rows, and for any other set sums
    the same joint draws."""
    rm = planted.summary["revenue_month"]
    ids = planted.products["product_id"].astype(str).tolist()
    cat = planted.interval_for("category", "m", ids)
    assert cat["revenue"] == pytest.approx((rm["estimate"], rm["low"], rm["high"]), rel=1e-6)
    seg = planted.segments.set_index("segment_id")
    s_open = planted.interval_for("segment", "OPEN", [])
    assert s_open["revenue"] == pytest.approx((seg.at["OPEN", "revenue_est"], seg.at["OPEN", "revenue_lo"],
                                               seg.at["OPEN", "revenue_hi"]), rel=1e-4)
    sub = ids[:37]
    node = planted.interval_for("taxonomy", "n", sub)
    pr = planted.products.set_index("product_id").loc[sub]
    est, lo, hi = node["revenue"]
    assert est == pytest.approx(pr["revenue_est"].sum(), rel=1e-4) and lo <= est <= hi
    assert lo >= pr["revenue_lo"].max() - 1e-6                           # a set is never below its largest member's low
    all_ = planted.draws.interval(ids)["revenue"]                          # summing product draws reproduces the market
    assert all_[0] == pytest.approx(rm["estimate"], rel=1e-4) and all_[1] == pytest.approx(rm["low"], rel=0.03)
    assert planted.interval_for("taxonomy", "x", sub + ["not-a-product"]) is None     # never a partial sum


def test_sparse_badges_never_put_a_range_below_what_was_certainly_sold():
    """Implants-like market: only a handful of badged listings. The model cannot be validated, and no interval --
    market, segment or product -- may start below the sum of its own badge floors (it used to: interval inflation
    was applied around the mean and could cross the floor)."""
    L, P, S = planted_market(seed=4)
    keep = L.index[L["sales"].notna()][:3]
    L.loc[~L.index.isin(keep), "sales"] = np.nan
    out = compute(L, P, S, as_of=pd.Timestamp("2026-07-01"))
    rm = out.summary["revenue_month"]
    assert rm["floor"] > 0 and rm["floor"] <= rm["low"] + 1e-6 <= rm["estimate"] + 2e-6
    assert rm["model_validated"] is False and any("observed sales" in r for r in rm["model_issues"])
    assert rm["headline"] == rm["floor"]
    seg = out.segments
    assert (seg["revenue_lo"] >= seg["revenue_floor"] - 1e-6).all()
    Lo = out.listings
    pfloor = (Lo["units_floor"] * pd.to_numeric(Lo["price"], errors="coerce").fillna(0)).groupby(Lo["product_id"]).sum()
    pr = out.products.set_index("product_id")
    assert (pr["revenue_lo"] >= pfloor.reindex(pr.index).fillna(0) - 1e-6).all()
    assert (pr["revenue_lo"] <= pr["revenue_est"] + 1e-6).all()
    # the market still adds up exactly: anchoring rescales the excess over the floor, it does not shift totals
    assert out.products["revenue_est"].sum() == pytest.approx(rm["estimate"], rel=1e-6)


def test_revenue_awaiting_a_scope_decision_is_disclosed_not_dropped():
    """E4: listings whose sub-category scope is still 'review' stay in every total; the summary says how much of the
    observed floor and of the estimate they carry."""
    L, P, S = planted_market()
    L["scope_status"] = np.where(L["segment_id"] == "LOCKED", "review", "in")
    out = compute(L, P, S, as_of=pd.Timestamp("2026-07-01"))
    rm = out.summary["revenue_month"]
    und = rm["undecided"]
    seg = out.segments.set_index("segment_id")
    assert und["listings"] == int((L["segment_id"] == "LOCKED").sum())
    assert und["floor"] == pytest.approx(seg.at["LOCKED", "revenue_floor"], rel=1e-4)
    assert und["estimate"] == pytest.approx(seg.at["LOCKED", "revenue_est"], rel=1e-3)
    assert und["decided_floor"] == pytest.approx(seg.at["OPEN", "revenue_floor"], rel=1e-4)
    assert 0 < und["floor_share"] < 1 and rm["floor"] == pytest.approx(und["floor"] + und["decided_floor"], abs=0.02)
    assert compute(*planted_market(), as_of=pd.Timestamp("2026-07-01")).summary["revenue_month"]["undecided"] is None


def test_planted_answer(planted):
    seg = planted.segments.set_index("segment_id")
    assert seg.loc["LOCKED", "hhi_est"] > seg.loc["OPEN", "hhi_est"]
    assert seg.loc["LOCKED", "top_brand_est"] == "Dominant"
    assert seg.loc["OPEN", "entrant_success_rate"] > seg.loc["LOCKED", "entrant_success_rate"]
    assert seg.loc["OPEN", "entrant_value_expected"] > seg.loc["LOCKED", "entrant_value_expected"]
    assert seg.loc["OPEN", "opportunity_index"] > seg.loc["LOCKED", "opportunity_index"]


def test_unit_economics(planted):
    L = planted.listings
    ok = L["unit_cost"].notna()
    assert ok.all()                                                                     # genuine costs are used
    exp = (L["price"] * 0.85 - 4.0 - L["unit_cost"]).round(2)
    assert (L.loc[ok, "unit_margin"] - exp[ok]).abs().max() < 0.011
    assert planted.segments["entrant_value_kind"].eq("profit").all()


def test_price_derived_cost_is_not_used_as_cost():
    L, P, S = planted_market(seed=1)
    L["attributes"] = [json.dumps({"unit_cost": round(p * 0.55 - 4.0, 2), "fba_fee": 4.0}) for p in L["price"]]
    out = compute(L, P, S, as_of=pd.Timestamp("2026-07-01"))
    assert out.summary["unit_cost"]["derived_field"] is True
    assert out.listings["unit_cost"].isna().all() and out.listings["cost_ceiling"].notna().all()
    assert out.segments["entrant_value_kind"].eq("revenue").all()                         # falls back to revenue, stated
    assert out.segments["margin_rate_median"].isna().all()


def test_invalid_costs_are_quarantined():
    L, P, S = planted_market(seed=2)
    L.loc[:9, "attributes"] = json.dumps({"unit_cost": -1.0})
    out = compute(L, P, S, as_of=pd.Timestamp("2026-07-01"))
    assert out.listings.loc[:9, "unit_cost"].isna().all()
    assert out.listings.loc[:9, "unit_cost_issue"].str.contains("invalid").all()


# ---------------------------------------------------------------- pipeline + API
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    d = tmp_path_factory.mktemp("dip_v3")
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
    c = TestClient(app)
    r = c.post("/api/v2/datasets", files={"file": ("m.csv", synthetic_dataset(months=3).to_csv(index=False), "text/csv")},
               data={"market": "mm"})
    for _ in range(240):
        j = c.get(f"/api/v2/jobs/{r.json()['job_id']}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert j["status"] == "done", j.get("error")
    yield c
    mp.undo()
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def test_api_contract(api):
    m = api.get("/api/v2/markets/mm/metrics").json()
    assert m["summary"]["revenue_month"]["estimate"] > 0 and m["summary"]["evidence_grade"] in "ABCD"
    assert any(r["metric"] == "opportunity_index" for r in m["rows"])
    segs = api.get("/api/v2/markets/mm/segments-v3").json()
    assert segs and "opportunity_index" in segs[0] and "revenue_lo" in segs[0]
    sid = segs[0]["segment_id"]
    ex = api.get(f"/api/v2/markets/mm/segments/{sid}/explain").json()
    assert ex["metrics"] and ex["opportunity_components"]["demand"]["weight"] > 0 and ex["top_listings"]
    pid = api.get("/api/v2/markets/mm/products?limit=1").json()["items"][0]["product_id"]
    pe = api.get(f"/api/v2/products/{pid}/explain").json()
    assert pe["listings"] and "units_est" in pe["listings"][0] and pe["components"]["segment"]["weight"] == 0.7
    doc = api.get("/api/v2/methodology").json()
    assert any("M1.4" in s["ids"] or "1.4" in s["title"] for s in doc["sections"])
    assert api.get("/api/v2/markets/mm/segments/NOPE/explain").status_code == 404


# ---------------------------------------------------------------- 5.4 gap analysis
def _gap_market(seed=3, planted=True):
    rng = np.random.default_rng(seed)
    rows = []
    vocab = ["dental", "model", "teeth", "study", "standard", "adult", "jaw", "upper", "lower", "removable", "clinic", "anatomy"]
    for i in range(160):
        brushless = rng.random() < 0.2
        base = 3.3 + (1.3 if (brushless and planted) else 0.0)
        units = float(np.exp(base + rng.normal(0, 0.6)))
        title = " ".join(rng.choice(vocab, 7, replace=False)) + (" brushless" if brushless else " brushed")
        rows.append({"id": f"B0G{i:06d}", "segment_id": "S", "family_label": "Widgets", "title": title, "brand": f"Br{i % 20}",
                     "price": 30.0, "price_band": int(i % 4), "units_est": units, "specs": {}, "is_entrant": False,
                     "unit_margin": 5.0})
    return pd.DataFrame(rows), pd.DataFrame({"segment_id": ["S"], "segment_label": ["widgets"], "opportunity_index": [50.0]})


def test_planted_gap_is_recovered_and_recommended():
    from dip.metrics.gaps import analyse

    L, S = _gap_market()
    g, rec = analyse(L, S)
    row = g.set_index("feature").loc["brushless"]
    assert bool(row["is_gap"]) and row["lift_lo"] > 1.5 and row["q_value"] <= 0.1
    assert not g.set_index("feature").loc["brushed", "is_gap"]                # the complement is not a gap
    noise = g[g["feature"].isin(["dental", "model", "teeth", "study", "standard"]) & g["is_gap"]]
    assert noise.empty                                                         # FDR: shuffled vocabulary is not a gap
    assert "brushless" in rec.iloc[0]["features"] and rec.iloc[0]["expected_units_lo"] > 0


def test_no_gap_without_signal():
    from dip.metrics.gaps import analyse

    L, S = _gap_market(planted=False)
    g, rec = analyse(L, S)
    assert not g["is_gap"].any()
    assert rec.iloc[0]["features"] == [] and "no significant feature gap" in rec.iloc[0]["basis"]


def test_api_gaps_and_recommendations(api):
    assert isinstance(api.get("/api/v2/markets/mm/gaps").json(), list)
    rec = api.get("/api/v2/markets/mm/recommendations").json()
    assert isinstance(rec, list)


# ---------------------------------------------------------------- Phase 6 competitor intelligence
def test_brand_table_planted_leader(planted):
    b = planted.brands
    assert b is not None and len(b) > 10
    top = b.iloc[0]
    assert top["brand"] == "Dominant" and top["position"] == "leader" and top["p_top"] >= 0.95 and top["rank"] == 1
    assert (b["share_lo"] <= b["share_est"] + 1e-9).all() and (b["share_est"] <= b["share_hi"] + 1e-9).all()
    assert abs(b["share_est"].sum() - 1) < 1e-6
    assert (b["rank_lo"] <= b["rank_hi"]).all()
    info = planted.summary["brands"]
    assert info["hhi_lo"] <= info["hhi"] <= info["hhi_hi"] and info["effective_competitors"] > 1
    # a brand whose share interval lies entirely below 1/effective competitors is "below_par"
    below = b[b["position"] == "below_par"]
    assert (below["share_hi"] < info["equal_share_benchmark"]).all()


def test_bayes_rating_shrinks_small_brands():
    from dip.metrics.brands import bayes_rating
    r = pd.Series([4.8, 4.0, 4.0, 4.0])
    v = pd.Series([2, 1000, 1000, 1000])
    w, m, c, basis = bayes_rating(r, v)
    assert basis == "review counts" and 4.0 < m < 4.01 and c == 1000
    shrunk = (c * m + 4.8 * 2) / (c + 2)
    assert shrunk - m < 0.01                       # 2 reviews of 4.8 barely move the estimate
    _, m2, c2, basis2 = bayes_rating(r, None)
    assert c2 == 1 and basis2.startswith("one per listing") and m2 == pytest.approx(4.2)


def test_launch_momentum_detects_surge():
    from dip.metrics.brands import momentum
    as_of = pd.Timestamp("2026-07-01")
    rows = []
    for i in range(60):                            # segment A: 50 recent vs 10 previous launches -> accelerating
        days = 100 if i < 50 else 500
        rows.append({"segment_id": "A", "launch_date": as_of - pd.Timedelta(days=days)})
    for i in range(40):                            # segment B: 20 vs 20 -> no significant change
        rows.append({"segment_id": "B", "launch_date": as_of - pd.Timedelta(days=100 if i < 20 else 500)})
    out = momentum(pd.DataFrame(rows), as_of).set_index("scope_id")
    assert out.loc["A", "direction"] == "accelerating" and out.loc["A", "q_value"] < 0.01
    assert out.loc["B", "direction"] == "no_significant_change"
    assert out.loc["", "recent"] == 70 and out.loc["", "previous"] == 30


def test_snapshot_share_changes_are_significance_tested():
    from dip.metrics.brands import changes
    h = pd.DataFrame([
        {"period": "2026-01-01", "brand": "A", "share_est": 0.50, "share_lo": 0.48, "share_hi": 0.52},
        {"period": "2026-01-01", "brand": "B", "share_est": 0.10, "share_lo": 0.02, "share_hi": 0.30},
        {"period": "2026-02-01", "brand": "A", "share_est": 0.30, "share_lo": 0.28, "share_hi": 0.32},
        {"period": "2026-02-01", "brand": "B", "share_est": 0.14, "share_lo": 0.03, "share_hi": 0.35},
    ])
    out, info = changes(h, 0.95)
    out = out.set_index("brand")
    assert info["status"] == "ok" and info["from"] == "2026-01-01" and info["to"] == "2026-02-01"
    assert bool(out.loc["A", "significant"]) and not bool(out.loc["B", "significant"])   # B's move is inside the noise
    _, one = changes(h[h["period"] == "2026-01-01"], 0.95)
    assert one["status"] == "needs_two_snapshots"


def test_api_competitors_v3(api):
    c = api.get("/api/v2/markets/mm/competitors-v3").json()
    assert c["brands"] and {"share_lo", "share_hi", "p_top", "position", "signals"} <= set(c["brands"][0])
    assert isinstance(c["brands"][0]["signals"], list)
    assert c["info"]["effective_competitors"] > 0
    assert c["change_info"]["status"] in ("ok", "needs_two_snapshots")
    assert isinstance(c["cohorts"], list) and isinstance(c["momentum"], list)


# ---------------------------------------------------------------- Phase 6 launch simulator
@pytest.mark.parametrize("heavy,seed", [(False, 5), (True, 6)])
def test_launch_predictive_is_calibrated(heavy, seed):
    """Fit on an observed synthetic market, predict fresh listings from the same process: the p10-p90
    predictive interval must cover ~80% of the true units and the median must be close."""
    from dip.metrics.demand import estimate, model_card, predict_draws
    from dip.metrics.observation import observe
    from dip.metrics.synthetic import Scenario, generate

    f, _ = generate(Scenario(n=800, seed=seed, heavy_tails=heavy))
    as_of = f.attrs["as_of"]
    card = model_card(estimate(f, observe(f["sales"]), as_of), as_of)
    T, true = generate(Scenario(n=3000, seed=seed, heavy_tails=heavy))    # same seed -> same sub-category effects
    D = predict_draws(card, T, 800, 1, 0.0, as_of)
    lo, hi = np.quantile(D, 0.1, axis=0), np.quantile(D, 0.9, axis=0)
    coverage = ((true >= lo) & (true <= hi)).mean()
    assert 0.70 <= coverage <= 0.90, coverage
    assert 0.8 <= np.median(D) / np.median(true) <= 1.25
    so = __import__("dip.metrics", fromlist=["config"]).config()["sales_observation"]
    assert D.max() <= so["badge_ladder"][-1] * so["top_rung_multiplier"] + 1e-6   # physical cap respected


def test_api_launch_simulate(api):
    p = api.get("/api/v2/markets/mm/products?limit=1").json()["items"][0]
    body = {"title": p["title"], "price": 30.0, "market": "mm", "unit_cost": 6.0, "fulfilment_fee": 4.0,
            "fixed_monthly_cost": 200.0, "launch_cost": 3000.0}
    r = api.post("/api/v2/launch/simulate", json=body)
    assert r.status_code == 200, r.text
    s = r.json()
    u = s["units"]
    assert u["lo"] <= u["p10"] <= u["median"] <= u["p90"] <= u["hi"]
    e = s["economics"]
    assert e["unit_cost_basis"] == "given" and e["unit_margin"] == pytest.approx(30 * (1 - e["referral_fee"]) - 4 - 6)
    assert s["break_even_units"] == pytest.approx(200 / e["unit_margin"])
    assert s["profit"]["mean"] == pytest.approx(u["mean"] * e["unit_margin"] - 200, rel=1e-6)   # exact accounting per draw
    assert 0 <= s["profit"]["p_positive"] <= 1 and 0 <= s["p_break_even"] <= 1
    assert s["price_curve"]["points"] and s["price_curve"]["optimise"] == "profit"
    assert s["placement"]["segment_id"] and s["comparables"]
    assert "_units_draws" not in s
    # no unit cost -> no invented profit
    s2 = api.post("/api/v2/launch/simulate", json={"title": p["title"], "price": 30.0, "market": "mm"}).json()
    assert "profit" not in s2 and s2["economics"]["unit_cost"] is None and "unit cost" in s2["economics"]["unit_cost_basis"]
    assert api.post("/api/v2/launch/simulate", json={**body, "price": 0}).status_code == 400


def test_api_launch_compare_common_random_numbers(api):
    p = api.get("/api/v2/markets/mm/products?limit=1").json()["items"][0]
    base = {"title": p["title"], "market": "mm", "unit_cost": 6.0}
    c = api.post("/api/v2/launch/compare-v3", json={**base, "scenarios": [{"name": "A", "price": 30}, {"name": "A again", "price": 30},
                                                                          {"name": "B", "price": 45}]}).json()
    rows = {r["name"]: r for r in c["scenarios"]}
    assert c["compare_on"] == "profit" and abs(sum(r["p_best"] for r in c["scenarios"]) - 1) < 1e-9
    assert rows["A again"]["units"] == rows["A"]["units"]           # identical scenario, identical random numbers
    assert rows["A again"]["p_beats_first"] == 0.0
    assert api.post("/api/v2/launch/compare-v3", json={**base, "scenarios": [{"price": 30}]}).status_code == 400


# ---------------------------------------------------------------- Phase 6 shopping mode
def test_pareto_fronts_by_hand():
    from dip.metrics.shopping import pareto_fronts
    M = np.array([[3, 3], [2, 2], [1, 4], [1, 1], [2, 2]], dtype=float)   # higher is better
    front, dom = pareto_fronts(M)
    assert list(front) == [1, 2, 1, 3, 2]
    assert dom[0] == -1 and dom[2] == -1 and dom[1] == 0 and dom[3] in (0, 1, 4)


def _shop_products():
    rows = []
    for i in range(12):
        rows.append({"product_id": f"P{i}", "title": ("brushless " if i % 2 == 0 else "brushed ") + "dental micromotor handpiece",
                     "brand": f"B{i}", "price": 100 + 10 * i, "rating": 4.5, "reviews": 500, "units_est": 50.0, "units_lo": 30.0 + i,
                     "units_hi": 80.0, "market": "m"})
    rows.append({"product_id": "HYPE", "title": "brushless dental micromotor handpiece", "brand": "H", "price": 150, "rating": 5.0,
                 "reviews": 3, "units_est": 50.0, "units_lo": 35.0, "units_hi": 80.0, "market": "m"})
    rows.append({"product_id": "SOLID", "title": "brushless dental micromotor handpiece", "brand": "S", "price": 150, "rating": 4.8,
                 "reviews": 3000, "units_est": 50.0, "units_lo": 35.0, "units_hi": 80.0, "market": "m"})
    return pd.DataFrame(rows)


def test_shopping_requirements_shrinkage_and_dominance():
    from dip.metrics.shopping import recommend
    out = recommend(_shop_products(), "brushless micromotor", budget_max=200, limit=50)
    res = pd.DataFrame(out["results"]).set_index("product_id")
    assert out["need_words"] == ["brushless"]
    # typed words are requirements: every brushless product has full coverage, brushed ones none
    assert (res.loc[[p for p in res.index if "brushed" in res.at[p, "title"]], "need_coverage"] == 0).all()
    # Bayesian rating: 4.8 on 3000 reviews beats 5.0 on 3 reviews
    assert res.at["SOLID", "rating_bayes"] > res.at["HYPE", "rating_bayes"]
    # a dominated product never ranks above the product dominating it
    order = {p: i for i, p in enumerate(res.index)}
    for p, d in res["dominated_by"].dropna().items():
        assert order[d] < order[p] and res.at[d, "front"] < res.at[p, "front"]
    assert res.at["HYPE", "dominated_by"] == "SOLID"
    assert (res["price"] <= 200).all() and out["filtered_out"]["budget_max"] > 0


def test_api_shopping_v3(api):
    r = api.post("/api/v2/shopping/recommend-v3", json={"need": "micromotor", "budget_max": 400, "profile": "beginner"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["weights"]["proof"] == 0.3 and b["profile"] == "beginner"
    if b["results"]:
        x = b["results"][0]
        assert x["front"] == 1 and {"utility", "rating_bayes", "proof_units", "explain"} <= set(x)
    assert api.post("/api/v2/shopping/recommend-v3", json={"need": "x", "profile": "nope"}).status_code == 400


def test_api_galaxy_v3(api):
    g = api.get("/api/v2/markets/mm/galaxy-v3").json()
    assert g["products"] and {"revenue_est", "segment_label", "price_quartile", "gx"} <= set(g["products"][0])
    ids = {p["product_id"] for p in g["products"]}
    assert all(e["source"] in ids and e["target"] in ids and e["type"] in ("similar", "brand") for e in g["edges"])
    assert g["lod"]["shown"] == g["lod"]["total"] == len(ids) and abs(g["lod"]["revenue_share_shown"] - 1) < 1e-9
    few = api.get("/api/v2/markets/mm/galaxy-v3?limit=3").json()                    # level of detail: the largest first
    assert few["lod"]["shown"] == 3 and few["lod"]["total"] == len(ids) and 0 < few["lod"]["revenue_share_shown"] < 1
    top3 = sorted((p["revenue_est"] for p in g["products"]), reverse=True)[:3]
    assert sorted((p["revenue_est"] for p in few["products"]), reverse=True) == pytest.approx(top3)
    # brand links: every product of a multi-product brand is linked to that brand's largest product
    by_brand = {}
    for p in g["products"]:
        if p["brand"]:
            by_brand.setdefault(p["brand"], []).append(p)
    for brand, ps in by_brand.items():
        if len(ps) > 1:
            hub = max(ps, key=lambda p: p["revenue_est"] or 0)["product_id"]
            linked = {e["target"] for e in g["edges"] if e["type"] == "brand" and e["source"] == hub}
            assert linked == {p["product_id"] for p in ps} - {hub}
    q = [p["price_quartile"] for p in g["products"] if p["price_quartile"] is not None]
    assert set(q) <= {1, 2, 3, 4}


# ---------------------------------------------------------------- Phase 6 knowledge graph ontology
def test_graph_edges_carry_evidence_and_paths_explain(api):
    g = api.get("/api/v2/graph/explore?node=category:mm&depth=3&limit=3000").json()
    assert g["edges"]
    for e in g["edges"]:
        pr = e["props"]
        assert pr.get("evidence") and pr.get("basis") in ("fact", "estimate", "test", "similarity"), e
        if pr.get("strength") is not None:
            assert 0 <= pr["strength"] <= 1
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"Category", "ProductFamily", "Segment"} <= kinds
    # gap edges exist only for significant gaps
    for e in g["edges"]:
        if e["rel"] == "HAS_GAP":
            assert e["props"]["q_value"] <= 0.10 and e["props"]["low"] > 1
    prod = next(n for n in g["nodes"] if n["kind"] == "Product")
    p = api.get(f"/api/v2/graph/path?source=category:mm&target={prod['id']}").json()
    assert p["nodes"][0]["id"] == "category:mm" and p["nodes"][-1]["id"] == prod["id"]
    assert len(p["edges"]) == len(p["nodes"]) - 1 and all(e["props"].get("evidence") for e in p["edges"])
    w = api.get(f"/api/v2/graph/why?source={p['nodes'][0]['id']}&target={p['nodes'][1]['id']}").json()
    assert w["edges"] and w["edges"][0]["props"]["evidence"]
    assert api.get("/api/v2/graph/path?source=category:mm&target=nope").json()["nodes"] == []


def test_graph_store_props_cannot_shadow_structure(tmp_path):
    from dip.storage.graph import EmbeddedGraphStore
    g = EmbeddedGraphStore(str(tmp_path / "g.duckdb"))
    g.replace_market("m", [{"id": "a", "kind": "Feature", "label": "A", "props": {"kind": "term", "label": "x"}},
                           {"id": "b", "kind": "Segment", "label": "B", "props": {}}],
                     [{"source": "b", "target": "a", "rel": "HAS_GAP", "props": {"rel": "oops", "strength": 0.9}}])
    nb = g.neighbourhood("a", 1)
    a = next(n for n in nb["nodes"] if n["id"] == "a")
    assert a["kind"] == "Feature" and a["label"] == "A" and a["props"]["prop_kind"] == "term"
    assert nb["edges"][0]["rel"] == "HAS_GAP"
    assert [n["id"] for n in g.find_path("b", "a")["nodes"]] == ["b", "a"]


# ---------------------------------------------------------------- Phase 7 analyst v3
def test_number_guard():
    from dip.intelligence.analyst_v3 import Fact, unsupported_numbers
    facts = [Fact("revenue $114.1k/month (95% interval $100.3k–$131.7k); share 12.6%", "", "/x", [114100, 100300, 131700, 0.126])]
    ok = "The market earns about $114.1k a month [F1], somewhere between $100.3k and $131.7k with 95% confidence; share 12.6%."
    assert unsupported_numbers(ok, facts) == []
    assert unsupported_numbers("Revenue is 114,100 dollars, or 0.126 share [F1].", facts) == []
    assert unsupported_numbers("Revenue is $250k and growing 40% a year [F1].", facts) == [250000.0, 0.4]


class _Resp:
    def __init__(self, text):
        self.status_code = 200
        self._t = text
        self.text = text

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._t}]}}]}


def test_api_analyst_v3_offline_grounded_and_rejected(api, monkeypatch):
    r = api.post("/api/v2/analyst/ask-v3", json={"question": "How big is the market?", "lang": "zh"}).json()
    assert r["intent"] == "size" and r["mode"] == "computed" and r["facts"] and r["facts"][0]["source"].startswith("/api/v2/")
    assert "估算月销售额" in r["answer"]
    # without a key: AI unavailable, computed answer stands
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = api.post("/api/v2/analyst/ask-v3", json={"question": "How big is the market?", "use_ai": True}).json()
    assert r["ai_status"] == "unavailable" and r["answer"]
    # with a key: a faithful reply is accepted, a fabricated number is rejected; both traced
    import httpx
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    fact = r["facts"][0]["text"]
    # the size fact leads with the observed floor, as the market page does; the model's estimate follows, flagged
    summary = api.get("/api/v2/markets").json()[0]
    assert fact.index("observed revenue of at least") < fact.index("modelled estimate")
    if summary.get("model_validated") is False:
        assert "not validated" in fact
    est = fact.split("observed revenue of at least ")[1].split("/month")[0]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(f"The market is worth about {est} per month [F1]."))
    good = api.post("/api/v2/analyst/ask-v3", json={"question": "How big is the market?", "use_ai": True}).json()
    assert good["mode"] == "ai" and est in good["ai_answer"]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp("The market is worth $987.6M per month and grows 73% [F1]."))
    bad = api.post("/api/v2/analyst/ask-v3", json={"question": "How big is the market?", "use_ai": True}).json()
    assert bad["ai_status"] == "rejected" and "ai_answer" not in bad and bad["answer"]
    traces = api.get("/api/v2/ai/traces").json()
    v3 = [t for t in traces if t["purpose"] == "analyst.v3"]
    assert {"ok", "rejected"} <= {t["status"] for t in v3}
    assert api.post("/api/v2/analyst/ask-v3", json={"question": "x", "lang": "fr"}).status_code == 400


def test_analyst_caveat_when_the_demand_model_is_not_validated(api, monkeypatch):
    """Answers built on model estimates close with a caveat naming the markets whose model failed validation;
    none when it passed. The size answer carries the flag in its own fact instead."""
    from dip.intelligence import analyst_v3 as a

    monkeypatch.setattr(a, "_summary", lambda m: {"revenue_month": {"model_validated": m == "ok_mkt"}})
    cav = a.model_caveat(["ok_mkt", "bad_mkt"])
    assert len(cav) == 1 and "bad_mkt" in cav[0].en and "ok_mkt" not in cav[0].en and "bad_mkt" in cav[0].zh
    assert a.model_caveat(["ok_mkt"]) == []
    monkeypatch.undo()
    markets = api.get("/api/v2/markets").json()
    r = api.post("/api/v2/analyst/ask-v3", json={"question": "Who leads this market?"}).json()
    failed = [m["name"] for m in markets if m.get("model_validated") is False and m["name"] in r["scope"]["markets"]]
    has = any(f["text"].startswith("Caveat:") for f in r["facts"])
    assert has == bool(failed and r["facts"][:-1])
    size = api.post("/api/v2/analyst/ask-v3", json={"question": "How big is the market?"}).json()
    assert not any(f["text"].startswith("Caveat:") for f in size["facts"])


@pytest.mark.parametrize("q,intent", [
    ("How big is the market?", "size"), ("市场有多大？", "size"), ("What should we sell?", "recommend"), ("我们应该卖什么？", "recommend"),
    ("Who leads this market?", "competitors"), ("谁在领导这个市场？", "competitors"), ("Are launches speeding up?", "growth"),
    ("上新在加速吗？", "growth"), ("What happens if we launch it at $20?", "launch"), ("如果以 $20 上市会怎样？", "launch"),
    ("Why is the score 55?", "why"), ("为什么是这个分数？", "why"), ("How hard is it to enter?", "entry"), ("进入难度如何？", "entry"),
    ("What do customers complain about?", "pain"), ("客户投诉什么？", "pain"), ("Which product types sell most?", "types"),
    ("哪些产品类型卖得最好？", "types"), ("Who are the suppliers?", "suppliers"), ("有哪些供应商？", "suppliers"),
    ("Can I trust these numbers?", "quality"), ("这些数据可信吗？", "quality"),
])
def test_analyst_intents_bilingual(q, intent):
    from dip.intelligence.analyst_v3 import intent_of
    assert intent_of(q) == intent


def test_analyst_answers_from_the_knowledge_tables(api):
    """Complaints, product types, suppliers and number checks are answered from their own tables, with sources,
    and every followup the analyst suggests routes back to an intent it can answer."""
    from dip.intelligence.analyst_v3 import FOLLOWUPS, FOLLOWUPS_ZH, intent_of

    pain = api.post("/api/v2/analyst/ask-v3", json={"question": "What do customers complain about in mm?"}).json()
    assert pain["intent"] == "pain" and pain["facts"]
    assert all(f["source"] == "/api/v2/markets/mm/pain" for f in pain["facts"])
    types = api.post("/api/v2/analyst/ask-v3", json={"question": "Which product types sell in mm?"}).json()
    assert types["intent"] == "types" and "/api/v2/markets/mm/taxonomy" in types["sources"]
    tax = api.get("/api/v2/markets/mm/taxonomy").json()
    assert f"split into {len(tax['nodes'])} types" in types["facts"][0]["text"]
    sup = api.post("/api/v2/analyst/ask-v3", json={"question": "Who are the suppliers for mm?"}).json()
    assert sup["intent"] == "suppliers" and sup["facts"] and sup["sources"] == ["/api/v2/sourcing/ranking"]
    q = api.post("/api/v2/analyst/ask-v3", json={"question": "Can I trust the numbers for mm?", "lang": "zh"}).json()
    integ = api.get("/api/v2/markets/mm/integrity").json()
    c = integ["counts"]
    assert q["intent"] == "quality" and f"{c.get('pass', 0)} 项通过" in q["facts"][0]["text"]
    assert len(q["facts"]) == 1 + sum(ch["status"] in ("fail", "warn") for ch in integ["checks"])
    for fu in [x for v in FOLLOWUPS.values() for x in v] + [x for v in FOLLOWUPS_ZH.values() for x in v]:
        assert intent_of(fu) != "overview", fu


# ---------------------------------------------------------------- Phase 8 significant changes only
def test_significance_layer():
    from dip import significance as sig
    rev = lambda e, lo, hi: {"estimate": e, "low": lo, "high": hi}  # noqa: E731
    # tight intervals, revenue doubles -> significant and important
    ch = sig.apply([], {"revenue": rev(100, 90, 110)}, {"revenue": rev(200, 180, 220)})
    ev = ch[0]
    assert ev["kind"] == "market.size_change" and ev["payload"]["significance"]["significant"] and ev["severity"] == "important"
    # same move inside wide intervals -> kept as info, not an alert
    ch = sig.apply([], {"revenue": rev(100, 20, 500)}, {"revenue": rev(130, 30, 600)})
    assert ch[0]["severity"] == "info" and not ch[0]["payload"]["significance"]["significant"]
    # a rule-based share event is demoted when the share intervals overlap
    b = pd.DataFrame({"brand": ["A"], "share_est": [0.20], "share_lo": [0.05], "share_hi": [0.45]})
    a = pd.DataFrame({"brand": ["A"], "share_est": [0.26], "share_lo": [0.07], "share_hi": [0.50]})
    ev = {"kind": "competitor.share_change", "subject": "A", "severity": "notice", "payload": {}}
    out = sig.apply([ev], {"brands": b}, {"brands": a})
    assert out[0]["severity"] == "info" and out[0]["payload"]["significance"]["p_value"] > 0.05
    # segment demand shifts are reported only when significant (and >= 20%)
    sb = pd.DataFrame({"segment_label": ["s1", "s2"], "revenue_est": [100, 100], "revenue_lo": [95, 50], "revenue_hi": [105, 200]})
    sa = pd.DataFrame({"segment_label": ["s1", "s2"], "revenue_est": [150, 150], "revenue_lo": [140, 70], "revenue_hi": [160, 300]})
    out = sig.apply([], {"segments": sb}, {"segments": sa})
    assert [e["payload"]["segment_label"] for e in out] == ["s1"]
    assert sig.log_test(100, 0, 110, 120, 100, 130) is None          # a zero bound cannot be tested
    exact = sig.log_test(100, 100, 100, 300, 300, 300)                 # exact sales: the change is certain
    assert exact["p_value"] == 0.0 and exact["exact"]
    assert sig.log_test(100, 100, 100, 100, 100, 100)["p_value"] == 1.0


# ---------------------------------------------------------------- Phase 8 workflow on v3 with validated inputs
def test_api_project_prediction_is_v3_and_inputs_validated(api):
    p = api.get("/api/v2/markets/mm/products?limit=1").json()["items"][0]
    r = api.post("/api/v2/projects", json={"title": p["title"], "market": "mm", "idea": {"price": 30, "unit_cost": 6}})
    assert r.status_code == 200, r.text
    pred = r.json()["prediction"]
    assert pred["model"].startswith("metrics-v3") and pred["attractiveness_basis"].startswith("opportunity index")
    lo, mid, hi = pred["units_p10_p50_p90"]
    assert lo <= mid <= hi and pred["economics"]["unit_cost_basis"] == "given"
    # no unit cost -> no profit invented
    r = api.post("/api/v2/projects", json={"title": p["title"], "market": "mm", "idea": {"price": 30}}).json()
    assert r["prediction"]["profit"] is None and r["prediction"]["economics"]["unit_cost"] is None
    bad = [({"title": "x", "market": "mm"}, "title"), ({"title": "Good title", "market": "nope"}, "unknown market"),
           ({"title": "Good title", "idea": {"price": -3}}, "price"), ({"title": "Good title", "idea": {"price": "abc"}}, "number"),
           ({"title": "Good title", "idea": {"price": 10, "unit_cost": -1}}, "unit_cost")]
    for body, msg in bad:
        resp = api.post("/api/v2/projects", json=body)
        assert resp.status_code == 409 and msg in resp.json()["detail"], (body, resp.text)
    w = api.post("/api/v2/projects", json={"title": "Loss maker", "market": "mm", "idea": {"price": 10, "unit_cost": 12}}).json()
    assert w["idea"]["warnings"] and "loses money" in w["idea"]["warnings"][0]
    pid = w["id"]
    assert api.post(f"/api/v2/projects/{pid}/track", json={"listing_ids": ["not an asin!"]}).status_code == 409
    assert api.post(f"/api/v2/projects/{pid}/track", json={"listing_ids": []}).status_code == 409


def test_badge_actual_is_an_interval(api, monkeypatch):
    from dip import projects
    with __import__("dip.storage.business", fromlist=["session"]).session() as s:
        m = s.get(__import__("dip.storage.business", fromlist=["Market"]).Market, "mm")
        kind = (((m.summary or {}).get("metrics_v3") or {}).get("sales_observation") or {}).get("kind")
    lo, hi = projects._actual_interval("mm", 200.0)
    if kind == "badge":
        assert (lo, hi) == (200.0, 300.0)
    else:
        assert lo == hi == 200.0
    assert projects._actual_interval("mm", None) == (None, None)


# ---------------------------------------------------------------- Phase 8 accuracy dashboard
def test_api_accuracy_dashboard(api, monkeypatch):
    from dip.pilot import accuracy as A
    d = api.get("/api/v2/accuracy").json()
    assert d["markets"] and d["markets"][0]["market"] == "mm" and "auc" in d["markets"][0]
    assert "launch_calibration" in d and "targets" in d
    out = A.run_synthetic(2)                                      # small synchronous run
    assert out["markets"] == 2 and 0 <= out["summary"]["market_coverage_95"] <= 1
    d = api.get("/api/v2/accuracy").json()
    checks = {c["metric"]: c for c in d["synthetic"]["checks"]}
    assert {"median_abs_error", "market_coverage_95"} <= set(checks) and checks["median_abs_error"]["status"] in ("pass", "watch", "fail")
    assert d["synthetic"]["summary"]["median_abs_error"] < 0.15    # the production model is accurate on known truth
    r = api.post("/api/v2/accuracy/synthetic?markets=2").json()
    assert r["started"] in (True, False)


# ---------------------------------------------------------------- growth and forecast (random-effects trend)
def _series(n, growth, sd_meas, tau, rng, start="2025-01-01"):
    periods = pd.date_range(start, periods=n, freq="MS")
    t = np.arange(n, dtype=float)
    true = np.log(10_000) + np.log1p(growth) * t + rng.normal(0, tau, n)
    obs = true + rng.normal(0, sd_meas, n)
    z = 1.959964
    return pd.DataFrame({"period": periods.astype(str), "est": np.exp(obs), "lo": np.exp(obs - z * sd_meas),
                         "hi": np.exp(obs + z * sd_meas)}), true


def test_forecast_growth_interval_coverage():
    from dip.metrics import forecast as F
    rng = np.random.default_rng(7)
    hits_g, hits_f, trials = 0, 0, 300
    for _ in range(trials):
        h, _ = _series(8, 0.05, 0.10, 0.05, rng)
        f = F.fit(h)
        assert f["status"] == "ok" and f["growth_lo"] <= f["growth_per_month"] <= f["growth_hi"]
        hits_g += f["growth_lo"] <= 0.05 <= f["growth_hi"]
        # the true level 3 months after the last snapshot (same process, fresh period noise)
        true_next = np.exp(np.log(10_000) + np.log1p(0.05) * (7 + 3) + rng.normal(0, 0.05))
        fc = next(x for x in f["forecast"] if x["months_ahead"] == 3)
        hits_f += fc["low"] <= true_next <= fc["high"]
    # nominal 95%; the months-from-days axis and moment estimate of tau allow a small tolerance
    assert 0.90 <= hits_g / trials <= 0.995, hits_g / trials
    assert 0.90 <= hits_f / trials <= 0.995, hits_f / trials


def test_forecast_direction_and_edge_cases():
    from dip.metrics import forecast as F
    rng = np.random.default_rng(1)
    grow, _ = _series(10, 0.15, 0.03, 0.0, rng)
    assert F.fit(grow)["direction"] == "growing"
    fall, _ = _series(10, -0.15, 0.03, 0.0, rng)
    assert F.fit(fall)["direction"] == "declining"
    flat, _ = _series(4, 0.0, 0.5, 0.0, rng)
    assert F.fit(flat)["direction"] == "no_significant_trend"
    few = F.fit(grow.head(2))
    assert few == {"status": "needs_snapshots", "periods": 2, "min_periods": 3}
    # duplicates of a period count once; non-positive estimates are not usable
    dup = pd.concat([grow.head(2), grow.head(2)], ignore_index=True)
    assert F.fit(dup)["periods"] == 2
    bad = grow.head(3).copy()
    bad.loc[0, "lo"] = 0
    assert F.fit(bad)["status"] == "needs_snapshots"
    # forecasts are ordered in time and their intervals widen with the horizon
    fc = F.fit(grow)["forecast"]
    widths = [np.log(x["high"] / x["low"]) for x in fc]
    assert [x["months_ahead"] for x in fc] == [3, 6, 12] and widths == sorted(widths)
    # market + segments (segments matched by label, the latest id reported)
    rows = []
    for i, r in enumerate(grow.head(5).to_dict("records")):
        rows.append({**r, "scope": "market", "scope_id": "", "label": ""})
        rows.append({**r, "scope": "segment", "scope_id": f"seg{i}", "label": "Large"})
    mf = F.market_forecast(pd.DataFrame(rows))
    assert mf["market"]["status"] == "ok" and mf["segments"][0]["label"] == "Large" and mf["segments"][0]["segment_id"] == "seg4"
    assert F.market_forecast(pd.DataFrame())["market"]["status"] == "needs_snapshots"


def test_api_forecast_v3(api):
    from dip.storage import lake
    d = api.get("/api/v2/markets/mm/forecast-v3").json()
    assert d["market"] == {"status": "needs_snapshots", "periods": 1, "min_periods": 3}   # one upload so far
    hist = lake.read_curated("revenue_history", "mm")
    assert set(hist["scope"]) == {"market", "segment"} and (hist["lo"] <= hist["est"]).all() and (hist["est"] <= hist["hi"]).all()
    # two earlier snapshots (as if uploaded before): the endpoint now fits the trend
    p0 = pd.Timestamp(hist["period"].iat[0])
    extra = [hist.assign(period=str((p0 - pd.DateOffset(months=k)).date()), est=hist["est"] / (1.1 ** k),
                         lo=hist["lo"] / (1.1 ** k), hi=hist["hi"] / (1.1 ** k)) for k in (1, 2)]
    lake.write_curated("revenue_history", "mm", pd.concat([*extra, hist], ignore_index=True).drop(columns=["market"], errors="ignore"))
    try:
        d = api.get("/api/v2/markets/mm/forecast-v3").json()
        assert d["market"]["status"] == "ok" and d["market"]["periods"] == 3
        assert abs(d["market"]["growth_per_month"] - 0.1) < 0.01 and len(d["market"]["fitted"]) == 3
        assert d["segments"] and all(s["periods"] == 3 for s in d["segments"])
    finally:
        lake.write_curated("revenue_history", "mm", hist.drop(columns=["market"], errors="ignore"))
    assert api.get("/api/v2/markets/nope/forecast-v3").status_code == 404


def test_forecast_flags_extrapolation_beyond_history():
    from dip.metrics import forecast as F
    h, _ = _series(5, 0.05, 0.05, 0.0, np.random.default_rng(2))       # 4 months of history
    f = F.fit(h)
    assert 3.9 < f["span_months"] < 4.1
    assert [x["beyond_span"] for x in f["forecast"]] == [False, True, True]


# ---------------------------------------------------------------- segment detail: price bands, brands, momentum
def test_price_bands_bootstrap():
    from dip.metrics.bands import price_bands
    rng = np.random.default_rng(0)
    n = 300
    band = rng.integers(0, 3, n)
    price = 10 + 10 * band + rng.uniform(0, 5, n)
    units = rng.lognormal(np.log(np.array([50, 100, 20]))[band], 0.5)
    L = pd.DataFrame({"price_band": band, "price": price, "units_est": units, "revenue_est": units * price})
    out = price_bands(L)
    assert [b["band"] for b in out] == [1, 2, 3] and all(b["bands"] == 3 for b in out)
    assert sum(b["listings"] for b in out) == n and abs(sum(b["revenue_share"] for b in out) - 1) < 1e-9
    for b in out:
        assert b["units_lo"] <= b["units_mean"] <= b["units_hi"] and b["price_min"] <= b["price_median"] <= b["price_max"]
        assert b["revenue_share_lo"] <= b["revenue_share"] <= b["revenue_share_hi"]
    assert out[1]["units_lo"] > out[0]["units_hi"] > out[2]["units_hi"]      # the planted order is resolved
    assert price_bands(L) == out                                              # seeded: reproducible
    assert price_bands(pd.DataFrame()) == [] and price_bands(L.drop(columns=["price_band"])) == []


def test_api_segment_explain_detail(api):
    sid = api.get("/api/v2/markets/mm/segments-v3").json()[0]["segment_id"]
    ex = api.get(f"/api/v2/markets/mm/segments/{sid}/explain").json()
    assert ex["price_bands"] and all(b["units_lo"] <= b["units_mean"] <= b["units_hi"] for b in ex["price_bands"])
    assert ex["brands"] and all(b["segment_id"] == sid and b["share_lo"] <= b["share_est"] <= b["share_hi"] for b in ex["brands"])
    shares = [b["share_est"] for b in ex["brands"]]
    assert shares == sorted(shares, reverse=True) and sum(shares) <= 1 + 1e-9
    assert ex["momentum"] is None or ex["momentum"]["scope_id"] == sid


def test_product_position_percentiles():
    from dip.metrics.bands import position
    peers = pd.DataFrame({"price": [10, 20, 30, 40], "units_est": [5, 5, 5, 5], "revenue_est": [50, 100, 150, 200],
                          "rating": [4.0, None, 4.5, 3.0]})
    pos = position({"price": 30, "units_est": 5, "revenue_est": 150, "rating": 4.5, "reviews": None}, peers)
    m = pos["metrics"]
    assert m["price"]["percentile"] == 0.625 and m["price"]["median"] == 25      # 2 below, 1 tied of 4
    assert m["units_est"]["percentile"] == 0.5                                   # all tied: the middle
    assert m["rating"]["n"] == 3 and m["rating"]["percentile"] == 5 / 6
    assert "reviews" not in m and pos["peers"] == 4 and pos["revenue_share"] == 0.3


def test_api_product_position(api):
    pid = api.get("/api/v2/markets/mm/products?limit=1").json()["items"][0]["product_id"]
    ex = api.get(f"/api/v2/products/{pid}/explain").json()
    pos = ex["position"]
    assert pos["peers"] >= 1 and 0 <= pos["metrics"]["price"]["percentile"] <= 1 and pos["points"]
    assert len(pos["points"]) <= pos["peers"] and 0 < pos["revenue_share"] <= 1


# ---------------------------------------------------------------- opportunity board
def test_concept_price():
    from dip.metrics.board import concept_price
    assert concept_price([10, 40], []) == 20.0                              # geometric middle
    assert concept_price(None, [{"price": 5}, {"price": 7}, {"price": 30}]) == 7.0
    assert concept_price("[10, 40]", None) == 20.0 and concept_price(None, None) is None


def test_api_opportunity_board(api):
    d = api.get("/api/v2/opportunities-v3").json()
    items = d["items"]
    assert d["markets"] >= 1 and items
    from dip.metrics.board import board_key          # explainable score first, the older index breaks ties
    assert [board_key(r) for r in items] == sorted(board_key(r) for r in items)
    first_unscored = next((i for i, r in enumerate(items) if not (r.get("engine") or {}).get("score")), len(items))
    assert all((r.get("engine") or {}).get("score") is None for r in items[first_unscored:])
    recs = api.get("/api/v2/markets/mm/recommendations").json()
    recs_by = {r["segment_id"]: r for r in recs}
    for r in items:
        assert (r["concept_source"] == "gaps") == (r["segment_id"] in recs_by)
    for r in items:
        if r["price_range"]:
            assert r["price_range"][0] <= r["price"] <= r["price_range"][1]
        L = r.get("launch")
        if L and "units" in L:
            assert L["units"]["p10"] <= L["units"]["median"] <= L["units"]["p90"] and 0 <= L["revenue"]["p_target"] <= 1
        if r.get("leader"):
            assert r["leader"]["share_lo"] <= r["leader"]["share"] <= r["leader"]["share_hi"]
        assert r["growth"].get("status") == "needs_snapshots" or r["growth"]["lo"] <= r["growth"]["per_month"] <= r["growth"]["hi"]
    # the simulation behind a row is the launch simulator's own answer for that concept
    r = next(x for x in items if x.get("launch", {}).get("units"))
    # (at the rating the board assumed: the segment's rating bar, reported with its basis)
    assert r["launch"]["rating_basis"].startswith("segment rating bar") or r["launch"]["assumed_rating"] is None
    sim = api.post("/api/v2/launch/simulate", json={"title": " ".join([r["segment_label"], *r["features"]]), "price": r["price"],
                                                    "market": r["market"], "segment_id": r["segment_id"],
                                                    "rating": r["launch"]["assumed_rating"]}).json()
    assert sim["units"]["median"] == r["launch"]["units"]["median"]
    assert not any(x["code"] == "below_rating_bar" for x in r["launch"]["risks"])      # a competitive entrant meets the bar
    assert api.get("/api/v2/opportunities-v3?market=mm").json()["items"]
    assert api.get("/api/v2/opportunities-v3?market=nope").status_code == 404


def test_board_rows_are_warmed_by_processing_and_survive_a_restart(api, monkeypatch):
    from dip.metrics import board as bd
    from dip.storage import business as b

    with b.session() as s:
        m = s.get(b.Market, "mm")
        job = s.get(b.Job, m.run_id)
        version = bd.version_of(m)
    assert any(x["name"] == "board" and x["status"] == "done" for x in job.stages)       # processing computed them
    f = bd._cache_file("mm")
    assert f.exists() and json.loads(f.read_text())["market"] == "mm"
    expected = api.get("/api/v2/opportunities-v3?market=mm").json()["items"]
    bd._CACHE.clear()                                                               # a restarted process
    monkeypatch.setattr(bd, "market_rows", lambda *a, **k: pytest.fail("recomputed although stored rows are current"))
    assert api.get("/api/v2/opportunities-v3?market=mm").json()["items"] == expected
    # a new version (re-processing) or a changed setting is not served from the stored rows
    calls = []
    monkeypatch.setattr(bd, "market_rows", lambda *a, **k: calls.append(1) or [])
    assert bd.board([("mm", None, version + "x")]) == [] and calls == [1]
    bd._CACHE.clear()
    monkeypatch.setattr(bd, "_stamp", lambda v: "changed-settings")
    assert bd.board([("mm", None, version)]) == [] and calls == [1, 1]
    bd._CACHE.clear()


def test_analyst_growth_uses_forecast(api):
    from dip.storage import lake
    a = api.post("/api/v2/analyst/ask-v3", json={"question": "Is the mm market growing?", "lang": "en"}).json()
    f = [x for x in a["facts"] if x["source"].endswith("/forecast-v3")]
    assert a["intent"] == "growth" and f and "at least 3" in f[0]["text"]          # one snapshot: no trend claimed
    hist = lake.read_curated("revenue_history", "mm")
    p0 = pd.Timestamp(hist["period"].iat[0])
    extra = [hist.assign(period=str((p0 - pd.DateOffset(months=k)).date()), est=hist["est"] / (1.2 ** k),
                         lo=hist["lo"] / (1.2 ** k), hi=hist["hi"] / (1.2 ** k)) for k in (1, 2, 3)]
    lake.write_curated("revenue_history", "mm", pd.concat([*extra, hist], ignore_index=True).drop(columns=["market"], errors="ignore"))
    try:
        for lang in ("en", "zh"):
            a = api.post("/api/v2/analyst/ask-v3", json={"question": "Is the mm market growing?", "lang": lang}).json()
            f = [x for x in a["facts"] if x["source"].endswith("/forecast-v3")]
            g = float(re.search(r"([+-]\d+\.\d)%", f[0]["text"]).group(1))
            assert len(f) >= 4 and 19 < g < 21                                   # 20% per calendar month, months = days / 30.44
    finally:
        lake.write_curated("revenue_history", "mm", hist.drop(columns=["market"], errors="ignore"))


# ---------------------------------------------------------------- opportunity index: missing components are neutral
def test_geo_index_missing_components_neutral():
    idx, cov = geo_index({"a": 1.0, "b": None}, {"a": 1, "b": 1}, missing=0.5)
    assert idx == pytest.approx(100 * np.sqrt(0.5), abs=0.1) and cov == 0.5          # coverage still reported
    assert geo_index({"a": None, "b": None}, {"a": 1, "b": 1}, missing=0.5) == (None, 0.0)   # nothing measured: no index
    full, _ = geo_index({"a": 0.5, "b": 0.5}, {"a": 1, "b": 1})
    part, _ = geo_index({"a": 0.5, "b": None}, {"a": 1, "b": 1}, missing=0.5)
    assert full == part                                                                # comparable across coverage


def test_opportunity_index_comparable_across_coverage():
    """A segment without unit costs is not ranked on fewer components: margin counts as neutral 0.5."""
    from dip.metrics import config
    L, P, S = planted_market()
    L = L.drop(columns=["attributes"])                                                 # no unit costs -> no margin
    out = compute(L, P, S, as_of=pd.Timestamp("2026-07-01"))
    seg = out.segments.set_index("segment_id")
    op = config()["opportunity"]
    w = {k: v["weight"] for k, v in op["components"].items()}
    for sid, r in seg.iterrows():
        assert r["opp_margin"] is None or pd.isna(r["opp_margin"])
        parts = {k: (None if pd.isna(r[f"opp_{k}"]) else float(r[f"opp_{k}"])) for k in w}
        idx, cov = geo_index(parts, w, missing=op["missing_component_score"])
        assert r["opportunity_index"] == pytest.approx(idx, abs=0.2) and r["opportunity_coverage"] == pytest.approx(cov)
        assert r["opportunity_coverage"] < 1
    row = out.metrics.query("scope == 'segment' and metric == 'opp_margin'").iloc[0]
    assert "neutral" in row["caveat"]
    assert seg.loc["OPEN", "opportunity_index"] > seg.loc["LOCKED", "opportunity_index"]


# ---------------------------------------------------------------- launch simulator: assumed rating
def test_launch_rating_assumption_and_rating_bar_risk(api):
    from dip.metrics import launch as launch_mod
    from dip.storage import lake
    data = launch_mod.load("mm")
    S = data["segments"].dropna(subset=["rating_bar"])
    sid = S["segment_id"].iat[0]
    bar = float(S["rating_bar"].iat[0])
    title = str(S["segment_label"].iat[0])
    price = float(pd.to_numeric(data["listings"].loc[data["listings"]["segment_id"] == sid, "price"]).median())
    med = launch_mod.simulate({"title": title, "price": price}, data, "mm", segment_id=sid)
    a = med["assumptions"]
    assert a["rating_assumed"] and a["rating_basis"].startswith("segment median")
    r = [x for x in med["risks"] if x["code"] == "below_rating_bar"]
    assert all(x["severity"] == "low" and x["assumed"] for x in r)                      # an assumption is not a finding
    at_bar = launch_mod.simulate({"title": title, "price": price}, data, "mm", segment_id=sid, assume_rating="rating_bar")
    assert at_bar["assumptions"]["rating"] == bar and at_bar["assumptions"]["rating_basis"].startswith("segment rating bar")
    assert not any(x["code"] == "below_rating_bar" for x in at_bar["risks"])
    given = launch_mod.simulate({"title": title, "price": price, "rating": bar - 0.5}, data, "mm", segment_id=sid,
                                assume_rating="rating_bar")
    assert given["assumptions"]["rating"] == bar - 0.5 and not given["assumptions"]["rating_assumed"]   # given rating kept
    g = [x for x in given["risks"] if x["code"] == "below_rating_bar"]
    assert g and g[0]["severity"] == "medium" and not g[0]["assumed"]
    assert lake.has_curated("listings", "mm")


# ---------------------------------------------------------------- stable segment ids across snapshots
def _disc(ids, segs, labels=None):
    from dmie.engine.discovery import DiscoveryResult
    frame = pd.DataFrame({"id": ids, "segment_id": segs, "segment_label": labels or segs})
    S = frame.drop_duplicates("segment_id")[["segment_id", "segment_label"]].reset_index(drop=True)
    return DiscoveryResult(frame, S, pd.DataFrame(), {})


def test_stable_segment_ids():
    from dip.pipeline.clustering.taxonomy import stabilize
    prev = pd.DataFrame({"id": [f"a{i}" for i in range(10)] + [f"b{i}" for i in range(6)] + ["z0", "z1"],
                         "segment_id": ["C1-S0"] * 10 + ["C1-S1"] * 6 + ["C1-S2"] * 2})
    # rediscovered with swapped numbering; one new listing joins; the small segment C1-S2 dissolved;
    # a brand-new cluster appears and happens to be numbered C1-S2
    ids = [f"a{i}" for i in range(10)] + ["a_new"] + [f"b{i}" for i in range(6)] + ["n0", "n1", "n2"]
    segs = ["C1-S1"] * 11 + ["C1-S0"] * 6 + ["C1-S2"] * 3
    d, st = stabilize(_disc(ids, segs), prev)
    got = d.frame.set_index("id")["segment_id"]
    assert (got[[f"a{i}" for i in range(10)] + ["a_new"]] == "C1-S0").all()           # inherited by majority overlap
    assert (got[[f"b{i}" for i in range(6)]] == "C1-S1").all()
    assert got["n0"] not in {"C1-S0", "C1-S1", "C1-S2"}                                # never reuses an old id
    assert set(d.segments["segment_id"]) == set(got)
    assert st["segments_carried"] == 2 and st["ids_changed"] == 3
    # a split: the larger part keeps the id, the smaller gets a fresh one
    d2, _ = stabilize(_disc([f"a{i}" for i in range(10)], ["C1-S0"] * 7 + ["C1-S5"] * 3), prev)
    g2 = d2.frame.set_index("id")["segment_id"]
    assert (g2[[f"a{i}" for i in range(7)]] == "C1-S0").all() and g2["a9"] not in {"C1-S0", "C1-S1", "C1-S2"}
    # ids never move to another sub-category; no previous run -> unchanged
    d3, _ = stabilize(_disc([f"a{i}" for i in range(10)], ["C9-S0"] * 10), prev)
    assert (d3.frame["segment_id"] == "C9-S0").all()
    same, st0 = stabilize(_disc(ids, segs), None)
    assert st0["status"] == "no previous run" and list(same.frame["segment_id"]) == segs


def test_forecast_matches_segments_by_stable_id_with_label_fallback():
    from dip.metrics import forecast as F
    rows = []
    for k, p in enumerate(pd.date_range("2025-01-01", periods=5, freq="MS").astype(str)):
        e = 10000 * 1.1 ** k
        rows.append({"period": p, "scope": "market", "scope_id": "", "label": "", "est": e, "lo": e * 0.9, "hi": e * 1.1})
        # stable id, label drifting every snapshot (title keywords changed)
        rows.append({"period": p, "scope": "segment", "scope_id": "C1-S0", "label": f"Models · v{k}", "est": e, "lo": e * 0.9, "hi": e * 1.1})
        # history written before stable ids: a new id each snapshot, same label
        rows.append({"period": p, "scope": "segment", "scope_id": f"old{k}", "label": "Kits", "est": e / 2, "lo": e * 0.45, "hi": e * 0.55})
    mf = F.market_forecast(pd.DataFrame(rows))
    by = {s["segment_id"]: s for s in mf["segments"]}
    assert set(by) == {"C1-S0", "old4"}
    assert by["C1-S0"]["periods"] == 5 and by["C1-S0"]["label"] == "Models · v4"           # label drift does not break history
    assert by["old4"]["periods"] == 5 and abs(by["C1-S0"]["growth_per_month"] - 0.1) < 0.01


# ---------------------------------------------------------------- accuracy: scenarios and worst case
def test_run_synthetic_reports_scenarios_and_worst_case(api):
    from dip.pilot import accuracy as A
    out = A.run_synthetic(1, ["specified", "dead_listings"])
    s = out["summary"]
    assert set(s["by_scenario"]) == {"specified", "dead_listings"} and out["scenarios"] == ["specified", "dead_listings"]
    w = s["worst_case"]
    assert w["median_abs_error"] == max(v["median_abs_error"] for v in s["by_scenario"].values())
    assert w["market_coverage_95"] == min(v["market_coverage_95"] for v in s["by_scenario"].values())
    assert w["median_abs_error_scenario"] in s["by_scenario"]
    assert {r["scenario"] for r in out["rows"]} == {"specified", "dead_listings"}
    checks = {c["metric"]: c for c in A.checks(s, A.config()["accuracy"])}
    assert checks["median_abs_error"]["value"] == w["median_abs_error"] and checks["median_abs_error"]["basis"] == "worst scenario"
    assert A.checks({"median_abs_error": 0.01, "market_coverage_95": 1, "listing_coverage_95": 1, "top_category_correct": 1},
                    A.config()["accuracy"])[0]["basis"] == "pooled"                     # runs stored before scenarios

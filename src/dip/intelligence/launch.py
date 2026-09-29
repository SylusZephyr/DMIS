"""Market simulation: "our company wants to launch this product".

Given a product idea (title / specs / target price, optional unit cost) the
evaluator places it in a market and segment by similarity to real products,
then scores:

* **Market fit** (0-100 each, weights in config/platform/launch.yaml):
  demand, growth (trend), competition, price fit, customer pain, supplier
  availability, differentiation -- each with the number behind it. A component
  with no evidence is left out and listed as unavailable.
* **Risks**: brand dominance, low differentiation, price pressure, thin margin,
  declining demand, missing supplier evidence, low data coverage.
* **Attractiveness** = weighted fit minus risk penalties; verdict.
* **Positioning** from the target price's percentile in the segment.
* **Strategy**: recommendations derived from the evidence (under-served price
  tier, customer complaints to fix, bundling pattern among top sellers,
  competitor weaknesses, supplier shortlist).
* **Economics**: unit margin from the target price, fees and a unit cost (given,
  or the comparables' median cost field when the source has one) and the
  existing Monte-Carlo P&L simulation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from dip.settings import PROJECT_ROOT
from dip.storage import business as b
from dip.storage import lake

CONFIG = PROJECT_ROOT / "config" / "platform" / "launch.yaml"


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))


@dataclass
class LaunchIdea:
    title: str
    price: float
    specs: str = ""
    unit_cost: float | None = None
    market: str | None = None
    segment_id: str | None = None
    marketplace_fee: float | None = None
    fulfilment_fee: float | None = None
    fixed_monthly_cost: float = 0.0


def _num(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def choose_market(text: str) -> tuple[str | None, list[dict]]:
    """Market whose products are most similar to the idea (vector search across all markets)."""
    from dip.storage.vectors import get_vector_store

    try:
        hits = get_vector_store().similar_to_text(text, limit=25)
    except Exception:
        hits = []
    votes: dict[str, float] = {}
    for h in hits:
        if h.get("market"):
            votes[h["market"]] = votes.get(h["market"], 0.0) + max(h["score"], 0)
    if not votes:
        with b.session() as s:
            m = s.query(b.Market).first()
            return (m.name if m else None), []
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    return ranked[0][0], [{"market": k, "similarity_votes": round(v, 3)} for k, v in ranked[:5]]


def _similar(products: pd.DataFrame, text: str) -> np.ndarray:
    docs = (products["title"].fillna("") + " " + products["brand"].fillna("")).tolist()
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
    M = vec.fit_transform(docs + [text])
    return cosine_similarity(M[-1], M[:-1]).ravel()


def _attr_median(listings: pd.DataFrame, fields: list[str]) -> tuple[float | None, str | None, int]:
    if listings.empty or "attributes" not in listings:
        return None, None, 0
    vals, used = [], None
    for a in listings["attributes"]:
        try:
            d = json.loads(a) if isinstance(a, str) else (a or {})
        except ValueError:
            continue
        for f in fields:
            v = _num(d.get(f)) if isinstance(d, dict) else None
            if v is not None and v > 0:
                vals.append(v)
                used = used or f
                break
    return (float(np.median(vals)), used, len(vals)) if vals else (None, None, 0)


def evaluate(idea: LaunchIdea) -> dict:
    cfg = config()
    text = f"{idea.title} {idea.specs}".strip()
    alternatives: list[dict] = []
    market = idea.market
    if not market:
        market, alternatives = choose_market(text)
    if not market:
        return {"status": "no_markets", "note": "process at least one market first"}
    products = lake.read_curated("products", market)
    segments = lake.read_curated("segments", market)
    if products.empty or segments.empty:
        return {"status": "no_data", "market": market}
    sims = _similar(products, text)
    products = products.assign(similarity=sims)
    top = products.sort_values("similarity", ascending=False).head(cfg["comparables"])
    if idea.segment_id and idea.segment_id in set(segments["segment_id"]):
        seg_id = idea.segment_id
    else:
        w = top.groupby("segment_id")["similarity"].sum().sort_values(ascending=False)
        seg_id = w.index[0]
    seg = segments.set_index("segment_id").loc[seg_id]
    sp = products[products["segment_id"] == seg_id]
    trends_df = lake.read_curated("trends", market)
    trend = trends_df.set_index("scope").loc[seg_id].to_dict() if len(trends_df) and seg_id in set(trends_df["scope"]) else {}
    comp = lake.read_curated("competitors", market)
    comps_in_seg = sp.assign(brand=sp["brand"].fillna("")).groupby("brand")["monthly_revenue"].sum(min_count=1)

    fit, unavailable = {}, []
    # demand: segment revenue percentile among segments
    rev = segments["monthly_revenue"]
    if pd.notna(seg.get("monthly_revenue")) and rev.notna().sum() >= 2:
        pct = float((rev.dropna() <= seg["monthly_revenue"]).mean())
        fit["demand"] = {"score": round(pct * 100), "evidence": f"segment earns ${seg['monthly_revenue']:,.0f}/month — "
                                                                  f"larger than {pct:.0%} of segments"}
    else:
        unavailable.append("demand (no observed sales in this segment)")
    # growth: trend direction
    if trend.get("direction") is not None and not pd.isna(trend.get("direction")):
        fit["growth"] = {"score": round((float(trend["direction"]) + 1) * 50),
                         "evidence": f"trend {trend['trend']} (confidence {trend.get('confidence', 0):.0f}%)"}
    else:
        unavailable.append("growth (no trend evidence)")
    # competition: concentration
    t3 = _num(seg.get("top3_brand_share"))
    t1 = _num(seg.get("top_brand_share"))
    if t3 is not None:
        fit["competition"] = {"score": round((1 - 0.6 * t3 - 0.4 * (t1 or 0)) * 100),
                              "evidence": f"top brand {seg.get('top_brand')} holds {t1 or 0:.0%}, top 3 hold {t3:.0%} "
                                          f"({seg.get('concentration')})"}
    # price fit: target price vs segment distribution and the tier's revenue per product
    prices = sp["price"].dropna()
    positioning, price_pct = None, None
    if len(prices) >= 3:
        price_pct = float((prices <= idea.price).mean())
        positioning = next(lbl for bound, lbl in cfg["positioning"] if price_pct < bound)
        tier = "low" if idea.price <= prices.quantile(1 / 3) else "middle" if idea.price <= prices.quantile(2 / 3) else "premium"
        tp, tr = _num(seg.get(f"{tier}_products")), _num(seg.get(f"{tier}_revenue"))
        allp, allr = _num(seg.get("products")), _num(seg.get("monthly_revenue"))
        if tp and tr is not None and allp and allr:
            ratio = (tr / allr) / (tp / allp)
            score = float(np.clip(50 + 35 * np.log2(max(ratio, 1e-3)), 0, 100))
            fit["price_fit"] = {"score": round(score), "evidence": f"{tier} tier: {tr / allr:.0%} of revenue from {tp / allp:.0%} of products"}
        else:
            outside = price_pct < 0.05 or price_pct > 0.95
            fit["price_fit"] = {"score": 40 if outside else 60,
                                "evidence": f"target at the {price_pct:.0%} price percentile of the segment (tier revenue unknown)"}
    else:
        unavailable.append("price fit (fewer than 3 priced products in the segment)")
    # customer pain
    pain = lake.read_curated("pain", market, where="scope = ?", params=[f"segment:{seg_id}"])
    complaints = []
    if len(pain) and pain.iloc[0]["status"] == "ok":
        payload = json.loads(pain.iloc[0]["payload"])
        complaints = payload.get("complaints", [])
        share = sum(c["share_of_reviews"] for c in complaints[:3])
        fit["customer_pain"] = {"score": round(min(share, 1) * 100),
                                "evidence": "; ".join(f"{c['aspect']} {c['share_of_reviews']:.0%}" for c in complaints[:3]) or "no complaints"}
    else:
        unavailable.append("customer pain (no review text for this segment)")
    # suppliers
    matches = lake.read_curated("supplier_matches", market, where="segment_id = ?", params=[seg_id], order="match_score DESC")
    shortlist = []
    if len(matches):
        with b.session() as s:
            for _, m in matches.head(5).iterrows():
                sup = s.get(b.Supplier, m["supplier_id"])
                if sup is not None:
                    shortlist.append({"supplier_id": sup.id, "name": sup.name, "country": sup.country, "oem": sup.oem,
                                      "match_score": round(float(m["match_score"]), 3), "supplier_score": sup.score})
        best = float(matches["match_score"].max())
        fit["supplier_availability"] = {"score": round(min(best / 0.5, 1) * 100),
                                        "evidence": f"{int((matches['match_score'] >= 0.15).sum())} matching suppliers, best match {best:.2f}"}
    else:
        unavailable.append("supplier availability (no supplier list imported)")
    # differentiation
    closest = top.iloc[0]
    max_sim = float(closest["similarity"])
    fit["differentiation"] = {"score": round((1 - max_sim) * 100),
                              "evidence": f"closest existing product: {str(closest['title'])[:80]} (similarity {max_sim:.2f})"}

    wts = cfg["fit_weights"]
    used = {k: v for k, v in fit.items() if k in wts}
    raw = sum(wts[k] * v["score"] for k, v in used.items()) / sum(wts[k] for k in used)

    # economics
    listings = lake.read_curated("listings", market, columns=["id", "product_id", "attributes"],
                                 where=f"product_id IN ({','.join('?' * len(top))})", params=top["product_id"].tolist())
    ecfg = cfg["economics"]
    unit_cost, cost_basis = idea.unit_cost, "given"
    if unit_cost is None:
        unit_cost, field, n = _attr_median(listings, ecfg["unit_cost_fields"])
        cost_basis = f"median '{field}' of {n} comparable listings" if unit_cost is not None else None
    fulfil, fbasis = idea.fulfilment_fee, "given"
    if fulfil is None:
        fulfil, field, n = _attr_median(listings, ecfg["fulfilment_fee_fields"])
        fbasis = f"median '{field}' of {n} comparable listings" if fulfil is not None else "not in source (0 assumed)"
        fulfil = fulfil or 0.0
    fee = idea.marketplace_fee if idea.marketplace_fee is not None else ecfg["marketplace_fee"]
    margin = None if unit_cost is None else idea.price * (1 - fee) - fulfil - unit_cost
    economics = {"price": idea.price, "marketplace_fee": fee, "fulfilment_fee": round(fulfil, 2), "fulfilment_basis": fbasis,
                 "unit_cost": None if unit_cost is None else round(unit_cost, 2), "unit_cost_basis": cost_basis,
                 "unit_margin": None if margin is None else round(margin, 2),
                 "margin_rate": None if margin is None else round(margin / idea.price, 3)}
    sim_out = None
    if unit_cost is not None:
        from dmie.engine.simulation import SimulationInput, simulate

        sim_out = simulate(products, SimulationInput(idea.title, idea.price, unit_cost + fulfil, idea.specs, seg_id, fee,
                                                     idea.fixed_monthly_cost)).to_dict()

    # risks
    rc = cfg["risks"]
    risks = []
    if t1 is not None and t1 >= rc["dominance_medium"]:
        risks.append({"risk": "Existing dominance", "severity": "high" if t1 >= rc["dominance_high"] else "medium",
                      "evidence": f"{seg.get('top_brand')} holds {t1:.0%} of the segment"})
    if max_sim >= rc["differentiation_medium"]:
        risks.append({"risk": "Low differentiation", "severity": "high" if max_sim >= rc["differentiation_high"] else "medium",
                      "evidence": f"{max_sim:.0%} similar to '{str(closest['title'])[:60]}' ({closest.get('brand')})"})
    if trend.get("price_pressure"):
        risks.append({"risk": "Price pressure", "severity": "medium", "evidence": "segment prices are falling across snapshots"})
    if price_pct is not None and price_pct <= 0.1:
        risks.append({"risk": "Price pressure", "severity": "medium",
                      "evidence": f"target price is cheaper than {1 - price_pct:.0%} of the segment's products — competing on price"})
    if economics["margin_rate"] is not None and economics["margin_rate"] < rc["thin_margin"]:
        risks.append({"risk": "Thin margin", "severity": "high" if economics["margin_rate"] <= 0 else "medium",
                      "evidence": f"unit margin ${margin:,.2f} ({economics['margin_rate']:.0%} of price)"})
    if trend.get("trend") == "Declining":
        risks.append({"risk": "Declining demand", "severity": "high", "evidence": "; ".join(json.loads(trend.get("evidence") or "[]"))})
    if not len(matches):
        risks.append({"risk": "No manufacturer evidence", "severity": "medium", "evidence": "no supplier list matched this segment"})
    cov = _num(seg.get("coverage"))
    if cov is not None and cov < rc["low_coverage"]:
        risks.append({"risk": "Thin evidence", "severity": "medium", "evidence": f"evidence coverage {cov:.0%} of the opportunity inputs"})
    pen = cfg["risk_penalty"]
    attractiveness = float(np.clip(raw - sum(pen.get(r["severity"], 0) for r in risks), 0, 100))
    order = {"high": 0, "medium": 1}
    risks.sort(key=lambda r: order.get(r["severity"], 2))

    # strategy
    strat = []
    tiers = [(t, _num(seg.get(f"{t}_products")), _num(seg.get(f"{t}_revenue"))) for t in ("low", "middle", "premium")]
    allp, allr = _num(seg.get("products")), _num(seg.get("monthly_revenue"))
    if allp and allr:
        best_tier = max(((t, (r / allr) / (p / allp)) for t, p, r in tiers if p and r is not None), key=lambda x: x[1], default=None)
        if best_tier and best_tier[1] > 1.2:
            strat.append(f"Position in the {best_tier[0]} tier: it earns {best_tier[1]:.1f}x its share of products in revenue")
    for c in complaints[:2]:
        if c.get("opportunity"):
            strat.append(f"{c['opportunity']} — '{c['aspect']}' appears in {c['share_of_reviews']:.0%} of reviews")
    terms = cfg["bundle_terms"]
    if len(sp) >= 6 and sp["monthly_sales"].notna().sum() >= 4:
        ranked = sp.sort_values("monthly_sales", ascending=False, na_position="last")
        k = max(3, len(ranked) // 4)
        has = ranked["title"].fillna("").str.lower().str.contains("|".join(rf"\b{t}\b" for t in terms))
        top_rate, rest_rate = float(has.iloc[:k].mean()), float(has.iloc[k:].mean())
        if top_rate > 0 and top_rate >= cfg["bundle_lift"] * max(rest_rate, 0.05):
            strat.append(f"Bundle / kit: {top_rate:.0%} of the top sellers are sold as kits or sets vs {rest_rate:.0%} of the rest")
    if len(comp):
        leader = comp.set_index("brand").reindex([seg.get("top_brand")]).dropna(how="all")
        if len(leader):
            opp = json.loads(leader.iloc[0]["opportunities"] or "[]")
            if opp:
                strat.append(f"Against {seg.get('top_brand')}: {opp[0].lower()}")
    if any(r["risk"] == "Low differentiation" for r in risks):
        dom = seg.get("dominant_specs") or ""
        strat.append("Differentiate the specification" + (f" — most products already share {dom}" if dom else ""))
    if shortlist:
        strat.append("Source from " + ", ".join(f"{s['name']} ({s['country'] or 'n/a'})" for s in shortlist[:3]))
    elif not len(matches):
        strat.append("Import a supplier list to find manufacturers for this segment")
    if trend.get("trend") in ("Growing", "Emerging"):
        strat.append(f"Timing: segment is {trend['trend'].lower()} — enter early")

    verdicts = cfg["verdicts"]
    verdict = "Attractive" if attractiveness >= verdicts["attractive"] else "Moderate" if attractiveness >= verdicts["moderate"] else "Unattractive"
    return {
        "status": "ok", "idea": idea.__dict__, "market": market, "market_alternatives": alternatives,
        "segment": {"segment_id": seg_id, "label": seg.get("segment_label"), "family": seg.get("family_label"),
                    "monthly_revenue": _num(seg.get("monthly_revenue")), "products": _num(seg.get("products")),
                    "price_median": _num(seg.get("price_median")), "top_brand": seg.get("top_brand"),
                    "trend": trend.get("trend"), "opportunity_score": _num(seg.get("opportunity_score")),
                    "confidence": _num(seg.get("confidence_score"))},
        "market_attractiveness": round(attractiveness), "fit_score": round(raw), "verdict": verdict,
        "expected_positioning": positioning, "price_percentile": None if price_pct is None else round(price_pct, 3),
        "main_risk": risks[0] if risks else None, "risks": risks, "fit": fit, "unavailable": unavailable,
        "recommended_strategy": strat, "economics": economics, "simulation": sim_out, "suppliers": shortlist,
        "comparables": top[["product_id", "title", "brand", "price", "monthly_sales", "monthly_revenue", "similarity"]]
        .round(3).to_dict("records"),
        "competitors": [{"brand": k, "monthly_revenue": v} for k, v in comps_in_seg.sort_values(ascending=False).head(5).items()],
    }

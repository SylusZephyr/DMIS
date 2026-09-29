"""Opportunity engine (spec 35-36, 71-74, 155-156): explainable, evidence-limited opportunity scores.

For every segment and taxonomy node:

    opportunity = sum(w_i * s_i) / sum(w_i)  over the dimensions that HAVE evidence

    demand, offline strength, growth, customer pain, competition gap, pricing/margin, supplier
    availability, entry ease, data confidence -- each 0-100 from a stated metric and scale

A dimension without evidence is left out (never a neutral placeholder) and ``evidence_coverage`` says how
much of the total weight was actually measured. Scopes below ``min_coverage`` or ``min_products`` are
``insufficient_evidence`` and get no rank. Each scope also gets:

* an evidence matrix (dimension -> High / Medium / Low / Unknown, value, basis)  (spec 34)
* pattern detections A-E (spec 71), only when every dimension a pattern needs was measured
* risks (spec 156): regulatory, hazmat, concentration, low demand, low margin, low data confidence, offline
  demand unobserved, few suppliers
* numbered reasons built from the measured facts, and the confidence (spec 155)
* lifecycle status: detected / insufficient_evidence, or the stage of a linked project (spec 74)
"""

from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd

from dip.knowledge import config, offline

DIMENSIONS = ("demand", "offline_strength", "growth", "customer_pain", "competition_gap", "pricing_margin",
              "supplier_availability", "entry_ease", "data_confidence")


def _cfg() -> dict:
    return config()["opportunity"]


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def scale(dim: str, value) -> float | None:
    v = _f(value)
    if v is None:
        return None
    sc = _cfg()["scales"][dim]
    if sc.get("log10"):
        if v <= 0:
            return 0.0
        v = math.log10(v)
    x0, x1 = float(sc["x0"]), float(sc["x1"])
    return round(float(np.clip((v - x0) / (x1 - x0), 0, 1) * 100), 1)


def level(score: float | None) -> str:
    if score is None:
        return "Unknown"
    for lo, name in _cfg()["levels"]:
        if score >= lo:
            return name
    return "Low"


def node_inputs(P: pd.DataFrame, L: pd.DataFrame) -> dict:
    """Entry success and quality gap for a taxonomy node, from its own listings (point estimates)."""
    c = _cfg()
    out: dict = {}
    u = pd.to_numeric(L.get("units_est"), errors="coerce") if "units_est" in L else pd.Series(dtype=float)
    if "is_entrant" in L and u.notna().sum() >= 3:
        ent = L["is_entrant"].fillna(False).astype(bool)
        if ent.sum() >= int(c["node_min_entrants"]):
            out["entrant_success_rate"] = float((u[ent] >= u.median()).mean())
            out["entrant_basis"] = f"{int(ent.sum())} listings launched in the entry window vs the node's median units"
    r = pd.to_numeric(L.get("rating"), errors="coerce") if "rating" in L else pd.Series(dtype=float)
    ok = r.notna() & u.notna()
    if ok.sum() >= 3:
        out["quality_gap_share"] = float(np.average((r[ok] < float(c["quality_threshold"])).to_numpy(), weights=u[ok].to_numpy() + 1e-9))
    return out


def _regulatory(apps: set[str]) -> list[str]:
    return sorted(apps & set(_cfg()["regulated_applications"]))


def _hazmat(titles: list[str]) -> list[str]:
    """Dangerous-goods terms as whole words, not negated ("alcohol-free" is not a dangerous good)."""
    t = " ".join(x.lower() for x in titles if isinstance(x, str))
    neg = "|".join(re.escape(n) for n in _cfg().get("hazmat_negations", []))
    out = set()
    for h in _cfg()["hazmat_terms"]:
        for m in re.finditer(r"(?<![a-z])" + re.escape(h) + r"(?![a-z])", t):
            if not (neg and re.match(neg, t[m.end():])):
                out.add(h)
                break
    return sorted(out)


def score_scope(inp: dict) -> dict:
    """``inp``: products, revenue_est, revenue_lo, revenue_hi, growth_12m, quality_gap_share, hhi, margin_rate,
    supplier_matches (None when there is no supplier data at all), entrant_success_rate, data_confidence,
    offline_evidence_score, price_median, top3_share, brands, applications (set), titles (list), bases (dict)."""
    c = _cfg()
    w = c["weights"]
    metric_of = {d: c["scales"][d]["metric"] for d in DIMENSIONS}
    dims = {d: scale(d, inp.get(metric_of[d])) for d in DIMENSIONS}
    rp = c.get("customer_pain_reviews")
    if rp and _f(inp.get(rp["metric"])) is not None:
        # review text outranks the rating proxy (spec 27-30)
        v = _f(inp.get(rp["metric"]))
        dims["customer_pain"] = round(float(np.clip((v - rp["x0"]) / (rp["x1"] - rp["x0"]), 0, 1) * 100), 1)
        metric_of["customer_pain"] = rp["metric"]
    fb = c.get("pricing_margin_fallback")
    if dims["pricing_margin"] is None and fb and _f(inp.get(fb["metric"])) is not None:
        # no genuine unit cost: the share of price left after referral, FBA and freight stands in (spec 72)
        v = _f(inp.get(fb["metric"]))
        dims["pricing_margin"] = round(float(np.clip((v - fb["x0"]) / (fb["x1"] - fb["x0"]), 0, 1) * 100), 1)
        metric_of["pricing_margin"] = fb["metric"]
    present = {d: s for d, s in dims.items() if s is not None}
    wsum = sum(w.values())
    coverage = round(sum(w[d] for d in present) / wsum, 3)
    score = round(sum(w[d] * s for d, s in present.items()) / sum(w[d] for d in present), 1) if present else None
    enough = coverage >= float(c["min_coverage"]) and (inp.get("products") or 0) >= int(c["min_products"])
    bases = inp.get("bases") or {}
    matrix = {d: {"status": level(dims[d]), "score": dims[d], "metric": metric_of[d], "value": _f(inp.get(metric_of[d])),
                  "basis": bases.get(d) or ("no evidence -- excluded from the score" if dims[d] is None else None)} for d in DIMENSIONS}
    lvl = {d: matrix[d]["status"] for d in DIMENSIONS}
    patterns = []
    if lvl["demand"] == "High" and lvl["competition_gap"] == "High":
        patterns.append({"code": "A", "name": "high demand, low competition"})
    if lvl["demand"] == "High" and lvl["customer_pain"] == "High":
        patterns.append({"code": "B", "name": "high demand, frequent dissatisfaction"})
    if lvl["offline_strength"] == "High" and lvl["competition_gap"] in ("High", "Medium"):
        patterns.append({"code": "C", "name": "strong offline activity, limited online competition"})
    if lvl["demand"] == "High" and lvl["competition_gap"] == "Low":
        patterns.append({"code": "D", "name": "strong but concentrated market: differentiation gap"})
    if inp.get("price_premium") and lvl["demand"] == "High" and inp.get("low_differentiation"):
        patterns.append({"code": "E", "name": "high price, strong demand, little feature differentiation"})
    rt = c["risk_thresholds"]
    risks = []
    reg = _regulatory(set(inp.get("applications") or []))
    if reg:
        risks.append({"code": "regulatory", "severity": "high", "detail": "FDA-regulated device classes likely ("
                      + ", ".join(reg) + "); verify product code, class and 510(k) needs per product"})
    hz = _hazmat(inp.get("titles") or [])
    if hz:
        risks.append({"code": "hazmat", "severity": "high", "detail": "dangerous-goods terms in titles: " + ", ".join(hz)})
    dc = _f(inp.get("data_confidence"))
    if dc is not None and dc < rt["low_data_confidence"]:
        risks.append({"code": "low_data_confidence", "severity": "medium", "detail": f"data confidence {dc:.0f}/100"})
    hhi = _f(inp.get("hhi"))
    if hhi is not None and hhi >= rt["high_concentration_hhi"]:
        risks.append({"code": "high_concentration", "severity": "medium", "detail": f"HHI {hhi:,.0f}"})
    rev = _f(inp.get("revenue_est"))
    if rev is not None and rev < rt["low_demand_revenue"]:
        risks.append({"code": "low_demand", "severity": "medium", "detail": f"modeled revenue ${rev:,.0f}/month"})
    mg = _f(inp.get("margin_rate"))
    if mg is not None and mg < rt["low_margin"]:
        risks.append({"code": "low_margin", "severity": "high", "detail": f"median margin {mg:.0%}"})
    fh = _f(inp.get("fee_headroom"))
    if fh is not None and fh < rt.get("thin_fee_headroom", 0):
        risks.append({"code": "thin_fee_headroom", "severity": "high",
                      "detail": f"fees and freight take {1 - fh:.0%} of the price"})
    if dims["pricing_margin"] is None:
        risks.append({"code": "margin_unknown", "severity": "medium", "detail": "no genuine unit cost in the data: margin not measured"})
    if dims["offline_strength"] is None:
        risks.append({"code": "offline_unobserved", "severity": "low", "detail": "offline demand not observed (no offline evidence loaded)"})
    sm = inp.get("supplier_matches")
    if sm is not None and sm <= 1:
        risks.append({"code": "few_suppliers", "severity": "medium", "detail": f"{int(sm)} matching supplier(s) on file"})
    reasons = []
    if rev is not None:
        lo, hi = _f(inp.get("revenue_lo")), _f(inp.get("revenue_hi"))
        reasons.append(f"Modeled demand ${rev:,.0f}/month" + (f" (95% ${lo:,.0f}–${hi:,.0f})" if lo is not None and hi is not None else ""))
    reasons.append(f"{int(inp.get('products') or 0)} canonical products" + (f" from {int(inp['brands'])} brands" if inp.get("brands") else ""))
    if inp.get("top3_share") is not None:
        reasons.append(f"Top-3 brands hold {inp['top3_share']:.0%} of modeled revenue" + (f" (HHI {hhi:,.0f})" if hhi is not None else ""))
    if _f(inp.get("price_median")) is not None:
        reasons.append(f"Median price ${inp['price_median']:,.0f}")
    q = _f(inp.get("quality_gap_share"))
    if inp.get("offline_basis"):
        reasons.append(f"Offline evidence score {inp['offline_evidence_score']:.0f}/100 ({inp['offline_basis']})")
    if inp.get("top_complaints"):
        reasons.append(f"{inp['review_count']} reviews; top complaints: "
                       + ", ".join(f"{x['aspect']} ({x['share_of_reviews']:.0%})" for x in inp["top_complaints"]))
    elif q is not None:
        reasons.append(f"{q:.0%} of demand goes to listings rated below {c['quality_threshold']} (rating-based proxy)")
    e = _f(inp.get("entrant_success_rate"))
    if e is not None:
        reasons.append(f"{e:.0%} of recent entrants reached the median" + (f" ({bases['entry_ease']})" if bases.get("entry_ease") else ""))
    g = _f(inp.get("growth_12m"))
    if g is not None:
        reasons.append(f"Expected growth {g:+.0%} over 12 months")
    if fh is not None:
        fob = _f(inp.get("max_fob_median"))
        reasons.append(f"{fh:.0%} of the price is left after referral, FBA and freight"
                       + (f"; sourcing ceiling ${fob:,.2f} FOB" if fob is not None else "")
                       + (f" ({inp['landed_cost_basis']})" if inp.get("landed_cost_basis") else ""))
    missing = [d for d in DIMENSIONS if dims[d] is None]
    if missing:
        reasons.append("Not measured: " + ", ".join(m.replace("_", " ") for m in missing))
    gates = c.get("gates") or {}
    blocked = [r["code"] for r in risks if gates.get(r["code"]) == "block"]
    if blocked:
        reasons.insert(0, "Gated (not ranked) by " + ", ".join(blocked) + ": resolve before scoring (config opportunity.gates)")
    status = "gated" if blocked else "detected" if enough else "insufficient_evidence"
    return {"opportunity_score": score if enough and not blocked else None, "raw_score": score, "evidence_coverage": coverage,
            "status": status, "gated_by": blocked, "dimensions": dims, "evidence_matrix": matrix,
            "patterns": patterns, "risks": risks, "reasons": reasons, "confidence": dc}


def review_inputs(reports: list) -> dict:
    """Customer pain from review text (spec 27-30): the review-weighted pain score of the scope's reports
    (``dmie.engine.pain`` PainReport objects with status ok) and its most frequent complaints."""
    from dmie.engine.pain import review_pain_score

    ok = [r for r in reports if r is not None and r.status == "ok" and r.reviews]
    if not ok:
        return {}
    n = sum(r.reviews for r in ok)
    score = sum((review_pain_score(r) or 0) * r.reviews for r in ok) / n
    comp: dict[str, int] = {}
    for r in ok:
        for c in r.complaints:
            comp[c["aspect"]] = comp.get(c["aspect"], 0) + int(c["mentions"])
    top = sorted(comp.items(), key=lambda kv: -kv[1])[:3]
    return {"review_pain": round(float(score), 4), "review_count": int(n),
            "top_complaints": [{"aspect": a, "share_of_reviews": round(m / n, 3)} for a, m in top]}


def build(capacity: pd.DataFrame, segments: pd.DataFrame, products: pd.DataFrame, listings: pd.DataFrame,
          nodes: pd.DataFrame, supplier_matches: pd.DataFrame | None, has_suppliers: bool, projects: pd.DataFrame | None,
          pain_reports: dict | None = None, offline_evidence: pd.DataFrame | None = None) -> pd.DataFrame:
    """One opportunity row per segment and taxonomy node, ranked among the scopes with enough evidence."""
    seg = segments.set_index("segment_id") if len(segments) else pd.DataFrame()
    cat = capacity[capacity["scope"] == "category"]
    cat_p75 = _f(cat["price_p75"].iat[0]) if len(cat) else None
    sm_count = (supplier_matches.groupby("segment_id").size() if supplier_matches is not None and len(supplier_matches) else pd.Series(dtype=int))
    proj = {}
    if projects is not None and len(projects):
        for r in projects.itertuples():
            if r.segment_id:
                proj[str(r.segment_id)] = {"project_id": r.id, "stage": r.stage, "project_status": r.status}
    rows = []
    for r in capacity[capacity["scope"].isin(["segment", "taxonomy"])].to_dict("records"):
        sid = r["scope_id"]
        if r["scope"] == "segment":
            P = products[products["segment_id"].astype(str) == sid]
        else:
            ids = set(json.loads(nodes.loc[nodes["node_key"] == sid, "product_ids"].iat[0])) if len(nodes) else set()
            P = products[products["product_id"].astype(str).isin(ids)]
        L = listings[listings["product_id"].isin(P["product_id"])]
        bases: dict = {"demand": "demand model (modeled)", "competition_gap": "brand HHI on modeled revenue",
                       "data_confidence": "median product data confidence"}
        inp = {k: r.get(k) for k in ("products", "revenue_est", "revenue_lo", "revenue_hi", "hhi", "data_confidence",
                                     "price_median", "top3_share", "brands", "fee_headroom", "max_fob_median",
                                     "landed_cost_basis")}
        if r["scope"] == "segment" and sid in seg.index:
            s = seg.loc[sid]
            inp["growth_12m"] = _f(s.get("trend_growth_12m"))
            if inp["growth_12m"] is not None:
                bases["growth"] = f"trend engine ({s.get('trend_label')})"
            inp["quality_gap_share"] = _f(s.get("quality_gap_share"))
            inp["margin_rate"] = _f(s.get("margin_rate_median"))
            n_ent = _f(s.get("entrants")) or 0
            if n_ent > 0:                                          # a borrowed market rate is not segment evidence
                inp["entrant_success_rate"] = _f(s.get("entrant_success_rate"))
                bases["entry_ease"] = f"{int(n_ent)} segment entrants (metrics v3, shrunk when few)"
        else:
            ni = node_inputs(P, L)
            inp.update({k: v for k, v in ni.items() if k != "entrant_basis"})
            if "entrant_basis" in ni:
                bases["entry_ease"] = ni["entrant_basis"]
        inp["applications"] = {a for s in P.get("applications", pd.Series(dtype=object)).dropna() for a in str(s).split(",")}
        if _f(inp.get("fee_headroom")) is not None and _f(inp.get("margin_rate")) is None:
            bases["pricing_margin"] = "fee headroom: " + str(inp.get("landed_cost_basis"))
        off = offline.score(offline_evidence, str(r["label"]), inp["applications"]) if offline_evidence is not None else {}
        if off:
            inp["offline_evidence_score"] = off["offline_evidence_score"]
            bases["offline_strength"] = off["offline_basis"]
            inp["offline_basis"] = off["offline_basis"]
        pain = pain_reports or {}
        reps = ([pain.get(f"segment:{sid}")] if r["scope"] == "segment"
                else [pain.get(f"product:{p}") for p in P["product_id"].astype(str)])
        inp.update(review_inputs(reps))
        if inp.get("review_pain") is not None:
            bases["customer_pain"] = f"review text: {inp['review_count']} reviews (complaint share and sentiment)"
        elif inp.get("quality_gap_share") is not None:
            bases["customer_pain"] = "rating-based proxy: share of demand rated below the threshold (no review text)"
        if has_suppliers:
            inp["supplier_matches"] = int(sm_count.get(sid, 0)) if r["scope"] == "segment" else None
            if inp["supplier_matches"] is not None:
                bases["supplier_availability"] = "suppliers matched to the segment"
        inp["titles"] = P.get("title", pd.Series(dtype=object)).dropna().astype(str).tolist()
        inp["price_premium"] = cat_p75 is not None and _f(r.get("price_median")) is not None and r["price_median"] >= cat_p75
        attrs = [json.loads(a) for a in P.get("kn_attributes", pd.Series(dtype=object)).dropna()]
        combos = {json.dumps(a, sort_keys=True) for a in attrs if a}
        inp["low_differentiation"] = len(P) >= 5 and len(combos) / max(len(P), 1) < 0.3
        inp["bases"] = bases
        out = score_scope(inp)
        link = proj.get(sid)
        rows.append({"scope": r["scope"], "scope_id": sid, "label": r["label"], "products": r["products"], **{
            "opportunity_score": out["opportunity_score"], "raw_score": out["raw_score"],
            "evidence_coverage": out["evidence_coverage"], "status": (link["stage"] if link else out["status"]),
            "project_id": link["project_id"] if link else None, "confidence": out["confidence"],
            **{f"dim_{d}": out["dimensions"][d] for d in DIMENSIONS},
            "evidence_matrix": json.dumps(out["evidence_matrix"]), "patterns": json.dumps(out["patterns"]),
            "risks": json.dumps(out["risks"]), "reasons": json.dumps(out["reasons"]), "gated_by": json.dumps(out["gated_by"])}})
    cols = ["scope", "scope_id", "label", "products", "opportunity_score", "raw_score", "evidence_coverage", "status", "project_id",
            "confidence", *[f"dim_{d}" for d in DIMENSIONS], "evidence_matrix", "patterns", "risks", "reasons", "gated_by"]
    df = pd.DataFrame(rows, columns=cols)
    df["rank"] = pd.Series(dtype=float)
    if len(df):
        df["rank"] = df["opportunity_score"].rank(ascending=False, method="min")
        df = df.sort_values(["opportunity_score", "raw_score"], ascending=False, na_position="last").reset_index(drop=True)
    return df

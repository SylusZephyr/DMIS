"""Knowledge graph on metrics v3 (Phase 6): a typed ontology whose every edge carries its evidence.

Nodes
    Industry > Category (market) > ProductFamily (marketplace sub-category) > Segment > Product > Listing,
    plus Brand, Seller, Feature (a tested demand gap), Recommendation, Momentum (significant launch
    momentum), Supplier, Country, CustomerProblem.

Edges -- every edge has ``props``:
    evidence  one line a person can read ("12.6% (9.5-16.9%) of segment revenue, P(#1) 58%")
    metric    what was measured; value / low / high / n   the number, its 95% interval, the sample size
    strength  0..1, comparable within an edge type (share, lift confidence, similarity...)
    basis     fact (a source field) | estimate (demand model) | test (a statistical test) | similarity
Nothing is drawn without a reason: gap edges exist only for significant gaps, momentum only when the
binomial test is significant, supplier edges only above the match threshold.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dip.metrics import config


def _f(v):
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except (TypeError, ValueError):
        return None


def _money(v):
    v = _f(v)
    if v is None:
        return "n/a"
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:,.0f}"


def _pct(v):
    v = _f(v)
    return "n/a" if v is None else f"{v:.1%}" if v < 0.1 else f"{v:.0%}"


def ev(evidence: str, basis: str, strength: float | None = None, metric: str | None = None, value=None, low=None, high=None,
       n=None, **extra) -> dict:
    s = _f(strength)
    return {"evidence": evidence, "basis": basis, "strength": None if s is None else round(float(np.clip(s, 0, 1)), 4),
            "metric": metric, "value": _f(value), "low": _f(low), "high": _f(high), "n": None if n is None else int(n), **extra}


def build(market: str, branch: str, families: pd.DataFrame, segments: pd.DataFrame, products: pd.DataFrame,
          listings: pd.DataFrame, suppliers: pd.DataFrame | None = None, matches: pd.DataFrame | None = None,
          pain: dict | None = None, gaps: pd.DataFrame | None = None, recs: pd.DataFrame | None = None,
          seg_brands: pd.DataFrame | None = None, momentum: pd.DataFrame | None = None,
          similar_pairs: list[tuple[str, str, float]] | None = None, trends: pd.DataFrame | None = None) -> tuple[list, list]:
    nodes: list[dict] = []
    edges: list[dict] = []

    def N(i, k, label, **p):
        nodes.append({"id": i, "kind": k, "label": label, "props": p})

    def E(s, t, rel, props):
        edges.append({"source": s, "target": t, "rel": rel, "props": props})

    root, br, cat = "industry:dental", f"industry:dental/{branch}", f"category:{market}"
    mrev = float(pd.to_numeric(segments.get("revenue_est"), errors="coerce").sum()) if len(segments) else 0.0
    N(root, "Industry", "Dental Industry")
    N(br, "Industry", branch)
    E(br, root, "BELONGS_TO", ev("taxonomy", "fact", 1.0))
    N(cat, "Category", market, revenue=mrev or None, products=int(len(products)))
    E(cat, br, "BELONGS_TO", ev("taxonomy", "fact", 1.0))

    seg_rev = pd.to_numeric(segments.get("revenue_est"), errors="coerce") if len(segments) else pd.Series(dtype=float)
    fam_rev = seg_rev.groupby(segments["family_id"]).sum() if "family_id" in segments else pd.Series(dtype=float)
    for _, f in families.iterrows():
        fid = f"family:{market}/{f['family_id']}"
        r = _f(fam_rev.get(f["family_id"]))
        N(fid, "ProductFamily", f["family_label"], listings=int(f.get("listings", 0)), revenue=r)
        E(cat, fid, "HAS_FAMILY", ev(f"{_money(r)}/month = {_pct(r / mrev if r and mrev else None)} of the market's estimated revenue",
                                     "estimate", r / mrev if r and mrev else None, "revenue_share", r / mrev if r and mrev else None,
                                     n=f.get("listings")))
    for _, s in segments.iterrows():
        sid = f"segment:{market}/{s['segment_id']}"
        r, lo, hi = _f(s.get("revenue_est")), _f(s.get("revenue_lo")), _f(s.get("revenue_hi"))
        N(sid, "Segment", s["segment_label"], revenue=r, revenue_lo=lo, revenue_hi=hi, opportunity=_f(s.get("opportunity_index")),
          level=s.get("opportunity_level"), hhi=_f(s.get("hhi_est")), description=s.get("segment_description"),
          description_zh=s.get("segment_description_zh"), segment_id=s["segment_id"])
        fam = s.get("family_id")
        fr = _f(fam_rev.get(fam)) if isinstance(fam, str) else mrev
        E(f"family:{market}/{fam}" if isinstance(fam, str) else cat, sid, "HAS_SEGMENT",
          ev(f"{_money(r)}/month ({_money(lo)}–{_money(hi)}) = {_pct(r / fr if r and fr else None)} of its sub-category", "estimate",
             r / fr if r and fr else None, "revenue_month", r, lo, hi, s.get("listings_v3") or s.get("listings")))
        E(sid, cat, "BELONGS_TO", ev(f"{_pct(r / mrev if r and mrev else None)} of the market's estimated revenue", "estimate",
                                     r / mrev if r and mrev else None, "revenue_share", r / mrev if r and mrev else None))

    prev_seg = products.groupby("segment_id")["revenue_est"].transform("sum") if "revenue_est" in products else None
    for i, p in products.iterrows():
        pid = f"product:{p['product_id']}"
        r = _f(p.get("revenue_est"))
        N(pid, "Product", str(p["title"])[:120], revenue=r, revenue_lo=_f(p.get("revenue_lo")), revenue_hi=_f(p.get("revenue_hi")),
          units=_f(p.get("units_est")), price=_f(p.get("price")), opportunity=_f(p.get("opportunity_score")),
          listings=int(p.get("listing_count", 1) or 1), image=p.get("image"), product_id=p["product_id"])
        tot = _f(prev_seg.iat[i]) if prev_seg is not None else None
        E(f"segment:{market}/{p['segment_id']}", pid, "HAS_MODEL",
          ev(f"{_money(r)}/month = {_pct(r / tot if r and tot else None)} of the segment's estimated revenue", "estimate",
             r / tot if r and tot else None, "revenue_share", r / tot if r and tot else None))
        if isinstance(p.get("brand"), str) and p["brand"].strip():
            bid = f"brand:{p['brand'].strip().lower()}"
            N(bid, "Brand", p["brand"].strip())
            E(pid, bid, "MADE_BY", ev("brand field of the listing", "fact", 1.0))
    for _, lrow in listings.iterrows():
        lid = f"listing:{lrow['id']}"
        N(lid, "Listing", str(lrow["id"]), price=_f(lrow.get("price")), units=_f(lrow.get("units_est")),
          observed=lrow.get("sales_observation"), best=bool(lrow.get("is_best_listing", False)))
        E(f"product:{lrow['product_id']}", lid, "HAS_LISTING", ev("grouped into this product by entity resolution", "fact", 1.0))
        if isinstance(lrow.get("seller"), str) and lrow["seller"].strip():
            sel = f"seller:{lrow['seller'].strip().lower()}"
            N(sel, "Seller", lrow["seller"])
            E(lid, sel, "SOLD_BY", ev("seller field of the listing", "fact", 1.0))

    # brand shares per segment (joint simulation)
    for _, b in (seg_brands if seg_brands is not None else pd.DataFrame()).iterrows():
        bid = f"brand:{str(b['brand']).strip().lower()}"
        N(bid, "Brand", str(b["brand"]).strip())
        E(f"segment:{market}/{b['segment_id']}", bid, "BRAND_SHARE",
          ev(f"{_pct(b['share_est'])} ({_pct(b['share_lo'])}–{_pct(b['share_hi'])}) of segment revenue; P(#1) {_pct(b['p_top'])}",
             "estimate", b["share_est"], "revenue_share", b["share_est"], b["share_lo"], b["share_hi"], p_top=_f(b["p_top"])))

    # similarity + price-adjacent competition
    for a, c, sc in similar_pairs or []:
        E(f"product:{a}", f"product:{c}", "PRODUCT_SIMILAR_TO", ev(f"title embedding cosine {sc:.2f}", "similarity", sc, "cosine", sc))
    if len(products) > 1 and "price" in products:
        for _, g in products.dropna(subset=["price"]).groupby("segment_id"):
            g = g.sort_values("price")
            for (a, pa), (c, pc) in zip(g[["product_id", "price"]].values[:-1], g[["product_id", "price"]].values[1:]):
                gap = abs(pc - pa) / max(pa, pc, 1e-9)
                E(f"product:{a}", f"product:{c}", "COMPETES_WITH",
                  ev(f"same segment, adjacent price (${pa:,.2f} vs ${pc:,.2f}, {gap:.0%} apart)", "fact", 1 - gap, "price_gap", gap))

    # significant demand gaps -> features -> recommendation
    if gaps is not None and len(gaps):
        for _, g in gaps[gaps["is_gap"].astype(bool)].iterrows():
            fid = f"feature:{market}/{g['feature']}"
            N(fid, "Feature", g["feature"], feature_kind=g.get("kind"))
            E(f"segment:{market}/{g['segment_id']}", fid, "HAS_GAP",
              ev(f"listings with it sell {g['lift']:.2f}× ({g['lift_lo']:.2f}–{g['lift_hi']:.2f}); "
                 f"{_pct(g['supply_share'])} of listings → {_pct(g['demand_share'])} of demand; q = {g['q_value']:.3f}",
                 "test", 1 - float(g["q_value"]), "lift", g["lift"], g["lift_lo"], g["lift_hi"], g.get("listings"),
                 q_value=_f(g["q_value"])))
    if recs is not None and len(recs):
        for _, r in recs.iterrows():
            feats = r["features"]
            feats = list(feats) if isinstance(feats, (list, np.ndarray)) else []
            rid = f"recommendation:{market}/{r['segment_id']}"
            pr = r["price_range"]
            if isinstance(pr, str) and pr.startswith("["):
                import json
                try:
                    pr = json.loads(pr)
                except ValueError:
                    pass
            price_txt = f"${pr[0]:,.2f}–{pr[1]:,.2f}" if isinstance(pr, (list, tuple, np.ndarray)) and len(pr) == 2 else str(pr)
            N(rid, "Recommendation", (", ".join(feats) or "best price band") + f" · {price_txt}",
              expected_units=_f(r.get("expected_units")), expected_units_lo=_f(r.get("expected_units_lo")),
              expected_units_hi=_f(r.get("expected_units_hi")), basis=r.get("basis"))
            E(f"segment:{market}/{r['segment_id']}", rid, "RECOMMENDS",
              ev(f"expected {_f(r.get('expected_units')) or 0:.0f} units/month ({_f(r.get('expected_units_lo')) or 0:.0f}–"
                 f"{_f(r.get('expected_units_hi')) or 0:.0f}) from comparables with these features", "estimate",
                 _f(r.get("opportunity_index")) / 100 if _f(r.get("opportunity_index")) is not None else None,
                 "expected_units", r.get("expected_units"), r.get("expected_units_lo"), r.get("expected_units_hi")))
            for f in feats:
                E(rid, f"feature:{market}/{f}", "INCLUDES", ev("feature of the recommended specification", "test", 1.0))

    # significant launch momentum only
    if momentum is not None and len(momentum):
        for _, m in momentum[momentum["significant"].astype(bool)].iterrows():
            scope = cat if m["scope_id"] == "" else f"segment:{market}/{m['scope_id']}"
            mid = f"momentum:{market}/{m['scope_id'] or '__market__'}"
            N(mid, "Momentum", f"launches {m['direction']} ({m['recent']} vs {m['previous']})", direction=m["direction"])
            E(scope, mid, "HAS_MOMENTUM",
              ev(f"{m['recent']} launches in the last year vs {m['previous']} the year before; q = {m['q_value']:.3f}", "test",
                 1 - float(m["q_value"]), "rate_ratio", m["rate_ratio"], n=int(m["recent"] + m["previous"]), q_value=_f(m["q_value"])))

    # trend labels (trend detection): kept with their evidence; strength = the detector's confidence, so a
    # label resting on one snapshot is visibly weak
    if trends is not None and len(trends):
        for r in trends.to_dict("records"):
            if not isinstance(r.get("trend"), str) or r["trend"] == "Insufficient evidence":
                continue
            scope = r["scope"]
            tid = f"trend:{market}/{scope}"
            conf = _f(r.get("confidence"))
            evidence = r.get("evidence")
            if isinstance(evidence, str) and evidence.startswith("["):
                import json
                try:
                    evidence = json.loads(evidence)
                except ValueError:
                    pass
            evidence = "; ".join(evidence) if isinstance(evidence, (list, np.ndarray)) else (evidence or "")
            periods = r.get("periods")
            N(tid, "Trend", f"{r['trend']} ({conf or 0:.0f}%)", trend=r["trend"], confidence=conf, periods=periods)
            E(cat if scope == "__market__" else f"segment:{market}/{scope}", tid, "HAS_TREND",
              ev(f"{r['trend']}, confidence {conf or 0:.0f}% from {periods or 1} period(s)" + (f": {evidence}" if evidence else ""),
                 "estimate", None if conf is None else conf / 100, "trend_confidence", conf, n=periods))

    # suppliers
    thr = config().get("graph", {}).get("min_supplier_match", 0.15)
    for _, m in (matches if matches is not None else pd.DataFrame()).iterrows():
        if m["match_score"] < thr:
            continue
        E(f"segment:{market}/{m['segment_id']}", f"supplier:{m['supplier_id']}", "SUPPLIED_BY",
          ev(f"catalogue text match {m['match_score']:.2f} to the segment's products", "similarity", m["match_score"], "match_score",
             m["match_score"]))
    if suppliers is not None and len(suppliers):
        for _, s in suppliers.iterrows():
            sup_id = f"supplier:{s['supplier_id']}"
            N(sup_id, "Supplier", s["name"], score=_f(s.get("supplier_score")), country=s.get("country"))
            if isinstance(s.get("country"), str) and s["country"].strip():
                cid = f"country:{s['country'].strip().lower()}"
                N(cid, "Country", s["country"].strip())
                E(sup_id, cid, "LOCATED_IN", ev("supplier's country field", "fact", 1.0))

    # customer problems from review text
    for scope, rep in (pain or {}).items():
        if scope == "__market__" or getattr(rep, "status", None) != "ok":
            continue
        kind, key = scope.split(":", 1)
        src = f"segment:{market}/{key}" if kind == "segment" else f"product:{key}"
        for c in rep.complaints[:5]:
            pid = f"problem:{market}/{c['aspect']}"
            N(pid, "CustomerProblem", c["aspect"], opportunity=c.get("opportunity"))
            E(src, pid, "HAS_PROBLEM", ev(f"{_pct(c.get('share_of_reviews'))} of reviews ({int(c.get('mentions', 0))} mentions)", "fact",
                                          c.get("share_of_reviews"), "share_of_reviews", c.get("share_of_reviews"), n=c.get("mentions")))

    seen, uniq = set(), []
    for n in nodes:
        if n["id"] not in seen:
            seen.add(n["id"])
            uniq.append(n)
    return uniq, edges

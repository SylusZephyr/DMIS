"""Product requirements brief (spec 77): what a new product for one scope must be, from computed facts only.

For a segment or taxonomy node, assembled from tables the pipeline already computed (nothing is re-estimated,
no model writes text):

* target        the scope, its canonical products and modeled demand (with interval), opportunity score
* price         recommended band = the gaps engine's significant price band when the segment has one, else the
                scope's interquartile price range; entry / premium tier thresholds
* must-have     attributes that most of the scope's top sellers share (value held by at least ``must_have_share``
                of the top ``top_n`` products by modeled revenue that state the attribute)
* differentiators  statistically significant demand gaps (features) of the segment, with their evidence
* configuration the most common component set among the top sellers
* pain to fix   top review complaints when review text exists, else the rating-based quality gap
* sourcing      fee headroom and the max-FOB sourcing ceiling with their assumptions
* compliance    regulatory and hazmat risks of the scope
* evidence      evidence coverage, unmeasured dimensions, and the reasons of the opportunity engine

Every section states its basis; a section without data says so instead of being filled in.
"""

from __future__ import annotations

import json
from collections import Counter

import numpy as np
import pandas as pd

from dip.knowledge import config


def _cfg() -> dict:
    return config()["requirements"]


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _j(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return default if v is None else v


def must_haves(P: pd.DataFrame) -> tuple[list[dict], int]:
    c = _cfg()
    top = P.sort_values("revenue_est", ascending=False, na_position="last").head(int(c["top_n"])) if "revenue_est" in P else P.head(0)
    by: dict[str, list] = {}
    for a in top.get("kn_attributes", pd.Series(dtype=object)).dropna():
        for k, v in _j(a, {}).items():
            val = v.get("value") if isinstance(v, dict) else v      # products hold consensus values, listings {value, ...}
            if val is not None:
                by.setdefault(k, []).append(json.dumps(val, ensure_ascii=False))
    out = []
    for k, vals in by.items():
        top_val, n = Counter(vals).most_common(1)[0]
        share = n / len(top)
        if len(top) and share >= float(c["must_have_share"]):
            out.append({"attribute": k, "value": json.loads(top_val), "share_of_top_sellers": round(share, 2)})
    return sorted(out, key=lambda x: -x["share_of_top_sellers"]), int(len(top))


def build(scope: str, scope_id: str, capacity: pd.DataFrame, opportunities: pd.DataFrame, products: pd.DataFrame,
          nodes: pd.DataFrame, recommendations: pd.DataFrame) -> dict | None:
    cap = capacity[(capacity["scope"] == scope) & (capacity["scope_id"].astype(str) == scope_id)]
    if not len(cap):
        return None
    r = cap.iloc[0].to_dict()
    o = opportunities[(opportunities["scope"] == scope) & (opportunities["scope_id"].astype(str) == scope_id)] if len(opportunities) else opportunities
    opp = o.iloc[0].to_dict() if len(o) else {}
    if scope == "segment":
        P = products[products["segment_id"].astype(str) == scope_id]
    else:
        ids = set(_j(nodes.loc[nodes["node_key"] == scope_id, "product_ids"].iat[0], [])) if len(nodes) and (nodes["node_key"] == scope_id).any() else set()
        P = products[products["product_id"].astype(str).isin(ids)]
    rec = recommendations[recommendations["segment_id"].astype(str) == scope_id] if scope == "segment" and len(recommendations) else pd.DataFrame()
    rec_row = rec.iloc[0].to_dict() if len(rec) else None

    pr = _j(rec_row.get("price_range"), None) if rec_row else None
    price = ({"band": [round(float(pr[0]), 2), round(float(pr[1]), 2)], "basis": "significant price band of the gaps engine"}
             if pr and len(pr) == 2 else
             {"band": [_f(r.get("price_p25")), _f(r.get("price_p75"))], "basis": "interquartile price range of the scope"}
             if _f(r.get("price_p25")) is not None else {"band": None, "basis": "no prices"})
    price.update({"median": _f(r.get("price_median")), "entry_below": _f(r.get("tier_entry_below")),
                  "premium_from": _f(r.get("tier_premium_from"))})

    mh, n_top = must_haves(P)
    feats = [str(x) for x in _j(rec_row.get("features"), [])] if rec_row else []
    fev = _j(rec_row.get("feature_evidence"), []) if rec_row else []
    cfgs = P.sort_values("revenue_est", ascending=False, na_position="last").head(int(_cfg()["top_n"]))["configuration_key"].dropna() \
        if "configuration_key" in P and "revenue_est" in P else pd.Series(dtype=object)
    risks = _j(opp.get("risks"), [])
    reasons = _j(opp.get("reasons"), [])
    matrix = _j(opp.get("evidence_matrix"), {})
    pain_cell = matrix.get("customer_pain") or {}
    complaints = next((x for x in reasons if isinstance(x, str) and "top complaints" in x), None)
    return {
        "scope": scope, "scope_id": scope_id, "label": r.get("label"),
        "target": {"products": int(r.get("products") or 0), "listings": int(r.get("listings") or 0), "brands": r.get("brands"),
                   "revenue_est": _f(r.get("revenue_est")), "revenue_lo": _f(r.get("revenue_lo")), "revenue_hi": _f(r.get("revenue_hi")),
                   "opportunity_score": _f(opp.get("opportunity_score")), "status": opp.get("status"),
                   "evidence_coverage": _f(opp.get("evidence_coverage"))},
        "price": price,
        "must_have": {"attributes": mh, "top_sellers": n_top,
                      "basis": f"values shared by at least {_cfg()['must_have_share']:.0%} of the top {n_top} products by modeled revenue"},
        "differentiators": {"features": feats, "evidence": fev,
                            "basis": "statistically significant demand gaps (gaps engine)" if rec_row else "no gap analysis for this scope"},
        "configuration": {"most_common": Counter(cfgs).most_common(1)[0][0] if len(cfgs) else None,
                          "basis": "most common component set among the top sellers" if len(cfgs) else "no component sets stated"},
        "pain_to_fix": {"summary": complaints or (f"{pain_cell.get('value'):.0%} of demand goes to listings rated below the quality threshold"
                                                  if _f(pain_cell.get("value")) is not None and pain_cell.get("metric") == "quality_gap_share" else None),
                        "basis": pain_cell.get("basis") or "no customer-pain evidence"},
        "sourcing": {"fee_headroom": _f(r.get("fee_headroom")), "max_fob": _f(r.get("max_fob_median")),
                     "max_fob_by_duty": _j(r.get("max_fob_by_duty"), None),
                     "basis": r.get("landed_cost_basis") or "needs FBA fee and package weight on enough listings"},
        "compliance": [x for x in risks if isinstance(x, dict) and x.get("code") in ("regulatory", "hazmat")],
        "evidence": {"reasons": reasons, "not_measured": [d for d, v in matrix.items() if isinstance(v, dict) and v.get("score") is None]},
    }


def markdown(req: dict, market: str) -> str:
    def m(v):
        return "n/a" if v is None else f"${v:,.2f}" if v < 1000 else f"${v:,.0f}"
    t, p = req["target"], req["price"]
    lines = [f"# Product requirements: {req['label']}", f"_{market} · {req['scope']} · generated from computed platform data_", "",
             "## Target",
             f"- {t['products']} canonical products ({t['listings']} listings, {t['brands'] or 'n/a'} brands)",
             f"- Modeled demand {m(t['revenue_est'])}/month (95% {m(t['revenue_lo'])}–{m(t['revenue_hi'])})",
             f"- Opportunity score {t['opportunity_score']:.0f} (evidence coverage {t['evidence_coverage']:.0%})"
             if t["opportunity_score"] is not None and t["evidence_coverage"] is not None else f"- Opportunity: {t['status'] or 'not scored'}", "",
             "## Price",
             (f"- Recommended band {m(p['band'][0])}–{m(p['band'][1])} ({p['basis']})" if p.get("band") and p["band"][0] is not None
              else f"- No price band ({p['basis']})"),
             f"- Median {m(p['median'])}; entry below {m(p['entry_below'])}; premium from {m(p['premium_from'])}", "",
             "## Must-have attributes", f"_{req['must_have']['basis']}_"]
    lines += [f"- {a['attribute']}: {a['value']} ({a['share_of_top_sellers']:.0%} of top sellers)" for a in req["must_have"]["attributes"]] or ["- none shared widely enough"]
    lines += ["", "## Differentiators", f"_{req['differentiators']['basis']}_"]
    lines += [f"- {f}" for f in req["differentiators"]["features"]] or ["- none significant"]
    lines += ["", "## Configuration", f"- {req['configuration']['most_common'] or 'none stated'} ({req['configuration']['basis']})",
              "", "## Customer pain to fix", f"- {req['pain_to_fix']['summary'] or 'no evidence'} ({req['pain_to_fix']['basis']})",
              "", "## Sourcing",
              (f"- {req['sourcing']['fee_headroom']:.0%} of the price is left after fees and freight; sourcing ceiling {m(req['sourcing']['max_fob'])} FOB"
               if req["sourcing"]["fee_headroom"] is not None else "- not computed"), f"- Basis: {req['sourcing']['basis']}",
              *([("- By duty scenario: " + "; ".join(f"{k.replace('_', ' ')} {m(v)}" for k, v in req["sourcing"]["max_fob_by_duty"].items()))]
                if req["sourcing"].get("max_fob_by_duty") else []),
              "", "## Compliance"]
    lines += [f"- {x['code']}: {x.get('detail', '')}" for x in req["compliance"]] or ["- no regulatory or hazmat flag"]
    lines += ["", "## Evidence"] + [f"{i + 1}. {x}" for i, x in enumerate(req["evidence"]["reasons"])]
    if req["evidence"]["not_measured"]:
        lines.append(f"- Not measured: {', '.join(req['evidence']['not_measured'])}")
    return "\n".join(lines) + "\n"

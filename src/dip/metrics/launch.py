"""Launch simulator on metrics v3 (Phase 6).

"What happens if we launch this product at this price?" -- answered from the market's fitted demand model
(``demand.model_card``), never from scores:

1. **Placement** -- the idea's text is matched to the market's listings (TF-IDF cosine); the segment is the
   similarity-weighted vote of the nearest listings (or the one the user picks).
2. **Demand** -- posterior-predictive draws of monthly units for a new listing with the idea's price, the
   segment's sub-category, an assumed rating and age: parameter uncertainty from the model's bootstrap
   draws, listing noise from its error family, plus the segment's shrunken residual offset
   (empirical Bayes: segments inside one sub-category differ).
3. **Economics** -- per draw: units x unit margin - fixed monthly cost. Unit cost comes from the user; a
   source cost field is used only when the engine found it genuine (a price-derived "cost" is a ceiling,
   never a cost). Break-even units, P(profit > 0), P(profit >= target), payback of the launch cost.
4. **Price curve** -- the same draws (common random numbers) over a price grid inside the segment's observed
   range. The price effect is a cross-sectional association, not a causal elasticity (stated).
5. **Empirical check** -- what the segment's actual entrants sell, next to the model's prediction.
6. **Gaps** -- which statistically significant demand gaps (§10) the idea's text/spec/price covers.
7. **Risks** -- rule-coded from computed values (concentration, entrant success, rating bar, differentiation,
   price extrapolation, weak evidence, negative margin), each with its value.

Scenario comparison runs every scenario on the same segment with the same random numbers, so differences
come from the scenarios, not from simulation noise.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from dip.metrics import config
from dip.metrics.demand import card_mu, predict_draws
from dip.storage import lake


def _num(v) -> float | None:
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except (TypeError, ValueError):
        return None


def _q(d: np.ndarray, lvl: float) -> dict:
    a = (1 - lvl) / 2
    return {"mean": float(d.mean()), "p10": float(np.quantile(d, 0.1)), "median": float(np.median(d)),
            "p90": float(np.quantile(d, 0.9)), "lo": float(np.quantile(d, a)), "hi": float(np.quantile(d, 1 - a))}


def load(market: str) -> dict | None:
    """Everything the simulator needs for one market (read once per request)."""
    card = lake.read_curated("demand_model", market)
    if card.empty:
        return None
    cols = ["id", "title", "brand", "price", "rating", "launch_date", "category", "segment_id", "segment_label", "family_label",
            "units_est", "units_lo", "units_hi", "revenue_est", "is_entrant", "fulfilment_fee", "unit_cost", "unit_cost_issue",
            "price_band", "specs", "age_days"]
    if "reviews" in lake.read_curated("listings", market, limit=0).columns:     # the demand model may use reviews
        cols.append("reviews")
    L = lake.read_curated("listings", market, columns=cols)
    S = lake.read_curated("segments", market)
    return {"card": json.loads(card["card"].iat[0]), "listings": L, "segments": S,
            "gaps": lake.read_curated("gaps", market), "recs": lake.read_curated("recommendations", market)}


def place(L: pd.DataFrame, text: str, segment_id: str | None, k: int) -> dict:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    docs = L["title"].fillna("").astype(str).tolist()
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
    M = vec.fit_transform(docs + [text])
    sim = cosine_similarity(M[-1], M[:-1]).ravel()
    order = np.argsort(-sim)[:k]
    near = L.iloc[order].assign(similarity=sim[order])
    votes = near.groupby("segment_id")["similarity"].sum()
    votes = (votes / votes.sum()).sort_values(ascending=False) if votes.sum() > 0 else votes
    chosen = segment_id if segment_id and segment_id in set(L["segment_id"]) else (votes.index[0] if len(votes) else L["segment_id"].mode().iat[0])
    return {"segment_id": chosen, "votes": votes, "similarity": sim, "user_chosen": bool(segment_id and chosen == segment_id)}


def segment_offset(card: dict, L: pd.DataFrame, seg_id: str, prior: float) -> tuple[float, int]:
    """Shrunken mean log-residual of the segment relative to the whole market (the estimator's bias on
    log E[X] cancels in the difference)."""
    ok = pd.to_numeric(L["units_est"], errors="coerce") > 0
    Lk = L[ok]
    if Lk.empty:
        return 0.0, 0
    r = np.log(Lk["units_est"].to_numpy(dtype=float)) - card_mu(card, Lk)
    in_seg = (Lk["segment_id"] == seg_id).to_numpy()
    n = int(in_seg.sum())
    if n == 0:
        return 0.0, 0
    diff = r[in_seg] - r.mean()
    return float(diff.sum() / (n + prior)), n


def _features(text: str, price: float, seg_listings: pd.DataFrame) -> set[str]:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    from dip.metrics.gaps import _spec_features, _term_features
    from dmie.engine.features import extract_specs

    stop = set(ENGLISH_STOP_WORDS) | {s.lower() for s in config()["segmentation"]["stop_terms"]}
    f = _term_features(text, stop) | _spec_features(extract_specs(text))
    pb = seg_listings.dropna(subset=["price_band", "price"])
    if len(pb):
        nb = int(pb["price_band"].max()) + 1
        rng = pb.groupby("price_band")["price"].agg(["min", "max"])
        inside = rng[(rng["min"] <= price) & (price <= rng["max"])]
        band = int(inside.index[0]) if len(inside) else int((rng["min"] - price).abs().idxmin())
        f.add(f"price band {band + 1}/{nb}")
    return f


def _economics(idea: dict, seg_l: pd.DataFrame, src: dict) -> dict:
    ue = config()["unit_economics"]
    fee = idea.get("referral_fee")
    fee = ue["referral_fee"] if fee is None else float(fee)
    fba, fba_basis = _num(idea.get("fulfilment_fee")), "given"
    if fba is None:
        v = pd.to_numeric(seg_l["fulfilment_fee"], errors="coerce").dropna() if "fulfilment_fee" in seg_l else pd.Series(dtype=float)
        if len(v):
            fba, fba_basis = float(v.median()), f"segment median of {len(v)} listings' fulfilment fee"
        else:
            fba, fba_basis = 0.0, "not in source and not given (0 assumed)"
    cost, cost_basis = _num(idea.get("unit_cost")), "given"
    if cost is None:
        genuine = pd.Series(dtype=float)
        if "unit_cost" in seg_l and not src.get("derived_field"):
            ok = seg_l["unit_cost_issue"].isna() if "unit_cost_issue" in seg_l else True
            genuine = pd.to_numeric(seg_l.loc[ok, "unit_cost"], errors="coerce").dropna()
        if len(genuine) >= 3:
            cost, cost_basis = float(genuine.median()), f"segment median of {len(genuine)} genuine source unit costs"
        else:
            cost_basis = ("the source's cost field is derived from price (a ceiling, not a cost) -- enter your unit cost"
                          if src.get("derived_field") else "no unit cost given or in source -- enter your unit cost")
    return {"referral_fee": fee, "fulfilment_fee": fba, "fulfilment_basis": fba_basis, "unit_cost": cost, "unit_cost_basis": cost_basis,
            "fixed_monthly_cost": float(idea.get("fixed_monthly_cost") or 0.0), "launch_cost": _num(idea.get("launch_cost"))}


def assumed_reviews(seg_l: pd.DataFrame, L: pd.DataFrame) -> tuple[float | None, str]:
    """Review count for a hypothetical new listing when the model uses reviews: the median of the segment's
    entrants (listings launched within the entrant window), else of the market's entrants, else unknown
    (the model's median imputation). A new listing does not have an established listing's reviews."""
    if "reviews" not in L:
        return None, "not in source"
    for scope, frame in (("segment", seg_l), ("market", L)):
        ent = frame[frame["is_entrant"].fillna(False).astype(bool)] if "is_entrant" in frame else frame.iloc[0:0]
        r = pd.to_numeric(ent["reviews"], errors="coerce").dropna()
        if len(r) >= 3:
            return float(r.median()), f"median reviews of the {scope}'s {len(r)} entrants (assumed)"
    return None, "no entrants with reviews: model median (assumed)"


def simulate(idea: dict, data: dict, market: str, summary: dict | None = None, segment_id: str | None = None,
             assume_rating: str = "median") -> dict:
    """``assume_rating``: the rating used when the idea gives none -- "median" (the segment's median listing;
    the launch simulator's default) or "rating_bar" (what a competitive entrant must reach: the median rating of
    the segment's top sellers; used for Opportunity Board concepts). A rating the user gives is always used as is."""
    cfg = config()["launch"]
    lvl = config()["engine"]["interval"]
    card, L, S = data["card"], data["listings"], data["segments"]
    price = float(idea["price"])
    text = f"{idea.get('title', '')} {idea.get('specs') or ''}".strip()
    pl = place(L, text, segment_id or idea.get("segment_id"), cfg["neighbours"])
    sid = pl["segment_id"]
    seg_l = L[L["segment_id"] == sid]
    seg = S.set_index("segment_id").loc[sid] if sid in set(S["segment_id"]) else pd.Series(dtype=object)
    prices = pd.to_numeric(seg_l["price"], errors="coerce").dropna()
    ratings = pd.to_numeric(seg_l["rating"], errors="coerce").dropna()
    rating = _num(idea.get("rating"))
    rating_basis = "given"
    rating_given = rating is not None
    bar = _num(seg.get("rating_bar"))
    if rating is None:
        if assume_rating == "rating_bar" and bar is not None:
            rating, rating_basis = bar, "segment rating bar (assumed: a competitive entrant matches the top sellers' median rating)"
        else:
            rating = float(ratings.median()) if len(ratings) else None
            rating_basis = "segment median rating (assumed)" if rating is not None else "unknown"
    reviews, reviews_basis = assumed_reviews(seg_l, L)
    if _num(idea.get("reviews")) is not None:
        reviews, reviews_basis = _num(idea.get("reviews")), "given"
    age = int(idea.get("age_days") or cfg["default_age_days"])
    as_of = pd.Timestamp(card["as_of"]) if card.get("as_of") else pd.Timestamp.today().normalize()
    category = seg_l["category"].mode().iat[0] if "category" in seg_l and seg_l["category"].notna().any() else None
    offset, n_off = segment_offset(card, L, sid, cfg["segment_offset_prior"])
    sims, seed = cfg["simulations"], cfg["seed"]

    def frame(p: float | np.ndarray) -> pd.DataFrame:
        p = np.atleast_1d(p).astype(float)
        f = pd.DataFrame({"price": p, "rating": rating, "launch_date": as_of - pd.Timedelta(days=age), "category": category})
        if reviews is not None:
            f["reviews"] = reviews
        return f

    units = predict_draws(card, frame(price), sims, seed, offset, as_of)[:, 0]
    rev = units * price
    econ = _economics(idea, seg_l, (summary or {}).get("unit_cost") or {})
    out: dict = {"status": "ok", "market": market, "as_of": card.get("as_of")}
    unit_margin = None
    if econ["unit_cost"] is not None:
        unit_margin = price * (1 - econ["referral_fee"]) - econ["fulfilment_fee"] - econ["unit_cost"]
    econ["unit_margin"] = unit_margin
    econ["margin_rate"] = None if unit_margin is None else unit_margin / price
    ue = config()["unit_economics"]
    if unit_margin is not None:
        profit = units * unit_margin - econ["fixed_monthly_cost"]
        be = econ["fixed_monthly_cost"] / unit_margin if unit_margin > 0 else None
        mp = float(profit.mean())
        out["profit"] = {**_q(profit, lvl), "p_positive": float((profit > 0).mean()),
                         "target": ue["target_monthly_profit"], "p_target": float((profit >= ue["target_monthly_profit"]).mean())}
        out["break_even_units"] = be
        out["p_break_even"] = None if be is None else float((units >= be).mean())
        out["payback_months"] = (econ["launch_cost"] / mp) if econ["launch_cost"] and mp > 0 else None
    out["units"] = _q(units, lvl)
    out["revenue"] = {**_q(rev, lvl), "target": ue["target_monthly_revenue"], "p_target": float((rev >= ue["target_monthly_revenue"]).mean())}
    out["economics"] = econ
    out["assumptions"] = {"price": price, "rating": rating, "rating_basis": rating_basis, "rating_assumed": not rating_given,
                          "reviews": reviews, "reviews_basis": reviews_basis, "age_days": age,
                          "category": category, "segment_offset": round(offset, 4), "segment_offset_n": n_off,
                          "model_family": card.get("family"), "model_listings": card.get("n"), "simulations": sims}

    # price curve on common random numbers
    if len(prices) >= 3:
        lo, hi = np.quantile(prices, [cfg["price_grid_quantiles"][0], cfg["price_grid_quantiles"][1]])
        grid = np.unique(np.round(np.geomspace(max(lo, 0.01), max(hi, lo * 1.01), cfg["price_grid_points"]), 2))
        if price not in grid:
            grid = np.sort(np.append(grid, round(price, 2)))
        U = predict_draws(card, frame(grid), sims, seed, offset, as_of, shared_noise=True)
        curve = []
        for j, p in enumerate(grid):
            row = {"price": float(p), "units": float(U[:, j].mean()), "revenue": float((U[:, j] * p).mean()),
                   "revenue_lo": float(np.quantile(U[:, j] * p, 0.1)), "revenue_hi": float(np.quantile(U[:, j] * p, 0.9))}
            if econ["unit_cost"] is not None:
                m = p * (1 - econ["referral_fee"]) - econ["fulfilment_fee"] - econ["unit_cost"]
                pr = U[:, j] * m - econ["fixed_monthly_cost"]
                row.update(profit=float(pr.mean()), profit_lo=float(np.quantile(pr, 0.1)), profit_hi=float(np.quantile(pr, 0.9)),
                           p_positive=float((pr > 0).mean()))
            curve.append(row)
        key = "profit" if econ["unit_cost"] is not None else "revenue"
        best = max(curve, key=lambda r: r[key])
        out["price_curve"] = {"points": curve, "optimise": key, "best_price": best["price"],
                              "at_edge": best["price"] in (curve[0]["price"], curve[-1]["price"]),
                              "caveat": "price effect is the cross-sectional association in this market, not a causal elasticity"}

    # empirical check: what this segment's actual entrants sell
    ent = seg_l[seg_l["is_entrant"].fillna(False).astype(bool)] if "is_entrant" in seg_l else seg_l.iloc[0:0]
    eu = pd.to_numeric(ent["units_est"], errors="coerce").dropna()
    out["entrants_actual"] = ({"n": int(len(eu)), "median": float(eu.median()), "p25": float(eu.quantile(0.25)),
                               "p75": float(eu.quantile(0.75))} if len(eu) else {"n": 0})

    # gaps the idea covers / misses
    g = data["gaps"]
    feats = _features(text, price, seg_l)
    gap_rows = []
    if len(g):
        gs = g[(g["segment_id"] == sid) & g["is_gap"].astype(bool)]
        for _, r in gs.iterrows():
            names = {r["feature"], *(json.loads(r["aliases"]) if isinstance(r["aliases"], str) else list(r["aliases"] or []))}
            gap_rows.append({"feature": r["feature"], "kind": r["kind"], "lift": _num(r["lift"]), "lift_lo": _num(r["lift_lo"]),
                             "lift_hi": _num(r["lift_hi"]), "q_value": _num(r["q_value"]), "covered": bool(names & feats)})
    out["gaps"] = gap_rows
    out["features"] = sorted(feats)

    # risks
    rc = cfg["risks"]
    risks = []
    hhi = _num(seg.get("hhi_est"))
    if hhi is not None and hhi >= rc["hhi_high"]:
        risks.append({"code": "concentrated", "severity": "high", "value": hhi, "brand": seg.get("top_brand_est")})
    esr = _num(seg.get("entrant_success_rate"))
    if esr is not None and esr < rc["entrant_success_low"]:
        risks.append({"code": "entrants_struggle", "severity": "high" if esr < rc["entrant_success_low"] / 2 else "medium", "value": esr})
    if bar is not None and rating is not None and rating < bar:
        # a rating the user gives is a plan: falling short of the bar is a risk. An assumed rating is not a plan --
        # it is reported as a low-severity reminder to enter the expected rating, not as a finding.
        risks.append({"code": "below_rating_bar", "severity": "medium" if rating_given else "low", "value": rating, "bar": bar,
                      "assumed": not rating_given})
    top_sim = float(pl["similarity"].max()) if len(pl["similarity"]) else 0.0
    if top_sim >= rc["similarity_high"]:
        risks.append({"code": "low_differentiation", "severity": "medium", "value": top_sim})
    if top_sim < rc["similarity_low"]:
        risks.append({"code": "poor_placement", "severity": "high", "value": top_sim})
    if len(prices) >= 3 and (price < prices.quantile(0.025) or price > prices.quantile(0.975)):
        risks.append({"code": "price_extrapolation", "severity": "medium", "value": price,
                      "range": [float(prices.min()), float(prices.max())]})
    if unit_margin is not None and unit_margin <= 0:
        risks.append({"code": "negative_margin", "severity": "high", "value": unit_margin})
    grade = (summary or {}).get("evidence_grade")
    if grade in ("C", "D"):
        risks.append({"code": "weak_evidence", "severity": "medium" if grade == "C" else "high", "value": grade})
    if len(eu) >= 3 and not (eu.quantile(0.1) <= out["units"]["median"] <= eu.quantile(0.9)):
        risks.append({"code": "model_vs_entrants", "severity": "medium", "value": out["units"]["median"],
                      "range": [float(eu.quantile(0.1)), float(eu.quantile(0.9))]})
    risks.sort(key=lambda r: {"high": 0, "medium": 1, "low": 2}.get(r["severity"], 3))
    out["risks"] = risks

    # placement + comparables + segment context
    votes = pl["votes"]
    labels = S.set_index("segment_id")["segment_label"].to_dict() if len(S) else {}
    out["placement"] = {"segment_id": sid, "segment_label": labels.get(sid, sid), "family": seg.get("family_label"),
                        "user_chosen": pl["user_chosen"], "vote_share": _num(votes.get(sid)) if len(votes) else None,
                        "top_similarity": top_sim,
                        "alternatives": [{"segment_id": k, "segment_label": labels.get(k, k), "vote_share": float(v)}
                                         for k, v in votes.head(4).items()]}
    near = seg_l.assign(similarity=pl["similarity"][seg_l.index.to_numpy()] if len(seg_l) else [])
    near = near.sort_values("similarity", ascending=False).head(cfg["comparables"])
    out["comparables"] = near[["id", "title", "brand", "price", "rating", "units_est", "units_lo", "units_hi", "revenue_est",
                               "is_entrant", "similarity"]].to_dict("records")
    out["segment"] = {k: (None if pd.isna(seg.get(k)) else seg.get(k)) if not isinstance(seg.get(k), (list, np.ndarray)) else list(seg.get(k))
                      for k in ("segment_label", "opportunity_index", "opportunity_level", "revenue_est", "revenue_lo", "revenue_hi",
                                "hhi_est", "top_brand_est", "entrant_success_rate", "rating_bar", "margin_rate_median",
                                "entrant_units_expected", "segment_description", "segment_description_zh")}
    out["segment"]["price_p10"] = float(prices.quantile(0.1)) if len(prices) else None
    out["segment"]["price_p90"] = float(prices.quantile(0.9)) if len(prices) else None
    rec = data["recs"]
    if len(rec) and sid in set(rec["segment_id"]):
        r = rec.set_index("segment_id").loc[sid]
        f = r["features"]
        out["recommendation"] = {"features": json.loads(f) if isinstance(f, str) else list(f), "price_range": r["price_range"],
                                 "expected_units": _num(r["expected_units"])}
    out["_units_draws"] = units                            # for scenario comparison; stripped by the API
    return out


def compare(base: dict, scenarios: list[dict], data: dict, market: str, summary: dict | None) -> dict:
    """Each scenario on the same segment (the first one's placement unless given) and the same random numbers."""
    first = simulate({**base, **scenarios[0]}, data, market, summary)
    sid = first["placement"]["segment_id"]
    runs = [first] + [simulate({**base, **s}, data, market, summary, segment_id=sid) for s in scenarios[1:]]
    rows = []
    has_profit = all("profit" in r for r in runs)
    key = "profit" if has_profit else "revenue"
    draws = []
    for s, r in zip(scenarios, runs):
        u = r["_units_draws"]
        price = r["assumptions"]["price"]
        if has_profit:
            d = u * r["economics"]["unit_margin"] - r["economics"]["fixed_monthly_cost"]
        else:
            d = u * price
        draws.append(d)
        rows.append({"name": s.get("name") or f"{price:g}", "price": price, "units": r["units"], "revenue": r["revenue"],
                     "profit": r.get("profit"), "unit_margin": r["economics"]["unit_margin"],
                     "gaps_covered": sum(g["covered"] for g in r["gaps"]), "gaps_total": len(r["gaps"]),
                     "risks": [x["code"] for x in r["risks"]], "break_even_units": r.get("break_even_units")})
    D = np.column_stack(draws)
    p_best = np.bincount(D.argmax(axis=1), minlength=D.shape[1]) / len(D)
    for i, row in enumerate(rows):
        row["p_best"] = float(p_best[i])
        row["p_beats_first"] = None if i == 0 else float((D[:, i] > D[:, 0]).mean())
    return {"status": "ok", "market": market, "segment_id": sid, "segment_label": first["placement"]["segment_label"],
            "compare_on": key, "scenarios": rows,
            "note": "same segment and same random numbers for every scenario" + ("" if has_profit else
                                                                                 "; profit needs a unit cost -- compared on revenue")}


def strip(out: dict) -> dict:
    return {k: v for k, v in out.items() if not k.startswith("_")}


"""Shopping mode on metrics v3 (Phase 6): multi-criteria choice with a Pareto front.

1. **Candidates** -- relevance of each product title to the need (word + character TF-IDF, max), gated at
   ``relevance_floor`` x the best match; budget and minimum rating are hard filters. Words the buyer typed
   that distinguish candidates are requirements: ``need_coverage`` = share of them present.
2. **Criteria** (all computed, none scored by hand):
   * match   -- need coverage, then relevance
   * quality -- Bayesian-average rating (review-weighted when the source has counts), so a 5.0 with three
                reviews does not beat a 4.7 with three thousand
   * proof   -- the *lower* 95% bound of estimated monthly units: demand we are confident exists
   * price   -- lower is better
3. **Pareto front** -- a product is dominated when another is at least as good on every criterion and
   strictly better on one; each dominated product names one product that dominates it. Non-dominated
   sorting gives every product a front number.
4. **Utility** -- weighted mean of criteria percentiles among the candidates, weights from the buyer
   profile (config presets, overridable). Ranking: front first, then utility -- a dominated product never
   outranks the product that dominates it.
5. **Explanations** -- strengths (top quartile among candidates) and trade-offs (bottom quartile) with the
   numbers behind them.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from dip.metrics import config
from dip.metrics.brands import bayes_rating

CRITERIA = ["match", "quality", "proof", "price"]


def relevance(titles: pd.Series, need: str) -> np.ndarray:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    text = titles.fillna("").str.lower().tolist()
    q = need.lower()
    if not q.strip() or not text:
        return np.ones(len(text))
    w = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True).fit(text + [q])
    rel = cosine_similarity(w.transform([q]), w.transform(text)).ravel()
    c = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit(text + [q])
    return np.maximum(rel, 0.8 * cosine_similarity(c.transform([q]), c.transform(text)).ravel())


def need_words(need: str, titles: pd.Series) -> tuple[list[str], np.ndarray]:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    stop = set(ENGLISH_STOP_WORDS) | {"need", "want", "looking", "buy", "good", "best", "cheap", "under", "over"}
    words = sorted({w for w in re.findall(r"[a-z][a-z0-9\-]+", need.lower()) if len(w) > 2 and w not in stop})
    t = titles.fillna("").str.lower()
    has = {w: t.str.contains(rf"\b{re.escape(w)}\b", regex=True).to_numpy() for w in words}
    distinct = [w for w, m in has.items() if 0 < m.sum() < len(t)]
    if not distinct:
        return [], np.ones(len(t))
    return distinct, sum(has[w].astype(float) for w in distinct) / len(distinct)


def pareto_fronts(M: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """M: rows x criteria, higher is better. Returns (front number from 1, index of one dominator or -1)."""
    n = len(M)
    ge = (M[:, None, :] >= M[None, :, :]).all(axis=2)
    gt = (M[:, None, :] > M[None, :, :]).any(axis=2)
    dom = ge & gt                                                       # dom[i, j]: i dominates j
    np.fill_diagonal(dom, False)
    front = np.zeros(n, dtype=int)
    remaining = np.ones(n, dtype=bool)
    k = 0
    while remaining.any():
        k += 1
        sub = np.flatnonzero(remaining)
        dominated = dom[np.ix_(sub, sub)].any(axis=0)
        cur = np.zeros(n, dtype=bool)
        cur[sub[~dominated]] = True
        front[cur] = k
        remaining &= ~cur
    dominator = np.full(n, -1)
    for j in range(n):
        d = np.flatnonzero(dom[:, j])
        if len(d):
            dominator[j] = d[np.argmin(front[d])]
    return front, dominator


def recommend(products: pd.DataFrame, need: str, budget_max: float | None = None, budget_min: float | None = None,
              min_rating: float | None = None, profile: str | None = None, weights: dict | None = None,
              limit: int = 12, requirements: list[dict] | None = None) -> dict:
    """``requirements``: structured requirements read from the need (dip.knowledge.needs); a product whose
    stated attributes contradict one is filtered out, one that does not state it is kept (unknown)."""
    cfg = config()["shopping"]
    P = products.reset_index(drop=True).copy()
    if P.empty:
        return {"status": "no_products", "results": [], "candidates": 0}
    P["price"] = pd.to_numeric(P["price"], errors="coerce")
    rel = relevance(P["title"], need)
    P["relevance"] = rel
    mask = pd.Series(True, index=P.index)
    if need.strip():
        mask &= P["relevance"] >= max(cfg["relevance_min"], cfg["relevance_floor"] * float(rel.max()))
    filters = {"relevance": int((~mask).sum())}
    for name, cond in (("budget_max", None if budget_max is None else P["price"] <= budget_max),
                       ("budget_min", None if budget_min is None else P["price"] >= budget_min),
                       ("min_rating", None if min_rating is None else pd.to_numeric(P["rating"], errors="coerce") >= min_rating)):
        if cond is not None:
            filters[name] = int((mask & ~cond.fillna(False)).sum())
            mask &= cond.fillna(False)
    mask &= P["price"].notna()
    C = P[mask].reset_index(drop=True)
    if C.empty:
        return {"status": "no_match", "results": [], "candidates": 0, "filtered_out": filters}
    if requirements:
        from dip.knowledge import needs
        ev = needs.evaluate(C, requirements)
        failed = ev["requirements_failed"] > 0
        filters["requirements"] = int(failed.sum())
        C = pd.concat([C, ev], axis=1)[~failed.to_numpy()].reset_index(drop=True)
        if C.empty:
            return {"status": "no_match", "results": [], "candidates": 0, "filtered_out": filters}
    words, cover = need_words(need, C["title"])
    C["need_coverage"] = cover
    if requirements:                    # stated attributes that meet the requirements count with the buyer's words
        share = pd.to_numeric(C["requirement_share"], errors="coerce")
        C["need_coverage"] = np.where(share.notna(), (C["need_coverage"] + share.fillna(0)) / 2, C["need_coverage"])
    w, m, c, basis = bayes_rating(C.get("rating", pd.Series(np.nan, index=C.index)), C.get("reviews"))
    r = pd.to_numeric(C.get("rating"), errors="coerce")
    C["rating_bayes"] = np.where(w > 0, (c * m + r.fillna(0) * w) / (c + w), m if np.isfinite(m) else np.nan)
    lo = pd.to_numeric(C.get("units_lo"), errors="coerce")
    C["proof_units"] = lo.fillna(0.0) if lo.notna().any() else pd.to_numeric(C.get("monthly_sales"), errors="coerce").fillna(0.0)

    crit = pd.DataFrame({"match": C["need_coverage"] + 1e-3 * C["relevance"],   # coverage first, relevance breaks ties
                         "quality": C["rating_bayes"].fillna(m if np.isfinite(m) else 0.0),
                         "proof": C["proof_units"], "price": -C["price"]})
    front, dominator = pareto_fronts(crit.to_numpy(dtype=float))
    pctl = crit.rank(pct=True, method="average")
    wt = {**cfg["profiles"].get(profile or "default", cfg["profiles"]["default"]), **(weights or {})}
    tot = sum(max(float(wt.get(k, 0)), 0) for k in CRITERIA) or 1.0
    C["utility"] = sum(max(float(wt.get(k, 0)), 0) * pctl[k] for k in CRITERIA) / tot * 100
    C["front"] = front
    C["dominated_by"] = [None if d < 0 else C.at[d, "product_id"] for d in dominator]
    C["dominated_by_title"] = [None if d < 0 else C.at[d, "title"] for d in dominator]
    for k in CRITERIA:
        C[f"pct_{k}"] = pctl[k].round(3)
    C = C.sort_values(["front", "utility"], ascending=[True, False]).reset_index(drop=True)

    def explain(row) -> list[dict]:
        out = []
        for k in CRITERIA:
            p = row[f"pct_{k}"]
            kind = "strength" if p >= 0.75 else "tradeoff" if p <= 0.25 and len(C) >= 4 else None
            if kind:
                val = {"match": row["need_coverage"], "quality": row["rating_bayes"], "proof": row["proof_units"], "price": row["price"]}[k]
                out.append({"criterion": k, "kind": kind, "value": None if pd.isna(val) else float(val), "percentile": float(p)})
        return out

    C["explain"] = [explain(r) for _, r in C.iterrows()]
    keep = [x for x in ["product_id", "title", "brand", "image", "market", "segment_id", "price", "rating", "reviews", "rating_bayes",
                        "units_est", "units_lo", "units_hi", "monthly_sales", "relevance", "need_coverage", "proof_units", "utility",
                        "front", "dominated_by", "dominated_by_title", "pct_match", "pct_quality", "pct_proof", "pct_price", "explain",
                        "requirement_share", "requirement_status"]
            if x in C]
    frontier = C[C["front"] == 1]
    return {"status": "ok", "candidates": int(len(C)), "filtered_out": filters, "need_words": words,
            "weights": {k: float(wt.get(k, 0)) for k in CRITERIA}, "profile": profile or "default",
            "rating_prior": {"mean": None if not np.isfinite(m) else round(m, 3), "strength": round(c, 1), "basis": basis},
            "pareto_size": int(len(frontier)), "results": C[keep].head(limit).to_dict("records"),
            "map": C[["product_id", "title", "price", "rating_bayes", "proof_units", "front", "utility"]].head(cfg["map_points"]).to_dict("records")}

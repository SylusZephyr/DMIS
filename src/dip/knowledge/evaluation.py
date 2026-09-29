"""Accuracy of the knowledge layer (spec 130-134) and stability of the opportunity ranking.

1. **Dental Confidence vs labels.** Two label sources, reported separately and never mixed:
   * the gold benchmark ``data/validated/<market>/gold_labels.csv`` (labelled by a model and marked as such --
     a benchmark, not human ground truth). Its relevance reasons map to "dental or not": EXACT_MATCH,
     WRONG_CATEGORY (a dental product of another category) and ACCESSORY_ONLY are dental; UNRELATED is not;
     AMBIGUOUS / UNCERTAIN rows are left out;
   * people's ``dental`` labels from the labelling workflow.
   A listing is predicted dental when its band is strong / probable / verified_dental, predicted non-dental
   when probably_non_dental / non_dental / verified_non_dental; "review" is neither and is counted apart
   (routing to review is not an error, it is the honest answer for unclear rows). Reported: recall of dental,
   precision of dental and of non-dental where there are negatives, the review share, and n. Fewer than
   ``min_labels`` rows: counts only, no rate.
2. **Ranking stability.** Each weight of the opportunity score is multiplied by a random factor in
   [1 - spread, 1 + spread] (``permutations`` draws, fixed seed) and the ranked scopes are re-scored from their
   stored dimension scores. Reported per scope: how often it stays in the top 3, its rank range, and overall the
   median Spearman correlation with the published ranking. Settings: ``evaluation`` in knowledge.yaml.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from dip.knowledge import config

DENTAL = {"strong", "probable", "verified_dental"}
NON_DENTAL = {"probably_non_dental", "non_dental", "verified_non_dental"}
GOLD_DENTAL = {"EXACT_MATCH": True, "WRONG_CATEGORY": True, "ACCESSORY_ONLY": True, "UNRELATED": False}


def _cfg() -> dict:
    return config()["evaluation"]


def listing_key(asin: str, marketplace: str = "US") -> str:
    """The v1 listing id of the gold files: sha1('<marketplace>|<asin>')."""
    return hashlib.sha1(f"{marketplace}|{asin}".encode("utf-8")).hexdigest()


def confusion(truth: pd.Series, band: pd.Series) -> dict:
    """truth: bool (dental); band: dental band per row (same index)."""
    n = int(len(truth))
    pred_d = band.isin(DENTAL)
    pred_n = band.isin(NON_DENTAL)
    review = ~(pred_d | pred_n)
    tp, fn_ = int((truth & pred_d).sum()), int((truth & pred_n).sum())
    tn, fp = int((~truth & pred_n).sum()), int((~truth & pred_d).sum())
    out = {"n": n, "dental": int(truth.sum()), "non_dental": int((~truth).sum()), "review": int(review.sum()),
           "true_dental": tp, "missed_dental": fn_, "true_non_dental": tn, "false_dental": fp}
    enough = n >= int(_cfg()["min_labels"])
    decided_d = int(truth.sum()) - int((truth & review).sum())
    out["dental_recall"] = round(tp / decided_d, 3) if enough and decided_d else None     # among decided rows
    out["dental_precision"] = round(tp / (tp + fp), 3) if enough and (tp + fp) and int((~truth).sum()) else None
    out["non_dental_precision"] = round(tn / (tn + fn_), 3) if enough and (tn + fn_) else None
    out["review_share"] = round(float(review.mean()), 3) if n else None
    return out


def gold_benchmark(market: str, records: pd.DataFrame) -> dict | None:
    from dip.settings import PROJECT_ROOT

    path = PROJECT_ROOT / "data" / "validated" / market / "gold_labels.csv"
    if not path.exists() or "dental_band" not in records:
        return None
    g = pd.read_csv(path)
    g["truth"] = g["relevance_reason"].map(GOLD_DENTAL)
    g = g.dropna(subset=["truth"])
    keys = records["id"].astype(str).map(listing_key)
    band = dict(zip(keys, records["dental_band"]))
    g = g[g["listing_id"].isin(band)]
    if not len(g):
        return {"source": str(path.relative_to(PROJECT_ROOT)), "matched": 0}
    res = confusion(g["truth"].astype(bool).reset_index(drop=True), g["listing_id"].map(band).reset_index(drop=True))
    labellers = sorted(set(g["reviewer"].dropna().astype(str))) if "reviewer" in g else []
    return {"source": str(path.relative_to(PROJECT_ROOT)), "matched": int(len(g)), "labelled_by": labellers,
            "note": "benchmark labelled by a model, not human ground truth", **res}


def human_dental(market: str) -> dict | None:
    """People's 'dental' labels from the labelling workflow (latest label per item)."""
    from dip.storage import business as b

    with b.session() as s:
        rows = (s.query(b.Label, b.LabelItem).join(b.LabelItem, b.Label.item_id == b.LabelItem.id)
                .join(b.LabelSample, b.LabelItem.sample_id == b.LabelSample.id)
                .filter(b.LabelSample.market_name == market, b.Label.check == "dental").order_by(b.Label.at).all())
    latest: dict = {}
    for lab, it in rows:
        latest[it.id] = (bool((lab.value or {}).get("is_dental")), (it.snapshot or {}).get("dental_band"))
    if not latest:
        return None
    t = pd.Series([v[0] for v in latest.values()])
    bnd = pd.Series([v[1] for v in latest.values()])
    return {"source": "labelling workflow", **confusion(t, bnd)}


def ranking_stability(opps: pd.DataFrame, weights: dict) -> dict:
    """Re-score ranked scopes under random weight perturbations; how stable is the ranking?"""
    c = _cfg()
    dims = [d for d in weights]
    r = opps[opps["opportunity_score"].notna()].reset_index(drop=True)
    if len(r) < 3:
        return {"status": "too_few_ranked", "ranked": int(len(r))}
    D = np.column_stack([pd.to_numeric(r.get(f"dim_{d}"), errors="coerce").to_numpy(dtype=float) for d in dims])
    W = np.array([float(weights[d]) for d in dims])
    mask = ~np.isnan(D)

    def score(w):
        num = np.nansum(np.where(mask, D * w, 0.0), axis=1)
        den = np.where(mask, w, 0.0).sum(axis=1)
        return num / np.where(den > 0, den, np.nan)

    base = score(W)
    base_rank = pd.Series(base).rank(ascending=False, method="min").to_numpy()
    rng = np.random.default_rng(int(c["seed"]))
    spread = float(c["weight_spread"])
    top3 = np.zeros(len(r))
    ranks = []
    rhos = []
    for _ in range(int(c["permutations"])):
        s = score(W * rng.uniform(1 - spread, 1 + spread, size=len(W)))
        rk = pd.Series(s).rank(ascending=False, method="min").to_numpy()
        ranks.append(rk)
        top3 += rk <= 3
        rhos.append(pd.Series(base_rank).corr(pd.Series(rk), method="spearman"))
    R = np.vstack(ranks)
    scopes = [{"scope": r.at[i, "scope"], "scope_id": r.at[i, "scope_id"], "label": r.at[i, "label"],
               "rank": int(base_rank[i]), "rank_min": int(R[:, i].min()), "rank_max": int(R[:, i].max()),
               "top3_share": round(float(top3[i] / len(R)), 3)} for i in np.argsort(base_rank)]
    return {"status": "ok", "ranked": int(len(r)), "permutations": int(c["permutations"]), "weight_spread": spread,
            "median_spearman": round(float(np.nanmedian(rhos)), 3), "scopes": scopes}

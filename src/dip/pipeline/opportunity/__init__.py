"""Stage 8 -- Opportunity engine (v2 formula).

    Opportunity = Demand + Growth + Customer pain + Competition gap + Supplier availability - Market difficulty

Each term is 0..1 and comes from computed evidence:

* demand            percentile of observed segment revenue (0 when unobserved)
* growth            forecast growth -> sigmoid; launch-cohort proxy if no forecast
* customer_pain     review pain score, else rating deficit
* competition_gap   1 - listing-crowdedness rank
* supplier          matched-supplier depth (saturating); absent until suppliers exist
* difficulty       mean(review moat rank, top-brand share)  [subtracted]

The positive terms are weight-averaged over the ones with data; difficulty
is subtracted; the result is rescaled to 0-100 and shrunk toward 50 in
proportion to missing evidence (``coverage``). Unverified demand is capped,
exactly as in v1. v1's own score (``dmie.engine.opportunity``) is kept
alongside as ``v1_score`` for comparison.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dmie.engine.opportunity import score_products, score_segments

WEIGHTS = {"demand": 0.25, "growth": 0.15, "customer_pain": 0.15, "competition_gap": 0.20, "supplier": 0.10}
DIFFICULTY_WEIGHT = 0.15
UNVERIFIED_CAP = 45.0
POSITIVE = list(WEIGHTS)


def score(segments: pd.DataFrame, growth: dict | None = None, pain: dict | None = None,
          momentum: float | None = None, supplier_matches: pd.DataFrame | None = None) -> pd.DataFrame:
    v1 = score_segments(segments, growth, pain, momentum)
    s = v1.rename(columns={"opportunity_score": "v1_score"}).copy()
    if s.empty:
        s["opportunity_score"] = pd.Series(dtype=float)
        return s
    f = pd.DataFrame(index=s.index)
    f["demand"] = s["market_size"]
    f["growth"] = s["demand_growth"]
    f["customer_pain"] = s["review_problems"]
    f["competition_gap"] = s["competition"]
    if supplier_matches is not None and len(supplier_matches):
        depth = supplier_matches[supplier_matches["match_score"] >= 0.15].groupby("segment_id").size()
        f["supplier"] = s["segment_id"].map(depth).fillna(0).map(lambda n: 1 - np.exp(-n / 2))
    else:
        f["supplier"] = np.nan
    difficulty = 1 - s["entry_difficulty"].astype(float)  # v1 stores ease; difficulty = 1 - ease

    W = np.array([WEIGHTS[k] for k in POSITIVE])
    F = f[POSITIVE].to_numpy(dtype=float)
    avail = ~np.isnan(F)
    wsum = (avail * W).sum(axis=1)
    pos = np.where(wsum > 0, np.nansum(F * W, axis=1) / np.where(wsum > 0, wsum, 1), np.nan)
    d = difficulty.fillna(0.5).to_numpy()
    raw = (pos - DIFFICULTY_WEIGHT * d + DIFFICULTY_WEIGHT) / (1 + DIFFICULTY_WEIGHT)
    coverage = (wsum + DIFFICULTY_WEIGHT * difficulty.notna().to_numpy()) / (W.sum() + DIFFICULTY_WEIGHT)
    out = 0.5 + (raw - 0.5) * coverage
    verified = s["monthly_revenue"].notna().to_numpy()
    s["opportunity_score"] = np.round(np.where(verified, out * 100, np.minimum(out * 100, UNVERIFIED_CAP)), 1)
    s["coverage"] = np.round(coverage, 3)
    for k in POSITIVE:
        s[f"f_{k}"] = f[k].round(3)
    s["f_difficulty"] = difficulty.round(3)
    s["opportunity_level"] = pd.cut(s["opportunity_score"], [-1, 40, 55, 70, 101], labels=["Low", "Moderate", "High", "Very high"]).astype(str)
    return s.sort_values("opportunity_score", ascending=False).reset_index(drop=True)


def score_product_level(products: pd.DataFrame, segment_scores: pd.DataFrame) -> pd.DataFrame:
    return score_products(products, segment_scores)

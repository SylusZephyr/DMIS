"""Opportunity Score Engine (Module 8).

Mathematical 0-100 score per product segment (and per product), from
seven factors, each normalised to 0..1 where 1 = more attractive:

    market_size        percentile rank of segment revenue (log)
    demand_growth      forecast growth (sigmoid); launch-cohort proxy if no forecast
    competition        1 - listing-count rank (crowdedness)
    price_opportunity  price dispersion (IQR / median) -> room to position
    review_problems    rating deficit and review pain -> room to out-build incumbents
    margin_opportunity percentile rank of median price (higher price, more margin room)
    entry_difficulty   ease of entry: 1 - mean(review-moat rank, top-brand share [segments >= 4 products])

Weights live in config/engine/engine.yaml. A factor with no data is left
out, the rest re-normalised, and the result shrunk toward 50 in
proportion to missing weight (``coverage``), so a score built on thin
evidence is visibly thin. Market size is the exception: unobserved
revenue scores 0 ("demand unverified"), because an unproven market must
not outrank a proven one; such segments are also capped at
``unverified_demand_cap``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dmie.engine.config import section

FACTORS = ["market_size", "demand_growth", "competition", "price_opportunity",
           "review_problems", "margin_opportunity", "entry_difficulty"]


def _rank(s: pd.Series) -> pd.Series:
    if s.notna().sum() <= 1:
        return pd.Series(np.where(s.notna(), 0.5, np.nan), index=s.index)
    return s.rank(pct=True, method="average")


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def score_segments(segments: pd.DataFrame, growth: dict[str, float] | None = None,
                   pain: dict[str, float] | None = None, cohort_momentum: float | None = None) -> pd.DataFrame:
    """Add factor columns + opportunity_score + coverage to a segment table."""
    weights = section("opportunity").get("weights", {})
    growth = growth or {}
    pain = pain or {}
    s = segments.copy()
    if s.empty:
        for c in FACTORS + ["opportunity_score", "coverage", "opportunity_drivers"]:
            s[c] = pd.Series(dtype="float64")
        return s
    f = pd.DataFrame(index=s.index)
    # Demonstrated market size: a segment with no observed revenue has not
    # demonstrated demand -- scored 0, never dropped (dropping it would let
    # unproven segments outrank proven ones).
    f["market_size"] = _rank(np.log1p(s["monthly_revenue"].astype(float))).fillna(0.0)
    g = s["segment_id"].map(lambda x: growth.get(str(x)))
    g = pd.to_numeric(g, errors="coerce")
    if g.notna().any():
        f["demand_growth"] = _sigmoid(2.5 * g.clip(-1, 2))
    elif cohort_momentum is not None:
        f["demand_growth"] = float(np.clip(0.3 + cohort_momentum, 0, 1))
    else:
        f["demand_growth"] = np.nan
    top_share = s["top_brand_share"].astype(float).where(s["products"] >= 4)  # share is meaningless for tiny segments
    f["competition"] = 1 - _rank(s["listings"].astype(float)).fillna(0.5)
    disp = (s["price_iqr"] / s["price_median"]).astype(float)
    f["price_opportunity"] = disp.clip(0, 1)
    rating_gap = ((4.7 - s["avg_rating"].astype(float)) / 1.2).clip(0, 1)
    pain_s = s["segment_id"].map(lambda x: pain.get(str(x)))
    pain_s = pd.to_numeric(pain_s, errors="coerce")
    f["review_problems"] = np.where(pain_s.notna(), 0.5 * rating_gap.fillna(pain_s) + 0.5 * pain_s, rating_gap)
    f["margin_opportunity"] = _rank(s["price_median"].astype(float))
    moat = _rank(np.log1p(s["median_reviews"].astype(float))) if s["median_reviews"].notna().any() else pd.Series(np.nan, index=s.index)
    ease = 1 - pd.concat([moat, top_share], axis=1).mean(axis=1, skipna=True)
    f["entry_difficulty"] = ease

    W = np.array([weights.get(k, 0) for k in FACTORS], dtype=float)
    F = f[FACTORS].to_numpy(dtype=float)
    avail = ~np.isnan(F)
    wsum = (avail * W).sum(axis=1)
    raw = np.where(wsum > 0, np.nansum(F * W, axis=1) / np.where(wsum > 0, wsum, 1), np.nan)
    coverage = wsum / W.sum()
    score = 0.5 + (raw - 0.5) * coverage  # thin evidence shrinks toward neutral
    for k in FACTORS:
        s[k] = f[k].round(3)
    cap = section("opportunity").get("unverified_demand_cap", 45)
    verified = s["monthly_revenue"].notna().to_numpy()
    s["opportunity_score"] = np.round(np.where(verified, score * 100, np.minimum(score * 100, cap)), 1)
    s["coverage"] = np.round(coverage, 3)
    s["demand_verified"] = s["monthly_revenue"].notna()
    s["opportunity_drivers"] = [_drivers(f.loc[i]) + ("" if ok else "; demand unverified (no sales data)")
                                for i, ok in zip(s.index, s["demand_verified"])]
    return s.sort_values("opportunity_score", ascending=False).reset_index(drop=True)


def _drivers(row: pd.Series) -> str:
    vals = row.dropna().sort_values(ascending=False)
    top = [f"{k.replace('_', ' ')} {v:.2f}" for k, v in vals.head(2).items()]
    weak = [f"{k.replace('_', ' ')} {v:.2f}" for k, v in vals.tail(1).items()] if len(vals) > 2 else []
    return "strong: " + ", ".join(top) + ("; weak: " + ", ".join(weak) if weak else "")


def score_products(products: pd.DataFrame, segment_scores: pd.DataFrame) -> pd.DataFrame:
    """Product score = 70% its segment's score + 30% product-level factors
    (own rating deficit = improvement room; own demand rank within segment)."""
    p = products.copy()
    if p.empty:
        p["opportunity_score"] = pd.Series(dtype="float64")
        return p
    seg = segment_scores.set_index("segment_id")["opportunity_score"] if len(segment_scores) else pd.Series(dtype=float)
    base = p["segment_id"].map(seg).astype(float)
    rating_gap = ((4.7 - p["rating"].astype(float)) / 1.2).clip(0, 1)
    demand = p.groupby("segment_id")["monthly_sales"].rank(pct=True)
    own = pd.concat([rating_gap, demand], axis=1).mean(axis=1, skipna=True)
    score = np.where(own.notna(), 0.7 * base + 30 * own, base)
    cap = section("opportunity").get("unverified_demand_cap", 45)
    p["opportunity_score"] = np.round(np.where(p["monthly_sales"].notna(), score, np.minimum(score, cap)), 1)
    return p

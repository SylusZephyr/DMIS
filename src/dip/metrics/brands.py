"""Competitor (brand) intelligence on metrics v3 (Phase 6).

Everything here is derived from the joint demand simulation, so every share carries an interval:

* brand table   -- estimated revenue / share with intervals, rank interval and P(rank 1), price index
                   against each listing's segment median, a Bayesian-average rating, entrants, and a
                   position read from the share interval against the market's equal-share benchmark
                   (1 / effective competitors).
* launch cohorts -- listings grouped by launch quarter with the revenue they earn today. Only listings that
                   are still listed are visible, so older cohorts are survivors (stated in the output).
* momentum      -- launches in the last window vs the window before, tested with the exact conditional
                   binomial test (equal Poisson rates), Benjamini-Hochberg over segments.
* changes       -- brand shares of two snapshots compared with a z-test on the interval-derived standard
                   errors, Benjamini-Hochberg over brands. Needs >= 2 uploads.

Thresholds live in config/platform/metrics.yaml (``brands``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from dip.metrics import config
from dip.metrics.gaps import _bh

BRAND_COLUMNS = ["brand", "listings", "products", "segments", "top_segment", "units_est", "revenue_est", "revenue_lo",
                 "revenue_hi", "share_est", "share_lo", "share_hi", "rank", "rank_lo", "rank_hi", "p_top", "median_price",
                 "price_index", "rating_bayes", "rating_gap", "reviews", "entrants", "entrant_revenue_share",
                 "median_age_days", "position", "signals"]


def _ci(d: np.ndarray, lvl: float, axis=0):
    a = (1 - lvl) / 2
    return np.quantile(d, a, axis=axis), np.quantile(d, 1 - a, axis=axis)


def bayes_rating(rating: pd.Series, reviews: pd.Series | None) -> tuple[pd.Series, float, float, str]:
    """Per-listing weights (review counts when the source has them, else 1 per listing), the market mean
    and the prior strength C = median weight of rated listings (data-driven)."""
    r = pd.to_numeric(rating, errors="coerce")
    if reviews is not None and pd.to_numeric(reviews, errors="coerce").notna().any():
        w = pd.to_numeric(reviews, errors="coerce").fillna(0).clip(lower=0)
        basis = "review counts"
    else:
        w = pd.Series(1.0, index=r.index)
        basis = "one per listing (no review counts in source)"
    w = w.where(r.notna(), 0.0)
    m = float((r.fillna(0) * w).sum() / w.sum()) if w.sum() > 0 else float("nan")
    rated = w[w > 0]
    c = float(rated.median()) if len(rated) else 1.0
    return w, m, c, basis


def table(L: pd.DataFrame, draws: np.ndarray, brand_ids, lvl: float) -> tuple[pd.DataFrame, dict]:
    """``draws``: (sims, brands) simulated monthly revenue per brand; ``brand_ids`` in the same order."""
    cfg = config()["brands"]
    brand = L["brand"].fillna("(no brand)").astype(str)
    live = draws.sum(axis=1) > 1e-9                     # a draw with no market revenue defines no shares
    if live.any() and not live.all():
        draws = draws[live]
    total = np.maximum(draws.sum(axis=1, keepdims=True), 1e-9)
    shares = draws / total
    hhi = (shares ** 2).sum(axis=1) * 10000
    n_eff = 10000 / max(float(hhi.mean()), 1e-9)
    ranks = (-draws).argsort(axis=1).argsort(axis=1) + 1                    # rank of each brand in each draw
    rk_lo, rk_hi = _ci(ranks, lvl)
    sh_lo, sh_hi = _ci(shares, lvl)
    rv_lo, rv_hi = _ci(draws, lvl)
    se, re_ = shares.mean(0), draws.mean(0)            # bounds clamped: exact data must not round outside
    out = pd.DataFrame({"brand": list(brand_ids), "revenue_est": re_, "revenue_lo": np.minimum(rv_lo, re_),
                        "revenue_hi": np.maximum(rv_hi, re_), "share_est": se, "share_lo": np.minimum(sh_lo, se),
                        "share_hi": np.maximum(sh_hi, se),
                        "rank_lo": np.floor(rk_lo).astype(int), "rank_hi": np.ceil(rk_hi).astype(int),
                        "p_top": (ranks == 1).mean(0)})

    price = pd.to_numeric(L["price"], errors="coerce")
    seg_med = price.groupby(L["segment_id"]).transform("median")
    w, m, c, basis = bayes_rating(L.get("rating", pd.Series(np.nan, index=L.index)), L.get("reviews"))
    r = pd.to_numeric(L.get("rating"), errors="coerce").fillna(0)
    rev_l = pd.to_numeric(L.get("revenue_est"), errors="coerce").fillna(0)
    ent = L.get("is_entrant", pd.Series(False, index=L.index)).fillna(False).astype(bool)
    frame = pd.DataFrame({"brand": brand, "product_id": L["product_id"].astype(str), "segment": L["segment_id"].astype(str),
                          "seg_label": L.get("segment_label", L["segment_id"]).astype(str), "price": price,
                          "pidx": price / seg_med, "w": w, "rw": r * w, "rev": rev_l, "ent": ent, "ent_rev": rev_l * ent,
                          "units": pd.to_numeric(L.get("units_est"), errors="coerce").fillna(0),
                          "age": pd.to_numeric(L.get("age_days"), errors="coerce")})
    g = frame.groupby("brand")
    agg = pd.DataFrame({"listings": g.size(), "products": g["product_id"].nunique(), "segments": g["segment"].nunique(),
                        "units_est": g["units"].sum(), "median_price": g["price"].median(), "price_index": g["pidx"].median(),
                        "reviews": g["w"].sum(), "rw": g["rw"].sum(), "entrants": g["ent"].sum(),
                        "ent_rev": g["ent_rev"].sum(), "rev_l": g["rev"].sum(), "median_age_days": g["age"].median()})
    by_seg = frame.groupby(["brand", "seg_label"])["rev"].sum().reset_index().sort_values("rev", ascending=False)
    agg["top_segment"] = by_seg.drop_duplicates("brand").set_index("brand")["seg_label"]
    agg["rating_bayes"] = (c * m + agg["rw"]) / (c + agg["reviews"]) if np.isfinite(m) else np.nan
    agg.loc[agg["reviews"] <= 0, "rating_bayes"] = np.nan
    agg["rating_gap"] = agg["rating_bayes"] - m
    agg["entrant_revenue_share"] = np.where(agg["rev_l"] > 0, agg["ent_rev"] / agg["rev_l"].where(agg["rev_l"] > 0), np.nan)
    has_age = frame["age"].notna().any()
    if not has_age:
        agg["entrants"] = np.nan
        agg["entrant_revenue_share"] = np.nan
    out = out.merge(agg.drop(columns=["rw", "ent_rev", "rev_l"]).reset_index(), on="brand", how="left")
    if "reviews" in L and pd.to_numeric(L["reviews"], errors="coerce").notna().any():
        out["reviews"] = out["reviews"].round(0)
    else:
        out["reviews"] = np.nan

    fair = 1.0 / n_eff
    pos = np.where(out["p_top"] >= cfg["leader_p_top"], "leader",
                   np.where(out["share_lo"] > fair, "above_par", np.where(out["share_hi"] < fair, "below_par", "par")))
    out["position"] = pos
    out["signals"] = [_signals(r, cfg, has_age) for _, r in out.iterrows()]
    out = out.sort_values("share_est", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    info = {"hhi": round(float(hhi.mean()), 1), "hhi_lo": round(float(_ci(hhi, lvl)[0]), 1),
            "hhi_hi": round(float(_ci(hhi, lvl)[1]), 1), "effective_competitors": round(n_eff, 2),
            "equal_share_benchmark": round(fair, 4), "rating_mean": None if not np.isfinite(m) else round(m, 3),
            "rating_prior_strength": round(c, 1), "rating_weight_basis": basis, "brands": int(len(out))}
    return out[BRAND_COLUMNS], info


def _signals(r, cfg: dict, has_age: bool) -> list[dict]:
    """Rule-coded, value-carrying strengths (+) and weaknesses (-). Codes are translated by the frontend."""
    s: list[dict] = []
    gap, pidx = r.get("rating_gap"), r.get("price_index")
    enough = (r.get("listings") or 0) >= cfg["min_listings_for_signals"]
    if pd.notna(gap) and enough:
        if gap <= -cfg["rating_gap"]:
            if pd.notna(pidx) and pidx >= 1 + cfg["price_premium"]:
                s.append({"code": "premium_low_rating", "kind": "weakness", "value": round(float(gap), 2),
                          "price_index": round(float(pidx), 2), "opening": "better_value"})
            else:
                s.append({"code": "low_rating", "kind": "weakness", "value": round(float(gap), 2), "opening": "better_quality"})
        elif gap >= cfg["rating_gap"]:
            s.append({"code": "high_rating", "kind": "strength", "value": round(float(gap), 2)})
    if pd.notna(pidx) and enough and pidx <= 1 - cfg["price_premium"] and not (pd.notna(gap) and gap <= -cfg["rating_gap"]):
        s.append({"code": "value_price", "kind": "strength", "value": round(float(pidx), 2)})
    if has_age and enough:
        if (r.get("entrants") or 0) == 0:
            s.append({"code": "no_recent_launches", "kind": "weakness", "value": 0, "opening": "newer_design"})
        elif pd.notna(r.get("entrant_revenue_share")) and r["entrant_revenue_share"] >= cfg["entrant_driven_share"]:
            s.append({"code": "entrant_driven", "kind": "strength", "value": round(float(r["entrant_revenue_share"]), 3)})
    return s


def cohorts(L: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Listings by launch quarter with their estimated revenue today (survivors only)."""
    ld = pd.to_datetime(L.get("launch_date"), errors="coerce")
    if ld is None or ld.notna().sum() == 0:
        return pd.DataFrame(columns=["quarter", "launched", "revenue_est", "revenue_share", "revenue_per_listing"])
    q = ld.dt.to_period("Q").astype(str)
    rev = pd.to_numeric(L.get("revenue_est"), errors="coerce").fillna(0)
    frame = pd.DataFrame({"quarter": q, "rev": rev})[ld.notna()]
    g = frame.groupby("quarter")
    out = pd.DataFrame({"launched": g.size(), "revenue_est": g["rev"].sum()}).reset_index()
    out["revenue_share"] = out["revenue_est"] / max(float(rev.sum()), 1e-9)
    out["revenue_per_listing"] = out["revenue_est"] / out["launched"]
    lookback = config()["brands"]["cohort_quarters"]
    return out.sort_values("quarter").tail(lookback).reset_index(drop=True)


def momentum(L: pd.DataFrame, as_of: pd.Timestamp, segments: pd.DataFrame | None = None) -> pd.DataFrame:
    """Launches in the last window vs the window before, per market ('') and segment, exact binomial test."""
    cfg = config()["brands"]
    win = pd.Timedelta(days=config()["entry"]["entrant_window_days"])
    ld = pd.to_datetime(L.get("launch_date"), errors="coerce")
    cols = ["scope_id", "segment_label", "recent", "previous", "rate_ratio", "p_value", "q_value", "direction", "significant",
            "entrant_revenue_share"]
    if ld is None or ld.notna().sum() == 0:
        return pd.DataFrame(columns=cols)
    recent = (ld > as_of - win) & (ld <= as_of)
    prev = (ld > as_of - 2 * win) & (ld <= as_of - win)
    rows = []
    groups = [("", np.ones(len(L), dtype=bool))] + [(sid, (L["segment_id"] == sid).to_numpy()) for sid in L["segment_id"].unique()]
    for sid, mask in groups:
        a, b = int((recent & mask).sum()), int((prev & mask).sum())
        n = a + b
        if n < cfg["min_launches_for_test"]:
            p = None
        else:
            p = float(stats.binomtest(a, n, 0.5).pvalue)
        rows.append({"scope_id": sid, "recent": a, "previous": b, "rate_ratio": (a + 0.5) / (b + 0.5), "p_value": p})
    out = pd.DataFrame(rows)
    out = out[(out["scope_id"] == "") | (out["recent"] + out["previous"] > 0)].reset_index(drop=True)
    seg = out["scope_id"] != ""
    tested = seg & out["p_value"].notna()
    out["q_value"] = np.nan
    if tested.any():
        out.loc[tested, "q_value"] = _bh(out.loc[tested, "p_value"].to_numpy())
    mkt = out["scope_id"] == ""
    out.loc[mkt, "q_value"] = out.loc[mkt, "p_value"]
    out["significant"] = out["q_value"].notna() & (out["q_value"] <= cfg["fdr_q"])
    out["direction"] = np.where(~out["significant"], "no_significant_change",
                                np.where(out["recent"] > out["previous"], "accelerating", "slowing"))
    labels = {} if segments is None or segments.empty else segments.set_index("segment_id")["segment_label"].to_dict()
    ers = {} if segments is None or "entrant_revenue_share" not in segments else \
        segments.set_index("segment_id")["entrant_revenue_share"].to_dict()
    out["segment_label"] = out["scope_id"].map(lambda s: labels.get(s, "") if s else "")
    out["entrant_revenue_share"] = out["scope_id"].map(lambda s: ers.get(s) if s else None)
    return out[cols]


def history_rows(brands: pd.DataFrame, period) -> pd.DataFrame:
    h = brands[["brand", "share_est", "share_lo", "share_hi", "revenue_est", "revenue_lo", "revenue_hi"]].copy()
    h.insert(0, "period", str(pd.Timestamp(period).date()))
    return h


def changes(history: pd.DataFrame, lvl: float) -> tuple[pd.DataFrame, dict]:
    """Share change between the two latest periods; z = diff / sqrt(se1^2 + se2^2), se from the interval."""
    cfg = config()["brands"]
    cols = ["brand", "share_from", "share_to", "change", "z", "p_value", "q_value", "significant"]
    if history is None or history.empty or history["period"].nunique() < 2:
        return pd.DataFrame(columns=cols), {"status": "needs_two_snapshots",
                                            "periods": 0 if history is None else int(history["period"].nunique())}
    ps = sorted(history["period"].unique())
    a, b = ps[-2], ps[-1]
    zc = stats.norm.ppf(0.5 + lvl / 2)
    A = history[history["period"] == a].set_index("brand")
    B = history[history["period"] == b].set_index("brand")
    brands = A.index.union(B.index)
    rows = []
    for br in brands:
        s1 = float(A["share_est"].get(br, 0.0))
        s2 = float(B["share_est"].get(br, 0.0))
        se1 = float(A["share_hi"].get(br, 0.0) - A["share_lo"].get(br, 0.0)) / (2 * zc)
        se2 = float(B["share_hi"].get(br, 0.0) - B["share_lo"].get(br, 0.0)) / (2 * zc)
        se = np.hypot(se1, se2)
        z = (s2 - s1) / se if se > 0 else 0.0
        rows.append({"brand": br, "share_from": s1, "share_to": s2, "change": s2 - s1, "z": z,
                     "p_value": float(2 * stats.norm.sf(abs(z))) if se > 0 else 1.0})
    out = pd.DataFrame(rows)
    out["q_value"] = _bh(out["p_value"].to_numpy())
    out["significant"] = out["q_value"] <= cfg["fdr_q"]
    out = out.reindex(out["change"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return out[cols], {"status": "ok", "from": a, "to": b, "tested": int(len(out)), "significant": int(out["significant"].sum())}

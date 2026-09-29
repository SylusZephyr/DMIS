"""Data Confidence Framework.

Every product (and, rolled up, every segment and market) gets one confidence
score built from four measurable components::

    source reliability      how far the source is trusted (config, per adapter)
  + data completeness       share of key fields present on its listings
  + verification status     sales observed, several listings corroborate,
                            clean quality checks, relevance confirmed,
                            rating backed by enough reviews
  + historical consistency  stability of its sales across snapshots
  = confidence score (0-100) with human-readable reasons

A component with no evidence is left out (its weight redistributed) and the
score is capped, so a single snapshot can never look as certain as a history.
Nothing is estimated: a missing review count is reported as missing.
The v1 per-record ``data_confidence`` (quality checks) is an input here, not replaced.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dip.settings import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config" / "platform" / "confidence.yaml"
COMPONENTS = ("source_reliability", "completeness", "verification", "historical_consistency")


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))


def source_reliability(adapter: str | None) -> float:
    table = config()["source_reliability"]
    return float(table.get(adapter or "default", table["default"]))


def level(score: float | None) -> str:
    if score is None or pd.isna(score):
        return "unknown"
    lv = config()["levels"]
    return "high" if score >= lv["high"] else "medium" if score >= lv["medium"] else "low"


def _present(s: pd.Series) -> np.ndarray:
    if s.dtype == object:
        return (s.notna() & (s.astype(str).str.strip() != "")).to_numpy()
    return s.notna().to_numpy()


def history_consistency(records: pd.DataFrame | None, product_of_listing: dict) -> pd.DataFrame:
    """Per product: number of periods with sales and the coefficient of variation of
    its total sales across them (``records`` = listing history with a ``period``
    column). Empty when the data has a single period."""
    cols = ["product_id", "snapshots", "sales_cv"]
    if records is None or records.empty or records["period"].nunique() < 2:
        return pd.DataFrame(columns=cols)
    r = records.loc[records["sales"].notna(), ["id", "period", "sales"]]
    r = r.assign(product_id=r["id"].map(product_of_listing)).dropna(subset=["product_id"])
    per = r.groupby(["product_id", "period"], sort=False)["sales"].sum()
    g = per.groupby(level=0)
    out = pd.DataFrame({"snapshots": g.size(), "mean": g.mean(), "std": g.std(ddof=0)})
    out["sales_cv"] = (out["std"] / out["mean"].replace(0, np.nan)).fillna(0.0)
    return out.reset_index()[cols]


def score_products(products: pd.DataFrame, listings: pd.DataFrame, adapter: str | None,
                   history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Adds confidence_score, confidence_level, confidence_components (JSON) and
    confidence_reasons (JSON list of {signal, ok, detail}) to the Product Master."""
    cfg = config()
    vcfg = cfg["verification"]
    L = listings
    # --- listing-level evidence, aggregated per product (vectorised) -------------
    fw = cfg["completeness_fields"]
    comp = np.zeros(len(L))
    for f, w in fw.items():
        if f in L:
            comp += w * _present(L[f])
    comp /= sum(fw.values())
    derived = L["_revenue_derived"].fillna(False).astype(bool).to_numpy() if "_revenue_derived" in L else np.zeros(len(L), bool)
    sales_obs = L["sales"].notna().to_numpy() & ~derived
    clean = (L["data_confidence"].fillna(0).to_numpy() >= vcfg["high_quality_confidence"]) if "data_confidence" in L else np.zeros(len(L), bool)
    status = L["relevance_status"].astype(str) if "relevance_status" in L else pd.Series("relevant", index=L.index)
    rscore = L["relevance_score"].fillna(50).to_numpy() / 100 if "relevance_score" in L else np.full(len(L), 0.5)
    rel_conf = np.where(status.str.startswith("human").to_numpy(), 1.0,
                        np.where(status.eq("relevant").to_numpy(), rscore, 0.3))
    reviews = pd.to_numeric(L["reviews"], errors="coerce") if "reviews" in L else pd.Series(np.nan, index=L.index)
    has_rating = L["rating"].notna().to_numpy() if "rating" in L else np.zeros(len(L), bool)
    rating_ver = np.where(has_rating & (reviews.fillna(-1).to_numpy() >= vcfg["min_reviews_for_verified_rating"]), 1.0,
                          np.where(has_rating & reviews.isna().to_numpy(), 0.5, 0.0))
    ev = pd.DataFrame({"product_id": L["product_id"].to_numpy(), "completeness": comp, "sales_obs": sales_obs,
                       "clean": clean, "rel": rel_conf, "rating_ver": rating_ver,
                       "reviews": reviews.to_numpy(), "has_rating": has_rating}).dropna(subset=["product_id"])
    g = ev.groupby("product_id", sort=False)
    agg = pd.DataFrame({
        "n_listings": g.size(), "completeness": g["completeness"].mean(), "sales_listings": g["sales_obs"].sum(),
        "clean_share": g["clean"].mean(), "relevance": g["rel"].mean(), "rating_ver": g["rating_ver"].max(),
        "max_reviews": g["reviews"].max(), "rated": g["has_rating"].any(),
    })
    p = products.copy()
    a = agg.reindex(p["product_id"].to_numpy())
    n_list = a["n_listings"].fillna(0).to_numpy()
    checks = vcfg["checks"]
    parts = {
        "sales_observed": (a["sales_listings"].fillna(0).to_numpy() > 0).astype(float),
        "corroborated": (n_list >= vcfg["corroborating_listings"]).astype(float),
        "clean_record": a["clean_share"].fillna(0).to_numpy(),
        "relevance_confirmed": a["relevance"].fillna(0).to_numpy(),
        "rating_verified": a["rating_ver"].fillna(0).to_numpy(),
    }
    verification = sum(checks[k] * v for k, v in parts.items()) / sum(checks.values())
    completeness = a["completeness"].fillna(0).to_numpy()
    src = np.full(len(p), source_reliability(adapter))

    h = history_consistency(history, dict(zip(L["id"], L["product_id"]))) if history is not None else pd.DataFrame()
    hcfg = cfg["history"]
    if len(h):
        h = h.set_index("product_id").reindex(p["product_id"].to_numpy())
        snaps = h["snapshots"].fillna(0).to_numpy()
        cv = h["sales_cv"].to_numpy(dtype=float)
        hist = np.clip((hcfg["unstable_cv"] - cv) / (hcfg["unstable_cv"] - hcfg["stable_cv"]), 0, 1)
        hist = np.where(snaps >= hcfg["min_snapshots"], hist, np.nan)
    else:
        snaps, cv, hist = np.zeros(len(p)), np.full(len(p), np.nan), np.full(len(p), np.nan)

    w = cfg["weights"]
    M = np.column_stack([src, completeness, verification, hist])
    W = np.array([w[c] for c in COMPONENTS])
    avail = ~np.isnan(M)
    score = np.nansum(M * W, axis=1) / (avail * W).sum(axis=1) * 100
    caps = cfg["caps"]
    no_hist = np.isnan(hist)
    score = np.where(no_hist, np.minimum(score, caps["without_history"]), score)
    no_sales = parts["sales_observed"] == 0
    score = np.where(no_sales, np.minimum(score, caps["without_sales"]), score)
    p["confidence_score"] = np.round(score, 1)
    p["confidence_level"] = [level(s) for s in p["confidence_score"]]
    p["confidence_components"] = [
        json.dumps({c: (None if np.isnan(v) else round(float(v) * 100, 1)) for c, v in zip(COMPONENTS, row)})
        for row in M
    ]
    p["confidence_reasons"] = [
        json.dumps(_reasons(adapter, int(n_list[i]), int(a["sales_listings"].fillna(0).iat[i]), completeness[i],
                            parts["clean_record"][i], parts["relevance_confirmed"][i], bool(a["rated"].fillna(False).iat[i]),
                            a["max_reviews"].iat[i], snaps[i], cv[i], vcfg, hcfg))
        for i in range(len(p))
    ]
    return p


def _reasons(adapter, n_list, n_sales, completeness, clean, relevance, rated, max_reviews, snaps, cv, vcfg, hcfg) -> list[dict]:
    out = []
    add = lambda signal, ok, detail: out.append({"signal": signal, "ok": bool(ok), "detail": detail})  # noqa: E731
    add("source", source_reliability(adapter) >= 0.8, f"{adapter or 'unknown'} source (reliability {source_reliability(adapter):.0%})")
    add("sales", n_sales > 0, f"sales observed on {n_sales} of {n_list} listing(s)" if n_sales else "no observed sales — demand unverified")
    add("listings", n_list >= vcfg["corroborating_listings"],
        f"{n_list} listings corroborate this product" if n_list >= vcfg["corroborating_listings"] else "single listing")
    add("completeness", completeness >= 0.75, f"{completeness:.0%} of key fields present")
    add("quality", clean >= 0.99, "passes all quality checks" if clean >= 0.99 else f"{1 - clean:.0%} of listings carry quality flags")
    add("relevance", relevance >= 0.9, f"relevance confidence {relevance:.0%}")
    if rated and pd.notna(max_reviews):
        add("reviews", max_reviews >= vcfg["min_reviews_for_verified_rating"], f"{int(max_reviews):,} reviews")
    elif rated:
        add("reviews", False, "rating present but review count not in source")
    else:
        add("reviews", False, "no rating")
    if snaps >= hcfg["min_snapshots"]:
        add("history", cv <= hcfg["stable_cv"], f"sales across {int(snaps)} snapshots vary {cv:.0%}"
            + (" (consistent)" if cv <= hcfg["stable_cv"] else " (volatile)"))
    else:
        add("history", False, "one snapshot — consistency measurable after the next upload")
    return out


def rollup(products: pd.DataFrame, by: str | None = None) -> pd.DataFrame | dict:
    """Revenue-weighted confidence of a group of products (unweighted where revenue is unknown)."""
    def one(g: pd.DataFrame) -> dict:
        s = g["confidence_score"].astype(float)
        wts = g["monthly_revenue"].astype(float) if "monthly_revenue" in g else pd.Series(np.nan, index=g.index)
        if wts.notna().any() and wts.fillna(0).sum() > 0:
            wts = wts.fillna(wts[wts > 0].min() if (wts > 0).any() else 1.0)
            score = float(np.average(s, weights=wts))
        else:
            score = float(s.mean()) if len(s) else None
        return {"confidence_score": None if score is None else round(score, 1), "confidence_level": level(score),
                "high_confidence_products": int((s >= config()["levels"]["high"]).sum()),
                "low_confidence_products": int((s < config()["levels"]["medium"]).sum())}
    if by is None:
        return one(products)
    rows = [{by: k, **one(g)} for k, g in products.groupby(by, sort=False)]
    return pd.DataFrame(rows, columns=[by, "confidence_score", "confidence_level", "high_confidence_products",
                                       "low_confidence_products"])

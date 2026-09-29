"""Price-band demand inside a segment (post-Phase 8).

Listings of a segment are split into the engine's price bands (quantiles of price within the segment,
``price_band`` on the listings table). Per band: listings, price range and median, mean estimated units per
listing with a percentile-bootstrap interval over listings, and the band's share of the segment's estimated
revenue with its bootstrap interval. The bootstrap resamples listings, so the interval is the sampling
variation of "a listing in this band"; each listing's own model interval is on the listings table.
Settings: ``segment_detail`` in config/platform/metrics.yaml.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dip.metrics import config


def price_bands(listings: pd.DataFrame, level: float | None = None) -> list[dict]:
    cfg = config()["segment_detail"]
    level = level or config()["engine"]["interval"]
    need = {"price_band", "price", "units_est"}
    if listings is None or listings.empty or not need <= set(listings.columns):
        return []
    L = listings.dropna(subset=["price_band", "price", "units_est"])
    if L.empty:
        return []
    rev = (L["revenue_est"] if "revenue_est" in L else L["units_est"] * L["price"]).fillna(0).to_numpy(dtype=float)
    units = L["units_est"].to_numpy(dtype=float)
    band = L["price_band"].to_numpy(dtype=int)
    bands = np.unique(band)
    rng = np.random.default_rng(cfg["seed"])
    B, n = cfg["bootstrap"], len(L)
    idx = rng.integers(0, n, size=(B, n))
    rb = rev[idx]
    tot = rb.sum(axis=1)
    a = (1 - level) / 2
    out = []
    for b in bands:
        m = band == b
        k = int(m.sum())
        u = units[m]
        ui = rng.integers(0, k, size=(B, k))
        means = u[ui].mean(axis=1)
        sh = np.where(tot > 0, (rb * m[idx]).sum(axis=1) / np.where(tot > 0, tot, 1), np.nan)
        p = L["price"].to_numpy(dtype=float)[m]
        out.append({"band": int(b) + 1, "bands": int(bands.max()) + 1, "listings": k,
                    "price_min": float(p.min()), "price_max": float(p.max()), "price_median": float(np.median(p)),
                    "units_mean": float(u.mean()), "units_lo": float(np.quantile(means, a)), "units_hi": float(np.quantile(means, 1 - a)),
                    "revenue_share": float(rev[m].sum() / rev.sum()) if rev.sum() > 0 else None,
                    "revenue_share_lo": float(np.nanquantile(sh, a)) if np.isfinite(sh).any() else None,
                    "revenue_share_hi": float(np.nanquantile(sh, 1 - a)) if np.isfinite(sh).any() else None})
    return out


POSITION_METRICS = ("price", "units_est", "revenue_est", "rating", "reviews")


def position(product: dict, peers: pd.DataFrame) -> dict:
    """Where one product sits among the products of its segment: the mid-rank percentile
    (share below + half the share tied) and the segment median, per metric; its share of the segment's
    estimated revenue. ``peers`` includes the product itself."""
    out: dict = {"peers": int(len(peers)), "metrics": {}}
    for m in POSITION_METRICS:
        v = product.get(m)
        if m not in peers or v is None or pd.isna(v):
            continue
        x = pd.to_numeric(peers[m], errors="coerce").dropna().to_numpy(dtype=float)
        if not len(x):
            continue
        out["metrics"][m] = {"value": float(v), "percentile": float(((x < v).sum() + 0.5 * (x == v).sum()) / len(x)),
                             "median": float(np.median(x)), "n": int(len(x))}
    if "revenue_est" in peers and product.get("revenue_est") is not None and not pd.isna(product.get("revenue_est")):
        tot = float(pd.to_numeric(peers["revenue_est"], errors="coerce").fillna(0).sum())
        out["revenue_share"] = float(product["revenue_est"]) / tot if tot > 0 else None
    return out

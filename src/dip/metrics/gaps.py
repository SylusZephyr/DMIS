"""Gap analysis -> recommended product (Phase 5.4).

For every segment and every candidate feature (recurring title term, bucketed spec, price band):

    supply share = listings with the feature / listings
    demand share = estimated units of those listings / estimated units
    lift         = mean estimated units per listing WITH the feature / WITHOUT it

with a listing-bootstrap interval, a one-sided bootstrap p-value (share of resamples with lift <= 1),
and Benjamini-Hochberg control of the false discovery rate across all tested features of the
market (many features are tested at once; without it some "gaps" would be noise).

A **gap** is a feature with FDR-significant lift > 1 whose demand share exceeds its supply share:
buyers reward it more than sellers supply it. The **recommended spec** of a segment combines up to
``max_features`` gap features that co-occur in the data, plus the price band with the highest demand
per listing (and a positive unit margin when margins are known), with comparables and evidence.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from scipy.stats import norm

from dip.metrics import config

_TOKEN = re.compile(r"(?u)\b[a-zA-Z][a-zA-Z\-]{2,}\b")


def _bucket(v: float, edges: list[float]) -> str | None:
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo <= v < hi:
            return f"{lo:g}-{hi:g}" if hi < 1e6 else f">={lo:g}"
    return None


def _spec_features(specs) -> set[str]:
    if isinstance(specs, str):
        import json
        try:
            specs = json.loads(specs)
        except ValueError:
            specs = {}
    out = set()
    edges = config()["gaps"]["spec_buckets"]
    for k, v in (specs or {}).items():
        if k in edges and isinstance(v, (int, float)):
            b = _bucket(float(v), edges[k])
            if b:
                out.add(f"{k} {b}")
    return out


def _term_features(title: str, stop: set[str]) -> set[str]:
    words = [w.lower() for w in _TOKEN.findall(title or "")]
    words = [w for w in words if w not in stop]
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}


def _bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted q-values."""
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    q = np.empty(n)
    prev = 1.0
    for rank, i in zip(range(n, 0, -1), order[::-1]):
        prev = min(prev, p[i] * n / rank)
        q[i] = prev
    return q


def analyse(listings: pd.DataFrame, segments: pd.DataFrame, seed: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (gaps table: one row per segment x feature tested, recommendations: one row per segment)."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    cfg = config()["gaps"]
    rng = np.random.default_rng(config()["demand_model"]["seed"] + 23 if seed is None else seed)
    stop = set(ENGLISH_STOP_WORDS) | {s.lower() for s in config()["segmentation"]["stop_terms"]}
    stop |= {w.lower() for b in listings["brand"].dropna().astype(str) for w in _TOKEN.findall(b)}
    rows = []
    feats_by_seg: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for sid, g in listings.groupby("segment_id"):
        g = g[pd.to_numeric(g["units_est"], errors="coerce").notna()]
        n = len(g)
        if n < cfg["min_segment_listings"]:
            continue
        units = g["units_est"].to_numpy(dtype=float)
        leaf_words = {w.lower() for w in _TOKEN.findall(str(g.get("family_label", pd.Series([""])).iloc[0]))}
        feats = [(_term_features(t, stop | leaf_words) | _spec_features(sp)) for t, sp in zip(g["title"], g.get("specs", [None] * n))]
        if "price_band" in g:
            nb = int(g["price_band"].max()) + 1 if g["price_band"].notna().any() else 0
            for i, pb in enumerate(g["price_band"]):
                if pd.notna(pb):
                    feats[i] = feats[i] | {f"price band {int(pb) + 1}/{nb}"}
        counts = pd.Series([f for fs in feats for f in fs]).value_counts()
        cand = counts[(counts >= cfg["min_support"]) & (counts <= n - cfg["min_support"])].head(cfg["max_candidates"]).index
        if not len(cand):
            continue
        M = np.array([[f in fs for f in cand] for fs in feats])                      # listings x features
        # features present on exactly the same listings are one signal: keep one, list the others as aliases
        keys = {}
        for j, f in enumerate(cand):
            keys.setdefault(M[:, j].tobytes(), []).append(f)
        keep = [min(fs, key=lambda x: (x.startswith("price band") is False and x.count(" "), -counts[x], len(x)))
                for fs in keys.values()]
        aliases = {k: [x for x in fs if x != k] for k, fs in zip(keep, keys.values())}
        cols = [j for j, f in enumerate(cand) if f in keep]
        cand = [cand[j] for j in cols]
        M = M[:, cols]
        feats_by_seg[sid] = (g, pd.DataFrame(M, columns=cand, index=g.index))
        idx = rng.integers(0, n, (cfg["bootstrap"], n))
        U = units[idx]                                                                  # B x n
        for j, f in enumerate(cand):
            has = M[:, j]
            with_ = units[has].mean()
            without = units[~has].mean()
            lift = with_ / without if without > 0 else np.nan
            hb = has[idx]
            num = (U * hb).sum(1) / np.maximum(hb.sum(1), 1)
            den = (U * ~hb).sum(1) / np.maximum((~hb).sum(1), 1)
            ok = (hb.sum(1) > 0) & ((~hb).sum(1) > 0) & (den > 0)
            lb = num[ok] / den[ok]
            lo, hi = (np.percentile(lb, [2.5, 97.5]) if len(lb) >= 20 else (np.nan, np.nan))
            # one-sided p-value for lift > 1 from the bootstrap distribution of log lift (normal approximation:
            # continuous, unlike counting resamples, whose resolution 1/B defeats FDR control over many tests)
            ll = np.log(lb[lb > 0]) if len(lb) else np.array([])
            sd = float(ll.std()) if len(ll) >= 20 else 0.0
            p = float(norm.sf(ll.mean() / sd)) if sd > 1e-9 else 1.0
            supply = has.mean()
            demand = units[has].sum() / max(units.sum(), 1e-9)
            ent = None
            if "is_entrant" in g:
                e = g["is_entrant"].to_numpy(dtype=bool)
                ent = int((e & has).sum())
            rows.append({"segment_id": sid, "feature": f, "aliases": aliases.get(f, []), "kind": "spec" if f.split(" ")[0] in cfg["spec_buckets"]
                         else "price band" if f.startswith("price band") else "term",
                         "listings_with": int(has.sum()), "listings": n, "supply_share": round(float(supply), 4),
                         "demand_share": round(float(demand), 4), "units_per_listing_with": round(float(with_), 2),
                         "units_per_listing_without": round(float(without), 2), "lift": None if np.isnan(lift) else round(float(lift), 3),
                         "lift_lo": None if np.isnan(lo) else round(float(lo), 3), "lift_hi": None if np.isnan(hi) else round(float(hi), 3),
                         "p_value": round(p, 4), "entrants_with": ent})
    gaps = pd.DataFrame(rows, columns=list(rows[0]) if rows else
                        ["segment_id", "feature", "aliases", "kind", "listings_with", "listings", "supply_share", "demand_share",
                         "units_per_listing_with", "units_per_listing_without", "lift", "lift_lo", "lift_hi", "p_value",
                         "entrants_with"])
    if gaps.empty:
        gaps["q_value"] = pd.Series(dtype=float)
        gaps["is_gap"] = pd.Series(dtype=bool)
        return gaps, _recommend(gaps, feats_by_seg, segments, cfg)
    gaps["q_value"] = np.round(_bh(gaps["p_value"].to_numpy()), 4)
    gaps["is_gap"] = (gaps["q_value"] <= cfg["fdr_q"]) & (gaps["lift_lo"].fillna(0) > 1) & (gaps["demand_share"] > gaps["supply_share"])
    return gaps, _recommend(gaps, feats_by_seg, segments, cfg)


REC_COLUMNS = ["segment_id", "segment_label", "opportunity_index", "features", "feature_evidence", "price_band", "price_range",
               "comparables", "expected_units", "expected_units_lo", "expected_units_hi", "basis"]


def _recommend(gaps: pd.DataFrame, feats_by_seg: dict, segments: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    seg = segments.set_index("segment_id") if len(segments) else pd.DataFrame()
    out = []
    sig = gaps[gaps["is_gap"]] if len(gaps) else gaps
    for sid, (g, M) in feats_by_seg.items():
        grp = sig[sig["segment_id"] == sid]
        chosen: list[str] = []
        for f in grp.sort_values(["lift_lo", "demand_share"], ascending=False)["feature"]:
            if f.startswith("price band"):
                continue
            if any(set(f.split()) & set(c.split()) for c in chosen):         # overlapping n-grams = same idea
                continue
            together = M[chosen + [f]].all(axis=1) if chosen else M[f]
            if together.sum() >= cfg["min_support"]:
                chosen.append(f)
            if len(chosen) >= cfg["max_features"]:
                break
        bands = g.groupby("price_band").agg(units=("units_est", "mean"), p_lo=("price", "min"), p_hi=("price", "max"),
                                             margin=("unit_margin", "median") if "unit_margin" in g else ("price", "size"))
        if "unit_margin" in g and g["unit_margin"].notna().any():
            bands = bands[bands["margin"].isna() | (bands["margin"] > 0)]
        best_band = bands["units"].idxmax() if len(bands) else None
        if chosen:
            match = M[chosen].all(axis=1).to_numpy()
            basis = "existing listings with all recommended features"
        else:
            match = (g["price_band"] == best_band).to_numpy() if best_band is not None else np.zeros(len(g), dtype=bool)
            basis = "no significant feature gap -- listings in the best price band"
        comps = g[match].sort_values("units_est", ascending=False)
        u = comps["units_est"].to_numpy(dtype=float)
        rng = np.random.default_rng(len(out))
        boot = [rng.choice(u, len(u)).mean() for _ in range(300)] if len(u) >= 2 else []
        r = seg.loc[sid] if sid in seg.index else {}
        out.append({"segment_id": sid, "segment_label": r.get("segment_label") if len(seg) else None,
                    "opportunity_index": r.get("opportunity_index") if len(seg) else None,
                    "features": chosen,
                    "feature_evidence": grp.set_index("feature").loc[chosen, ["lift", "lift_lo", "lift_hi", "q_value", "supply_share",
                                                                               "demand_share"]].reset_index().to_dict("records") if chosen else [],
                    "price_band": None if best_band is None else int(best_band) + 1,
                    "price_range": None if best_band is None else [round(float(bands.loc[best_band, "p_lo"]), 2),
                                                                     round(float(bands.loc[best_band, "p_hi"]), 2)],
                    "comparables": comps[["id", "title", "price", "units_est"]].head(5).to_dict("records"),
                    "expected_units": round(float(u.mean()), 1) if len(u) else None,
                    "expected_units_lo": round(float(np.percentile(boot, 2.5)), 1) if boot else None,
                    "expected_units_hi": round(float(np.percentile(boot, 97.5)), 1) if boot else None,
                    "basis": f"{len(u)} {basis}"})
    rec = pd.DataFrame(out, columns=REC_COLUMNS)
    if len(rec):
        rec = rec.sort_values("opportunity_index", ascending=False, na_position="last").reset_index(drop=True)
    return rec

"""Stage 5 -- Automatic product discovery: Family -> Segment -> Model.

Wraps ``dmie.engine.discovery.discover_segments`` (K-Means families,
DBSCAN/K-Means segments, class-TF-IDF labels) and adds the third level:
**models** inside each segment, grouped by the products' own parsed
specification signature (rpm, wattage, pack size, model tokens such as
N3 / H37L1 / 102L). Nothing is hardcoded -- a model is simply a recurring
specification combination found in the data.
"""

from __future__ import annotations

import hashlib
from collections import Counter

import numpy as np
import pandas as pd

from dmie.engine.discovery import DiscoveryResult, discover_segments
from dmie.engine.features import clean_title, extract_specs

# Up to this many current listings the exact v1 algorithm runs unchanged.
# Above it: one representative per identical (title, brand), and if still too
# many, fit on a fixed-seed sample and assign the rest to the nearest segment.
DIRECT_LIMIT = 5_000
SAMPLE_SIZE = 20_000

SPEC_ORDER = [("rpm", "{:,.0f} rpm"), ("watt", "{:,.0f} W"), ("volt", "{:,.0f} V"), ("pack", "{:,.0f} pcs"),
              ("ml", "{:,.0f} ml"), ("gram", "{:,.0f} g")]


def spec_signature(specs: dict | None, common_tokens: set[str]) -> str:
    specs = specs or {}
    parts = [fmt.format(specs[k]) for k, fmt in SPEC_ORDER if k in specs]
    toks = [t for t in specs.get("model_tokens", []) if t in common_tokens]
    parts += toks[:3]
    return " · ".join(parts)


def assign_models(products: pd.DataFrame, min_size: int = 2) -> pd.DataFrame:
    """Add model_id / model_label to the Product Master. A signature shared by
    fewer than ``min_size`` products in its segment falls back to 'other'."""
    p = products.copy()
    tok_counts = Counter(t for a in p["attributes"] for t in (a or {}).get("model_tokens", []))
    common = {t for t, c in tok_counts.items() if c >= min_size}
    p["model_label"] = p["attributes"].map(lambda a: spec_signature(a, common))
    counts = p.groupby(["segment_id", "model_label"])["product_id"].transform("count")
    p.loc[(p["model_label"] == "") | (counts < min_size), "model_label"] = "other"
    p["model_id"] = [
        seg + "-M" + hashlib.sha1(lbl.encode()).hexdigest()[:6] for seg, lbl in zip(p["segment_id"].astype(str), p["model_label"])
    ]
    return p


def discover(listings: pd.DataFrame, direct_limit: int = DIRECT_LIMIT, sample_size: int = SAMPLE_SIZE) -> DiscoveryResult:
    if len(listings) <= direct_limit:
        return discover_segments(listings)
    return discover_at_scale(listings, sample_size)


def discover_at_scale(listings: pd.DataFrame, sample_size: int = SAMPLE_SIZE) -> DiscoveryResult:
    from dip.storage.vectors import embed

    df = listings.reset_index(drop=True)
    key = [clean_title(t, b) + "\x1f" + (b or "").lower() for t, b in zip(df["title"], df["brand"].where(df["brand"].notna(), None))]
    codes, _ = pd.factorize(pd.Series(key))
    first = pd.Series(np.arange(len(df))).groupby(codes).first().to_numpy()
    reps = df.iloc[first].reset_index(drop=True)          # rep i <-> code i
    method_note = f"representatives={len(reps)}"
    if len(reps) > sample_size:
        rng = np.random.default_rng(0)
        fit_idx = np.sort(rng.choice(len(reps), sample_size, replace=False))
    else:
        fit_idx = np.arange(len(reps))
    fitted = discover_segments(reps.iloc[fit_idx].reset_index(drop=True))
    seg_of_rep = np.empty(len(reps), dtype=object)
    conf_of_rep = np.zeros(len(reps))
    seg_of_rep[fit_idx] = fitted.frame["segment_id"].to_numpy()
    conf_of_rep[fit_idx] = fitted.frame["segment_confidence"].to_numpy()
    rest = np.setdiff1d(np.arange(len(reps)), fit_idx)
    if len(rest):
        texts = (reps["title"].fillna("") + " " + reps["brand"].fillna("")).tolist()
        V = embed(texts)
        segs = fitted.segments["segment_id"].tolist()
        C = np.vstack([V[fit_idx][fitted.frame["segment_id"].to_numpy() == sid].mean(axis=0) for sid in segs])
        C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-12
        for i in range(0, len(rest), 4096):
            block = rest[i:i + 4096]
            S = V[block] @ C.T
            j = S.argmax(axis=1)
            seg_of_rep[block] = np.array(segs, dtype=object)[j]
            conf_of_rep[block] = np.clip(S[np.arange(len(block)), j], 0, 1) * 0.8  # assigned, not fitted
        method_note += f"; fitted on {len(fit_idx)} sample, {len(rest)} assigned by nearest centroid"
    seg_meta = fitted.segments.set_index("segment_id")
    out = df.copy()
    out["segment_id"] = seg_of_rep[codes]
    out["segment_confidence"] = np.round(conf_of_rep[codes], 3)
    out["family_id"] = out["segment_id"].map(seg_meta["family_id"])
    out["family_label"] = out["segment_id"].map(seg_meta["family_label"])
    out["segment_label"] = out["segment_id"].map(seg_meta["segment_label"])
    spec_of_rep = reps["title"].map(extract_specs).to_numpy()
    out["specs"] = spec_of_rep[codes]
    counts = out.groupby("segment_id").agg(listings=("segment_id", "size"), mean_confidence=("segment_confidence", "mean"))
    segments = fitted.segments.drop(columns=["listings", "mean_confidence"]).merge(counts, left_on="segment_id", right_index=True)
    families = (segments.groupby(["family_id", "family_label"], as_index=False)
                .agg(segments=("segment_id", "count"), listings=("listings", "sum")))
    method = {**fitted.method, "scale": method_note}
    return DiscoveryResult(out, segments, families, method)

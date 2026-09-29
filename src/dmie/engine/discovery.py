"""Product Segment Discovery Engine (Module 4).

Discovers the product structure of a market with no hardcoded taxonomy:

1. Families -- K-Means on the text embedding, k chosen by silhouette.
2. Segments within each family -- DBSCAN (the number of segments is
   unknown) with eps chosen automatically from the k-distance curve.
   If DBSCAN leaves too much noise or finds <2 clusters, K-Means with a
   silhouette-selected k is used instead. DBSCAN noise points are
   attached to the nearest segment centroid with a lower
   ``segment_confidence`` rather than dropped.
3. Labels -- class-based TF-IDF (terms that are frequent in the segment
   but rare elsewhere) plus the segment's dominant specs
   (e.g. "brushless · 50000 rpm").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, KMeans
from sklearn.metrics import silhouette_score
from sklearn.neighbors import NearestNeighbors

from dmie.engine.config import section
from dmie.engine.features import feature_matrix, text_matrix, clean_title

SEED = 0


@dataclass
class DiscoveryResult:
    frame: pd.DataFrame        # input + family_id, family_label, segment_id, segment_label, segment_confidence, specs
    segments: pd.DataFrame     # one row per segment
    families: pd.DataFrame     # one row per family
    method: dict               # per family: which algorithm was used


def _best_kmeans(X: np.ndarray, k_range: tuple[int, int]) -> tuple[np.ndarray, int]:
    n = len(X)
    lo, hi = k_range
    hi = min(hi, n - 1)
    if n < 6 or hi < 2:
        return np.zeros(n, dtype=int), 1
    best = (-1.0, None, 1)
    for k in range(max(2, lo), hi + 1):
        labels = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit_predict(X)
        if len(set(labels)) < 2:
            continue
        s = silhouette_score(X, labels, metric="cosine") if n > k else -1
        if s > best[0]:
            best = (s, labels, k)
    if best[1] is None:
        return np.zeros(n, dtype=int), 1
    return best[1], best[2]


def _auto_eps(X: np.ndarray, min_samples: int, quantile: float) -> float:
    k = min(min_samples, len(X) - 1)
    nn = NearestNeighbors(n_neighbors=k + 1, metric="cosine").fit(X)
    dist, _ = nn.kneighbors(X)
    kd = np.sort(dist[:, -1])
    # knee: point of maximum distance to the chord of the sorted k-distance curve
    x = np.linspace(0, 1, len(kd))
    y = (kd - kd.min()) / ((kd.max() - kd.min()) or 1)
    knee = int(np.argmax(x - y)) if len(kd) > 2 else 0
    eps = float(kd[knee]) if knee > 0 else float(np.quantile(kd, quantile))
    return max(eps, 1e-3)


def cluster_segments(X: np.ndarray, cfg: dict) -> tuple[np.ndarray, np.ndarray, str]:
    """Return (labels, confidence, method). Labels are 0..k-1, no noise."""
    n = len(X)
    min_size = cfg.get("min_cluster_size", 3)
    if n < max(2 * min_size, 6):
        return np.zeros(n, dtype=int), np.ones(n), "single"
    eps = _auto_eps(X, min_size, cfg.get("eps_quantile", 0.5))
    labels = DBSCAN(eps=eps, min_samples=min_size, metric="cosine").fit_predict(X)
    n_clusters = len(set(labels) - {-1})
    noise = float((labels == -1).mean())
    method = f"dbscan(eps={eps:.3f})"
    k_lo, k_hi = cfg.get("kmeans_k_range", [2, 12])
    k_cap = max(2, min(k_hi, int(np.sqrt(n / 2))))  # segment count scales with data size
    if n_clusters < 2 or noise > cfg.get("max_noise_fraction", 0.5):
        labels, k = _best_kmeans(X, (k_lo, k_cap))
        method = f"kmeans(k={k})"
    uniq = sorted(set(labels) - {-1})
    remap = {c: i for i, c in enumerate(uniq)}
    if not uniq:
        return np.zeros(n, dtype=int), np.ones(n), "single"
    centroids = np.vstack([X[labels == c].mean(axis=0) for c in uniq])
    cn = centroids / (np.linalg.norm(centroids, axis=1, keepdims=True) + 1e-12)
    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)
    sims = Xn @ cn.T
    out = np.array([remap[c] if c != -1 else int(np.argmax(sims[i])) for i, c in enumerate(labels)])
    conf = np.clip(sims[np.arange(n), out], 0, 1)
    conf = np.where(labels == -1, conf * 0.6, conf)  # noise points attached with reduced confidence
    out = _merge_similar(out, cn, cfg.get("merge_similarity", 0.75), k_cap)
    return out, conf, method


def _merge_similar(labels: np.ndarray, centroids: np.ndarray, threshold: float, max_k: int) -> np.ndarray:
    """Merge segments whose centroids are near-identical, and cap the
    count by repeatedly merging the most similar pair. Keeps segment
    lists readable without hand-tuning per market."""
    groups = {i: {i} for i in range(len(centroids))}
    cents = {i: centroids[i] * max((labels == i).sum(), 1) for i in groups}
    sizes = {i: max(int((labels == i).sum()), 1) for i in groups}
    while len(groups) > 1:
        keys = list(groups)
        C = np.vstack([cents[k] / sizes[k] for k in keys])
        C = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-12)
        S = C @ C.T
        np.fill_diagonal(S, -1)
        a, b = np.unravel_index(np.argmax(S), S.shape)
        if S[a, b] < threshold and len(groups) <= max_k:
            break
        ka, kb = keys[a], keys[b]
        groups[ka] |= groups.pop(kb)
        cents[ka] = cents[ka] + cents.pop(kb)
        sizes[ka] += sizes.pop(kb)
    remap = {}
    for new, (_, members) in enumerate(sorted(groups.items())):
        for m in members:
            remap[m] = new
    return np.array([remap[x] for x in labels])


def label_clusters(texts: list[str], labels: np.ndarray, n_terms: int = 3) -> dict[int, list[str]]:
    """Class-based TF-IDF: distinctive terms per cluster."""
    from sklearn.feature_extraction.text import CountVectorizer

    uniq = sorted(set(labels))
    docs = [" ".join(t for t, lab in zip(texts, labels) if lab == c) for c in uniq]
    try:
        cv = CountVectorizer(ngram_range=(1, 2), stop_words="english", token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b", min_df=1)
        counts = cv.fit_transform(docs).toarray().astype(float)
    except ValueError:
        return {c: [] for c in uniq}
    vocab = np.array(cv.get_feature_names_out())
    tf = counts / (counts.sum(axis=1, keepdims=True) + 1e-12)
    avg_words = counts.sum() / max(len(uniq), 1)
    idf = np.log(1 + avg_words / (counts.sum(axis=0) + 1))
    ctfidf = tf * idf
    out = {}
    for i, c in enumerate(uniq):
        order = np.argsort(-ctfidf[i])
        terms: list[str] = []
        for j in order:
            term = vocab[j]
            if any(term in t or t in term for t in terms):
                continue
            terms.append(term)
            if len(terms) >= n_terms:
                break
        out[c] = terms
    return out


def _dominant_specs(specs: pd.Series, titles: pd.Series) -> str:
    parts = []
    for key, unit in (("rpm", "rpm"), ("watt", "W"), ("pack", "pcs")):
        vals = [s[key] for s in specs if key in s]
        if len(vals) >= max(2, 0.4 * len(specs)):
            v = Counter(vals).most_common(1)[0][0]
            parts.append(f"{v:,.0f} {unit}")
    low = titles.fillna("").str.lower()
    for flag in ("brushless", "brushed", "cordless", "wireless", "disposable", "stainless"):
        if low.str.contains(flag).mean() >= 0.5:
            parts.insert(0, flag)
    return " · ".join(parts[:3])


def discover_segments(frame: pd.DataFrame) -> DiscoveryResult:
    cfg = section("discovery")
    df = frame.reset_index(drop=True).copy()
    n = len(df)
    if n == 0:
        empty = pd.DataFrame(columns=["segment_id", "segment_label", "family_id", "family_label", "listings"])
        for c in ("family_id", "family_label", "segment_id", "segment_label", "segment_confidence", "specs"):
            df[c] = pd.Series(dtype="object")
        return DiscoveryResult(df, empty, empty, {})

    X, _, _, specs = feature_matrix(df, cfg.get("svd_components", 40))
    texts = [clean_title(t, b) for t, b in zip(df["title"], df["brand"])]
    text_only, _, _ = text_matrix(texts, cfg.get("svd_components", 40))

    f_lo, f_hi = cfg.get("family_k_range", [2, 6])
    f_hi = min(f_hi, int(round(np.sqrt(n / 10))))
    fam_labels, _ = _best_kmeans(text_only, (f_lo, f_hi)) if n >= 30 and f_hi >= 2 else (np.zeros(n, dtype=int), 1)
    fam_terms = label_clusters(texts, fam_labels, 2)

    seg_ids = np.empty(n, dtype=object)
    seg_conf = np.zeros(n)
    method: dict = {}
    for fam in sorted(set(fam_labels)):
        idx = np.flatnonzero(fam_labels == fam)
        labels, conf, how = cluster_segments(X[idx], cfg)
        method[f"F{fam}"] = how
        for local, i in zip(labels, idx):
            seg_ids[i] = f"F{fam}-S{local}"
        seg_conf[idx] = conf

    seg_codes = pd.Series(seg_ids).astype("category").cat.codes.to_numpy()
    seg_terms = label_clusters(texts, seg_codes, cfg.get("label_terms", 3))
    df["specs"] = specs.values
    df["family_id"] = [f"F{f}" for f in fam_labels]
    df["family_label"] = [" / ".join(fam_terms.get(f, [])) or "general" for f in fam_labels]
    df["segment_id"] = seg_ids
    df["segment_confidence"] = seg_conf.round(3)

    seg_rows = []
    labels_map = {}
    for code, seg in zip(seg_codes, seg_ids):
        labels_map[seg] = code
    for seg, code in labels_map.items():
        m = df["segment_id"] == seg
        base = ", ".join(seg_terms.get(code, [])) or "general"
        spec_txt = _dominant_specs(df.loc[m, "specs"], df.loc[m, "title"])
        label = f"{base} ({spec_txt})" if spec_txt else base
        seg_rows.append({
            "segment_id": seg,
            "segment_label": label,
            "family_id": df.loc[m, "family_id"].iloc[0],
            "family_label": df.loc[m, "family_label"].iloc[0],
            "listings": int(m.sum()),
            "top_terms": seg_terms.get(code, []),
            "dominant_specs": spec_txt,
            "mean_confidence": float(df.loc[m, "segment_confidence"].mean()),
        })
    segments = pd.DataFrame(seg_rows).sort_values(["family_id", "listings"], ascending=[True, False]).reset_index(drop=True)
    df["segment_label"] = df["segment_id"].map(segments.set_index("segment_id")["segment_label"])
    families = (segments.groupby(["family_id", "family_label"], as_index=False)
                .agg(segments=("segment_id", "count"), listings=("listings", "sum")))
    return DiscoveryResult(df, segments, families, method)

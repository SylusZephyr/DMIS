"""Market Universe helpers: industry-branch assignment and 3D layouts.

* ``assign_branch`` places a market under a branch of the industry
  (config/platform/industry_branches.yaml) by TF-IDF similarity of its
  titles to each branch's prototype phrases -- no hardcoded products.
* ``galaxy_layout`` gives every product a deterministic 3D position:
  segments sit on a Fibonacci sphere, products orbit their segment centre,
  offset by a PCA of their text embedding (similar products sit close).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from dip.settings import PROJECT_ROOT
from dip.storage.vectors import embed

BRANCH_CONFIG = PROJECT_ROOT / "config" / "platform" / "industry_branches.yaml"


@lru_cache(maxsize=1)
def branches() -> dict:
    return yaml.safe_load(Path(BRANCH_CONFIG).read_text(encoding="utf-8"))


def assign_branch(titles: list[str]) -> tuple[str, dict]:
    cfg = branches()["branches"]
    names = list(cfg)
    protos = [" ".join(v) for v in cfg.values()]
    corpus = " ".join(t for t in titles if isinstance(t, str))[:200000].lower()
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True).fit(protos + [corpus])
    sims = cosine_similarity(vec.transform([corpus]), vec.transform(protos)).ravel()
    scores = {n: round(float(s), 4) for n, s in zip(names, sims)}
    return names[int(np.argmax(sims))], scores


def fibonacci_sphere(n: int, radius: float = 1.0) -> np.ndarray:
    if n <= 0:
        return np.zeros((0, 3))
    if n == 1:
        return np.zeros((1, 3))
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return radius * np.column_stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)])


def galaxy_layout(products: pd.DataFrame, radius: float = 60.0) -> pd.DataFrame:
    p = products.copy()
    segs = list(p["segment_id"].astype(str).value_counts().index)
    centres = dict(zip(segs, fibonacci_sphere(len(segs), radius)))
    xyz = np.zeros((len(p), 3))
    V = embed((p["title"].fillna("") + " " + p["brand"].fillna("")).tolist()) if len(p) else np.zeros((0, 256))
    for seg in segs:
        idx = np.flatnonzero(p["segment_id"].astype(str).to_numpy() == seg)
        spread = 4 + 3 * np.log1p(len(idx))
        if len(idx) >= 3:
            X = V[idx] - V[idx].mean(axis=0)
            _, _, vt = np.linalg.svd(X, full_matrices=False)
            off = X @ vt[:3].T
            off = off / (np.abs(off).max() or 1)
        else:
            off = fibonacci_sphere(len(idx), 0.5)
        xyz[idx] = centres[seg] + off * spread
    p["gx"], p["gy"], p["gz"] = xyz[:, 0].round(3), xyz[:, 1].round(3), xyz[:, 2].round(3)
    return p

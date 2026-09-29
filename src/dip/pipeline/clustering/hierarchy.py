"""Model and Variant levels of the product hierarchy.

    Category -> Family -> Segment -> Model -> Variant -> Listing

* **Model** -- first the v1 specification signature (``assign_models``); products
  whose titles carry no shared signature are grouped by title similarity inside
  their segment (average-linkage, cosine distance threshold from
  ``config/platform/hierarchy.yaml``) and labelled with the group's distinctive
  terms. ``model_basis`` records which rule produced each model: spec | text | none.
* **Variant** -- one Product Master entity (the listings of one real product).
  ``variant_label`` is what distinguishes it from the other products of its
  model: model tokens, spec values or words the rest of the model lacks.

Nothing is hardcoded: every label is a term or value found in the data.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from dip.settings import PROJECT_ROOT
from dmie.engine.discovery import label_clusters
from dmie.engine.features import clean_title

CONFIG = PROJECT_ROOT / "config" / "platform" / "hierarchy.yaml"
_WORD = re.compile(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]")
_FILLER = {"pcs", "pc", "pack", "count", "ct", "set", "piece", "pieces", "dental", "teeth", "tooth", "model", "and", "with", "for"}


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))


def _model_id(seg: str, label: str) -> str:
    return seg + "-M" + hashlib.sha1(label.encode()).hexdigest()[:6]


def text_models(products: pd.DataFrame) -> pd.DataFrame:
    """Group 'other' products into text models inside each segment."""
    cfg = config()["text_models"]
    p = products.copy()
    p["model_basis"] = np.where(p["model_label"] == "other", "none", "spec")
    if not cfg.get("enabled", True):
        return p
    from sklearn.cluster import AgglomerativeClustering

    for seg, g in p[p["model_label"] == "other"].groupby("segment_id", sort=False):
        if len(g) < max(2, cfg["min_size"]) or len(g) > cfg["max_group"]:
            continue
        texts = [clean_title(t, b if isinstance(b, str) else None) for t, b in zip(g["title"], g["brand"])]
        try:
            X = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True, min_df=1).fit_transform(texts)
        except ValueError:
            continue
        labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                         distance_threshold=cfg["distance_threshold"]).fit_predict(X.toarray())
        sizes = Counter(labels)
        keep = np.array([sizes[c] >= cfg["min_size"] for c in labels])
        if not keep.any():
            continue
        terms = label_clusters(texts, labels, n_terms=cfg["label_terms"])
        names = {c: " · ".join(t) for c, t in terms.items() if t}
        idx = g.index[keep]
        lab = [names.get(c) for c in labels[keep]]
        ok = [x is not None for x in lab]
        idx = idx[ok]
        lab = [x for x in lab if x is not None]
        p.loc[idx, "model_label"] = lab
        p.loc[idx, "model_basis"] = "text"
    p["model_id"] = [_model_id(str(s), lab) for s, lab in zip(p["segment_id"], p["model_label"])]
    return p


def _tokens(title: str, brand: str | None, attrs: dict | None) -> list[str]:
    words = _WORD.findall(clean_title(title, brand))
    out = [w for w in words if w not in ENGLISH_STOP_WORDS and w not in _FILLER and len(w) > 1]
    for t in (attrs or {}).get("model_tokens", []):
        out.append(str(t).lower())
    return list(dict.fromkeys(out))


def _is_code(t: str) -> bool:
    return any(ch.isdigit() for ch in t)


def assign_variants(products: pd.DataFrame) -> pd.DataFrame:
    """variant_id (= product_id) and variant_label for every product."""
    cfg = config()["variants"]
    p = products.copy()
    attrs = p["attributes"] if "attributes" in p else pd.Series([{}] * len(p), index=p.index)
    toks = [_tokens(t or "", b if isinstance(b, str) else None, a if isinstance(a, dict) else None)
            for t, b, a in zip(p["title"], p["brand"], attrs)]
    labels = [""] * len(p)
    pos = {ix: i for i, ix in enumerate(p.index)}
    for mid, g in p.groupby("model_id", sort=False):
        rows = [pos[ix] for ix in g.index]
        other = (g["model_label"] == "other").all()
        df = Counter(t for r in rows for t in set(toks[r]))
        n = len(rows)
        model_words = set(str(g["model_label"].iat[0]).replace("·", " ").split())
        for r in rows:
            cand = [t for t in toks[r] if t not in model_words and (other or n == 1 or df[t] / n < cfg["common_share"])]
            cand.sort(key=lambda t: (not _is_code(t), df[t], toks[r].index(t)))
            labels[r] = " · ".join(cand[: cfg["max_tokens"]])
    p["variant_id"] = p["product_id"]
    p["variant_label"] = [lab or str(b or "")[:40] or "base" for lab, b in zip(labels, p["brand"])]
    return p


def hierarchy_tree(products: pd.DataFrame, segments: pd.DataFrame, listings: pd.DataFrame | None = None,
                   market: str | None = None, listing_limit: int = 20) -> dict:
    """Nested Category -> Family -> Segment -> Model -> Variant -> Listing tree with metrics."""
    def num(v):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v) else float(v)

    lst_by_product = {}
    if listings is not None and len(listings):
        for pid, g in listings.groupby("product_id", sort=False):
            lst_by_product[pid] = [{"kind": "Listing", "id": str(r.id), "label": str(r.id), "price": num(r.price),
                                    "sales": num(r.sales), "best": bool(getattr(r, "is_best_listing", False))}
                                   for r in g.head(listing_limit).itertuples()]
    seg_meta = segments.set_index("segment_id") if len(segments) else pd.DataFrame()
    fams: dict = {}
    for (fam, seg), sg in products.groupby(["family_id", "segment_id"], sort=False):
        s = seg_meta.loc[seg] if seg in seg_meta.index else {}
        models = []
        for mid, mg in sg.groupby("model_id", sort=False):
            variants = [{"kind": "Variant", "id": r.product_id, "label": r.variant_label, "title": r.title,
                         "brand": r.brand, "price": num(r.price), "monthly_revenue": num(r.monthly_revenue),
                         "opportunity": num(r.opportunity_score), "confidence": num(getattr(r, "confidence_score", None)),
                         "children": lst_by_product.get(r.product_id, [])}
                        for r in mg.sort_values("monthly_revenue", ascending=False, na_position="last").itertuples()]
            models.append({"kind": "Model", "id": mid, "label": mg["model_label"].iat[0],
                           "basis": mg["model_basis"].iat[0] if "model_basis" in mg else None,
                           "products": len(mg), "monthly_revenue": num(mg["monthly_revenue"].sum(min_count=1)),
                           "children": variants})
        models.sort(key=lambda m: -(m["monthly_revenue"] or 0))
        node = {"kind": "Segment", "id": seg, "label": s.get("segment_label", seg) if len(s) else seg,
                "products": len(sg), "monthly_revenue": num(sg["monthly_revenue"].sum(min_count=1)),
                "opportunity": num(s.get("opportunity_score")) if len(s) else None,
                "confidence": num(s.get("confidence_score")) if len(s) else None, "children": models}
        f = fams.setdefault(fam, {"kind": "Family", "id": fam, "label": s.get("family_label", fam) if len(s) else fam,
                                  "children": []})
        f["children"].append(node)
    for f in fams.values():
        f["products"] = sum(c["products"] for c in f["children"])
        f["monthly_revenue"] = sum(c["monthly_revenue"] or 0 for c in f["children"]) or None
        f["children"].sort(key=lambda c: -(c["monthly_revenue"] or 0))
    children = sorted(fams.values(), key=lambda c: -(c["monthly_revenue"] or 0))
    return {"kind": "Category", "id": market, "label": market, "products": int(len(products)),
            "monthly_revenue": sum(c["monthly_revenue"] or 0 for c in children) or None, "children": children}

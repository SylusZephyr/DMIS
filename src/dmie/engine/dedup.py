"""Product Deduplication Engine (Module 5) -- the Product Identity Graph.

100 listings != 100 products. Listings are nodes; an edge joins two
listings when their weighted similarity clears ``match_threshold``:

    title (char TF-IDF cosine) · brand · specification · price · image

Hard vetoes (never the same product, whatever the score):
* conflicting specs (different pack count, rpm, wattage, volume, ...)
* prices differing by more than ``price_ratio_limit``
* different known brands

Candidate pairs come from nearest-neighbour search plus shared image /
native id blocks, so the work is ~O(n log n), not O(n^2). Groups are
formed with union-find that refuses to merge two groups if any known
pair across them is vetoed -- this prevents transitive chaining
(A~B, B~C, but A≠C) from collapsing distinct products.

Output: a Product Master (one row per product) and a listing->product map.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

from dmie.engine.config import section
from dmie.engine.features import clean_title, extract_specs, image_id, spec_conflict


@dataclass
class DedupResult:
    frame: pd.DataFrame          # listings + product_id, is_best_listing
    products: pd.DataFrame       # Product Master
    edges: pd.DataFrame          # scored candidate pairs (a, b, score, decision, reason)
    graph: nx.Graph

    def summary(self) -> dict:
        return {
            "listings": int(len(self.frame)),
            "products": int(len(self.products)),
            "multi_listing_products": int((self.products["listing_count"] > 1).sum()) if len(self.products) else 0,
            "candidate_pairs": int(len(self.edges)),
            "matched_pairs": int((self.edges["decision"] == "match").sum()) if len(self.edges) else 0,
        }


def _norm_brand(b) -> str | None:
    if not isinstance(b, str) or not b.strip():
        return None
    return "".join(ch for ch in b.lower() if ch.isalnum()) or None


def brand_similarity(a: str | None, b: str | None) -> float:
    if a is None or b is None:
        return 0.5
    if a == b or a in b or b in a:
        return 1.0
    return 0.0


def price_similarity(a: float, b: float) -> float:
    if not (a and b) or np.isnan(a) or np.isnan(b) or a <= 0 or b <= 0:
        return 0.5
    return min(a, b) / max(a, b)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.members = {i: {i} for i in range(n)}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.members[ra] |= self.members.pop(rb)


def _candidate_pairs(tfidf, frame: pd.DataFrame, k: int) -> set[tuple[int, int]]:
    n = tfidf.shape[0]
    pairs: set[tuple[int, int]] = set()
    if n < 2:
        return pairs
    nn = NearestNeighbors(n_neighbors=min(k + 1, n), metric="cosine").fit(tfidf)
    _, idx = nn.kneighbors(tfidf)
    for i, row in enumerate(idx):
        for j in row[1:]:
            pairs.add((min(i, j), max(i, j)))
    for key in ("_image_id", "id"):
        for _, group in frame.groupby(key).groups.items():
            g = list(group)
            if 1 < len(g) <= 50:
                for a in range(len(g)):
                    for b in range(a + 1, len(g)):
                        pairs.add((g[a], g[b]))
    return pairs


def resolve_products(frame: pd.DataFrame) -> DedupResult:
    cfg = section("dedup")
    w = cfg.get("weights", {})
    threshold = cfg.get("match_threshold", 0.72)
    ratio_limit = cfg.get("price_ratio_limit", 1.6)

    df = frame.reset_index(drop=True).copy()
    n = len(df)
    if "specs" not in df:
        df["specs"] = df["title"].map(extract_specs)
    df["_brand"] = df["brand"].map(_norm_brand)
    df["_image_id"] = df["image"].map(image_id)
    texts = [clean_title(t, b) or "untitled" for t, b in zip(df["title"], df["brand"])]
    if n:
        tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True).fit_transform(texts)
    pairs = _candidate_pairs(tfidf, df, 10) if n else set()

    rows = []
    vetoed: set[tuple[int, int]] = set()
    recs = df[["id", "_brand", "specs", "price", "_image_id"]].to_dict("records")
    tfidf = tfidf.tocsr() if n else None
    for a, b in sorted(pairs):
        ra, rb = recs[a], recs[b]
        same_native = ra["id"] == rb["id"] and pd.notna(ra["id"])
        title_sim = float(tfidf[a].multiply(tfidf[b]).sum())
        brand_sim = brand_similarity(ra["_brand"], rb["_brand"])
        conflicts = spec_conflict(ra["specs"], rb["specs"])
        shared = set(ra["specs"]) & set(rb["specs"]) - {"model_tokens"}
        spec_sim = 0.0 if conflicts else (1.0 if shared else 0.5)
        if "model_tokens" in ra["specs"] and "model_tokens" in rb["specs"]:
            if set(ra["specs"]["model_tokens"]) & set(rb["specs"]["model_tokens"]):
                spec_sim = max(spec_sim, 1.0) if not conflicts else spec_sim
        p_sim = price_similarity(ra["price"], rb["price"])
        same_img = ra["_image_id"] is not None and ra["_image_id"] == rb["_image_id"]
        img_sim = 1.0 if same_img else (0.3 if ra["_image_id"] and rb["_image_id"] else 0.5)
        score = (w.get("title", .45) * title_sim + w.get("brand", .15) * brand_sim + w.get("spec", .15) * spec_sim
                 + w.get("price", .10) * p_sim + w.get("image", .15) * img_sim)
        reason = []
        if same_native:
            decision, score, reason = "match", 1.0, ["same native id"]
        elif conflicts:
            decision, reason = "veto", [f"spec conflict: {', '.join(conflicts)}"]
        elif p_sim < 1 / ratio_limit and p_sim != 0.5:
            decision, reason = "veto", [f"price ratio {1 / p_sim:.2f} > {ratio_limit}"]
        elif brand_sim == 0.0:
            decision, reason = "veto", ["different brands"]
        elif score >= threshold:
            decision = "match"
        else:
            decision = "no_match"
        if not reason:
            reason = [f"title={title_sim:.2f} brand={brand_sim:.1f} spec={spec_sim:.1f} price={p_sim:.2f} image={'same' if same_img else 'diff'}"]
        if decision == "veto":
            vetoed.add((a, b))
        rows.append({"a": a, "b": b, "listing_a": ra["id"], "listing_b": rb["id"], "score": round(score, 4),
                     "decision": decision, "reason": "; ".join(reason)})
    edges = pd.DataFrame(rows, columns=["a", "b", "listing_a", "listing_b", "score", "decision", "reason"])

    uf = _UnionFind(n)
    for _, e in edges[edges["decision"] == "match"].sort_values("score", ascending=False).iterrows():
        ra, rb = uf.find(int(e["a"])), uf.find(int(e["b"]))
        if ra == rb:
            continue
        ma, mb = uf.members[ra], uf.members[rb]
        if any((min(x, y), max(x, y)) in vetoed for x in ma for y in mb):
            continue
        uf.union(ra, rb)

    graph = nx.Graph()
    graph.add_nodes_from(range(n))
    for _, e in edges[edges["decision"] == "match"].iterrows():
        if uf.find(int(e["a"])) == uf.find(int(e["b"])):
            graph.add_edge(int(e["a"]), int(e["b"]), weight=float(e["score"]))

    roots = [uf.find(i) for i in range(n)]
    group_ids = {}
    for r in set(roots):
        members = sorted(df.loc[list(uf.members[r]), "id"].astype(str))
        group_ids[r] = "P" + hashlib.sha1("|".join(members).encode()).hexdigest()[:12]
    df["product_id"] = [group_ids[r] for r in roots]
    products, best = build_product_master(df)
    df["is_best_listing"] = df.index.isin(best)
    return DedupResult(df.drop(columns=["_brand", "_image_id"]), products, edges, graph)


def _mode(s: pd.Series):
    s = s.dropna()
    return s.mode().iloc[0] if len(s) else None


def build_product_master(df: pd.DataFrame) -> tuple[pd.DataFrame, list[int]]:
    """One row per product. Sums only over listings with known values and
    reports coverage, so missing sales are never silently treated as 0."""
    rows = []
    best_idx: list[int] = []
    for pid, g in df.groupby("product_id", sort=False):
        known_sales = g["sales"].dropna()
        if len(known_sales):
            best = known_sales.idxmax()
            basis = "sales"
        elif g["reviews"].notna().any():
            best = g["reviews"].idxmax()
            basis = "reviews"
        else:
            best = g.index[0]
            basis = "first_listing"
        best_idx.append(best)
        seg = _mode(g["segment_id"]) if "segment_id" in g else None
        seg_label = _mode(g["segment_label"]) if "segment_label" in g else None
        specs: dict = {}
        for s in g["specs"]:
            for k, v in (s or {}).items():
                specs.setdefault(k, v)
        rated = g.dropna(subset=["rating"])
        wts = rated["reviews"].fillna(1).clip(lower=1) if len(rated) else None
        rows.append({
            "product_id": pid,
            "title": df.at[best, "title"],
            "brand": _mode(g["brand"]),
            "product_type": seg_label,
            "segment_id": seg,
            "family_id": _mode(g["family_id"]) if "family_id" in g else None,
            "attributes": specs,
            "listing_count": int(len(g)),
            "listing_ids": list(g["id"].astype(str)),
            "best_listing": df.at[best, "id"],
            "best_listing_basis": basis,
            "image": df.at[best, "image"],
            "url": df.at[best, "url"],
            "price_min": float(g["price"].min()) if g["price"].notna().any() else None,
            "price_max": float(g["price"].max()) if g["price"].notna().any() else None,
            "price_median": float(g["price"].median()) if g["price"].notna().any() else None,
            "total_sales": float(known_sales.sum()) if len(known_sales) else None,
            "revenue": float(g["revenue"].dropna().sum()) if g["revenue"].notna().any() else None,
            "sales_coverage": float(g["sales"].notna().mean()),
            "rating": float(np.average(rated["rating"], weights=wts)) if len(rated) else None,
            "reviews": float(g["reviews"].dropna().sum()) if g["reviews"].notna().any() else None,
            "sellers": int(g["seller"].dropna().nunique()) if g["seller"].notna().any() else None,
            "data_confidence": float(g["data_confidence"].mean()) if "data_confidence" in g else None,
        })
    return pd.DataFrame(rows), best_idx

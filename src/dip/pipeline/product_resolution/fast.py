"""Product resolution at scale (10^5-10^6 current listings).

Same rules as ``dmie.engine.dedup.resolve_products`` -- same features,
weights, thresholds, vetoes, anti-chaining union-find and product-id
formula -- restructured so the work grows roughly linearly:

1. **Exact collapse.** Listings with identical cleaned title, brand, image
   and specs whose prices all sit within the price-ratio limit are merged
   up front. Under the v1 rules every pair inside such a group scores
   >= 0.88 with no possible veto, so they always end in one product; the
   collapse changes no decision, it only removes pairs.
2. **Partitioned candidates.** Exact TF-IDF cosine k-NN, computed inside
   each discovered segment in bounded row blocks (never an n x n matrix),
   plus same-image blocks across the whole market.
3. **Vectorised scoring.** Title/brand/spec/price/image similarities and
   the veto rules are array operations; only model-token overlap (rare) is
   checked in Python.
4. **Vectorised Product Master** with the v1 column contract.
"""

from __future__ import annotations

import hashlib

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from dmie.engine.config import section
from dmie.engine.dedup import DedupResult, _norm_brand, _UnionFind
from dmie.engine.features import clean_title, extract_specs, image_id

SPEC_KEYS = ("rpm", "watt", "volt", "pack", "ml", "gram", "nm")
K = 10


def _spec_matrix(specs: pd.Series) -> np.ndarray:
    M = np.full((len(specs), len(SPEC_KEYS)), np.nan)
    for i, s in enumerate(specs):
        for j, k in enumerate(SPEC_KEYS):
            if s and k in s:
                M[i, j] = s[k]
    return M


def _collapse(df: pd.DataFrame, ratio_limit: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (group code per listing, representative row per group)."""
    spec_sig = df["specs"].map(lambda s: repr(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in (s or {}).items())))
    key = df["_text"] + "\x1f" + df["_brand"].fillna("") + "\x1f" + df["_image_id"].fillna("") + "\x1f" + spec_sig
    codes, _ = pd.factorize(key)
    price = df["price"].to_numpy(dtype=float)
    g = pd.DataFrame({"c": codes, "p": price})
    stats = g.groupby("c")["p"].agg(["min", "max"])
    ok = (stats["min"] > 0) & (stats["max"] / stats["min"] <= ratio_limit)  # all pairs pass the price veto
    # groups whose prices are too spread (or unknown) are not collapsed: each row is its own group
    collapsible = ok.reindex(range(codes.max() + 1), fill_value=False).to_numpy()
    grp = np.where(collapsible[codes], codes, codes.max() + 1 + np.arange(len(codes)))
    grp, _ = pd.factorize(grp)
    rep = pd.Series(np.arange(len(df))).groupby(grp).first().to_numpy()
    return grp, rep


def _knn_pairs(X, members: np.ndarray, k: int, block: int = 2048) -> list[np.ndarray]:
    out = []
    n = len(members)
    if n < 2:
        return out
    kk = min(k, n - 1)
    Xs = X[members]
    for i in range(0, n, block):
        S = (Xs[i:i + block] @ Xs.T).toarray()
        rows = np.arange(S.shape[0])
        S[rows, i + rows] = -1.0
        nb = np.argpartition(-S, kk - 1, axis=1)[:, :kk]
        a = np.repeat(members[i + rows], kk)
        b = members[nb.ravel()]
        out.append(np.column_stack([np.minimum(a, b), np.maximum(a, b)]))
    return out


def resolve_at_scale(frame: pd.DataFrame) -> DedupResult:
    cfg = section("dedup")
    w = cfg.get("weights", {})
    thr = cfg.get("match_threshold", 0.72)
    ratio_limit = cfg.get("price_ratio_limit", 1.6)

    df = frame.reset_index(drop=True).copy()
    if "specs" not in df:
        df["specs"] = df["title"].map(extract_specs)
    df["_brand"] = df["brand"].map(_norm_brand)
    df["_image_id"] = df["image"].map(image_id)
    df["_text"] = [clean_title(t, b) or "untitled" for t, b in zip(df["title"], df["brand"].where(df["brand"].notna(), None))]
    grp, rep = _collapse(df, ratio_limit)
    R = df.iloc[rep].reset_index(drop=True)
    n = len(R)

    X = normalize(TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, dtype=np.float32)
                  .fit_transform(R["_text"])).tocsr()
    parts = []
    seg = R["segment_id"].astype(str).to_numpy() if "segment_id" in R else np.zeros(n, dtype=object)
    for s in pd.unique(seg):
        parts += _knn_pairs(X, np.flatnonzero(seg == s), K)
    for _, idx in R.groupby("_image_id").groups.items():
        idx = np.asarray(idx)
        if 1 < len(idx) <= 50:
            a, b = np.triu_indices(len(idx), 1)
            parts.append(np.column_stack([idx[a], idx[b]]))
    pairs = np.unique(np.vstack(parts), axis=0) if parts else np.zeros((0, 2), dtype=int)
    pairs = pairs[pairs[:, 0] != pairs[:, 1]]
    a, b = pairs[:, 0], pairs[:, 1]

    title_sim = np.asarray(X[a].multiply(X[b]).sum(axis=1)).ravel() if len(pairs) else np.zeros(0)
    ba, bb = R["_brand"].to_numpy(dtype=object)[a], R["_brand"].to_numpy(dtype=object)[b]
    missing = pd.isna(ba) | pd.isna(bb)
    brand_sim = np.where(missing, 0.5, 0.0)
    known = np.flatnonzero(~missing)
    same = np.array([x == y or x in y or y in x for x, y in zip(ba[known], bb[known])], dtype=bool)
    brand_sim[known[same]] = 1.0

    S = _spec_matrix(R["specs"])
    sa, sb = S[a], S[b]
    both = ~np.isnan(sa) & ~np.isnan(sb)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel = np.abs(sa - sb) / np.maximum(sa, sb)
    conflict_any = (both & (np.maximum(sa, sb) > 0) & (rel > 0.05)).any(axis=1)
    shared_any = both.any(axis=1)
    spec_sim = np.where(conflict_any, 0.0, np.where(shared_any, 1.0, 0.5))
    toks = R["specs"].map(lambda s: frozenset((s or {}).get("model_tokens", [])) or None).to_numpy(dtype=object)
    tcheck = np.flatnonzero(~conflict_any & pd.notna(toks[a]) & pd.notna(toks[b]))
    if len(tcheck):
        overlap = np.array([bool(toks[a[i]] & toks[b[i]]) for i in tcheck])
        spec_sim[tcheck[overlap]] = 1.0

    pa, pb = R["price"].to_numpy(dtype=float)[a], R["price"].to_numpy(dtype=float)[b]
    valid = (pa > 0) & (pb > 0) & ~np.isnan(pa) & ~np.isnan(pb)
    p_sim = np.where(valid, np.minimum(pa, pb) / np.where(valid, np.maximum(pa, pb), 1), 0.5)
    ia, ib = R["_image_id"].to_numpy(dtype=object)[a], R["_image_id"].to_numpy(dtype=object)[b]
    has_a, has_b = pd.notna(ia), pd.notna(ib)
    same_img = has_a & (ia == ib)
    img_sim = np.where(same_img, 1.0, np.where(has_a & has_b, 0.3, 0.5))
    score = (w.get("title", .45) * title_sim + w.get("brand", .15) * brand_sim + w.get("spec", .15) * spec_sim
             + w.get("price", .10) * p_sim + w.get("image", .15) * img_sim)
    ida, idb = R["id"].to_numpy(dtype=object)[a], R["id"].to_numpy(dtype=object)[b]
    same_native = pd.notna(ida) & (ida == idb)
    price_veto = (p_sim < 1 / ratio_limit) & (p_sim != 0.5)
    decision = np.select(
        [same_native, conflict_any, price_veto, brand_sim == 0.0, score >= thr],
        ["match", "veto", "veto", "veto", "match"], "no_match")
    score = np.where(same_native, 1.0, score)

    uf = _UnionFind(n)
    vetoed = set(map(tuple, pairs[decision == "veto"].tolist()))
    order = np.flatnonzero(decision == "match")
    order = order[np.argsort(-score[order], kind="stable")]
    for i in order:
        ra, rb = uf.find(int(a[i])), uf.find(int(b[i]))
        if ra == rb:
            continue
        ma, mb = uf.members[ra], uf.members[rb]
        if vetoed and any((min(x, y), max(x, y)) in vetoed for x in ma for y in mb):
            continue
        uf.union(ra, rb)

    root_of_rep = np.array([uf.find(i) for i in range(n)])
    root = root_of_rep[grp]
    ids = df["id"].astype(str).to_numpy()
    order_ids = pd.DataFrame({"r": root, "id": ids}).sort_values(["r", "id"])
    pid_of_root = {r: "P" + hashlib.sha1("|".join(g).encode()).hexdigest()[:12] for r, g in order_ids.groupby("r")["id"]}
    df["product_id"] = [pid_of_root[r] for r in root]

    edges = pd.DataFrame({"a": rep[a], "b": rep[b], "listing_a": ida, "listing_b": idb, "score": np.round(score, 4),
                          "decision": decision,
                          "reason": np.where(decision == "match", "vectorised score >= threshold",
                                             np.where(decision == "veto", "veto (spec/price/brand)", "below threshold"))})
    products, best = build_product_master_fast(df)
    df["is_best_listing"] = df.index.isin(best)
    graph = nx.Graph()
    return DedupResult(df.drop(columns=["_brand", "_image_id", "_text"]), products, edges, graph)


def _mode_by(df: pd.DataFrame, key: str, col: str) -> pd.Series:
    """Per-group mode with v1's tie-break (Series.mode sorts ties ascending)."""
    d = df[[key, col]].dropna()
    if d.empty:
        return pd.Series(dtype=object)
    c = d.groupby([key, col]).size().rename("n").reset_index()
    c = c.sort_values([key, "n", col], ascending=[True, False, True])
    return c.drop_duplicates(key).set_index(key)[col]


def build_product_master_fast(df: pd.DataFrame) -> tuple[pd.DataFrame, list[int]]:
    """Vectorised equivalent of dmie.engine.dedup.build_product_master."""
    d = df.reset_index().rename(columns={"index": "_i"})
    g = d.groupby("product_id", sort=False)
    has_sales = d["sales"].notna()
    has_rev = d["reviews"].notna()
    # best listing: max sales among known sales; else max reviews; else first row (v1 idxmax picks the first max)
    ds = d[has_sales].sort_values(["product_id", "sales", "_i"], ascending=[True, False, True]).drop_duplicates("product_id")
    dr = d[has_rev].sort_values(["product_id", "reviews", "_i"], ascending=[True, False, True]).drop_duplicates("product_id")
    df_first = d.sort_values("_i").drop_duplicates("product_id")
    best = df_first.set_index("product_id")["_i"].rename("first")
    best = pd.concat([best, dr.set_index("product_id")["_i"].rename("rev"), ds.set_index("product_id")["_i"].rename("sales")], axis=1)
    best_i = best["sales"].fillna(best["rev"]).fillna(best["first"]).astype(int)
    basis = np.where(best["sales"].notna(), "sales", np.where(best["rev"].notna(), "reviews", "first_listing"))

    agg = g.agg(listing_count=("id", "size"), price_min=("price", "min"), price_max=("price", "max"),
                price_median=("price", "median"), total_sales=("sales", lambda s: s.sum(min_count=1)),
                revenue=("revenue", lambda s: s.sum(min_count=1)), sales_coverage=("sales", lambda s: s.notna().mean()),
                reviews=("reviews", lambda s: s.sum(min_count=1)), data_confidence=("data_confidence", "mean"))
    rated = d.dropna(subset=["rating"]).assign(_w=lambda x: x["reviews"].fillna(1).clip(lower=1))
    rated = rated.assign(_wr=rated["rating"] * rated["_w"]).groupby("product_id")[["_wr", "_w"]].sum()
    listing_ids = g["id"].agg(lambda s: list(s.astype(str)))
    sellers = d.dropna(subset=["seller"]).groupby("product_id")["seller"].nunique()
    specs = g["specs"].agg(lambda ss: {k: v for s in reversed(list(ss)) for k, v in (s or {}).items()})  # first wins
    out = pd.DataFrame(index=agg.index)
    bi = best_i.reindex(out.index).to_numpy()
    src = df.reset_index(drop=True)
    out["title"] = src["title"].to_numpy()[bi]
    out["brand"] = _mode_by(d, "product_id", "brand").reindex(out.index)
    out["product_type"] = _mode_by(d, "product_id", "segment_label").reindex(out.index) if "segment_label" in d else None
    out["segment_id"] = _mode_by(d, "product_id", "segment_id").reindex(out.index) if "segment_id" in d else None
    out["family_id"] = _mode_by(d, "product_id", "family_id").reindex(out.index) if "family_id" in d else None
    out["attributes"] = specs.reindex(out.index)
    out["listing_count"] = agg["listing_count"].astype(int)
    out["listing_ids"] = listing_ids.reindex(out.index)
    out["best_listing"] = src["id"].to_numpy()[bi]
    out["best_listing_basis"] = pd.Series(basis, index=best.index).reindex(out.index)
    out["image"] = src["image"].to_numpy()[bi]
    out["url"] = src["url"].to_numpy()[bi]
    for c in ("price_min", "price_max", "price_median", "total_sales", "revenue", "sales_coverage", "reviews", "data_confidence"):
        out[c] = agg[c]
    out["rating"] = (rated["_wr"] / rated["_w"]).reindex(out.index)
    out["sellers"] = sellers.reindex(out.index).astype("Int64").astype(object).where(sellers.reindex(out.index).notna(), None)
    out = out.reset_index()
    cols = ["product_id", "title", "brand", "product_type", "segment_id", "family_id", "attributes", "listing_count",
            "listing_ids", "best_listing", "best_listing_basis", "image", "url", "price_min", "price_max", "price_median",
            "total_sales", "revenue", "sales_coverage", "rating", "reviews", "sellers", "data_confidence"]
    return out[cols], best_i.tolist()

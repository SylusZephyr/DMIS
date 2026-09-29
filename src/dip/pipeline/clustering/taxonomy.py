"""Taxonomy-anchored segmentation (Phase 5.3).

Family  = the marketplace sub-category (e.g. Amazon browse node / SellerSprite 小类目) -- a real,
          externally defined product type instead of an unsupervised text cluster.
Segment = the sub-category itself, or -- when it holds at least ``split_min_listings`` listings --
          text/spec clusters inside it (the existing discovery algorithm run within the sub-category).

Keywords: class-based TF-IDF over uni- and bi-grams of all segments of the market, after removing
English stop words, a generic marketing stop-list (config), the market's brand names and the
segment's own sub-category words (already in its name). Descriptions are generated from the
segment's computed statistics (never free text), in English and Chinese.

Sources without a sub-category fall back to pure text discovery (``clustering.discover``).
"""

from __future__ import annotations

import hashlib
import re

import numpy as np
import pandas as pd

from dip.metrics import config
from dmie.engine.discovery import DiscoveryResult, _dominant_specs
from dmie.engine.features import extract_specs

_TOKEN = re.compile(r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b")


def _fid(leaf: str) -> str:
    return "C" + hashlib.sha1(leaf.encode("utf-8")).hexdigest()[:6]


def _words(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "")]


def keywords(texts: list[str], labels: np.ndarray, n: int, extra_stop: set[str], per_label_stop: dict | None = None) -> dict:
    """Class-based TF-IDF: terms frequent in a segment and rare in the others."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

    stop = set(ENGLISH_STOP_WORDS) | {s.lower() for s in config()["segmentation"]["stop_terms"]} | extra_stop
    uniq = list(dict.fromkeys(labels))
    docs = [" ".join(" ".join(w for w in _words(t) if w not in stop and not any(ch.isdigit() for ch in w))
                     for t, lab in zip(texts, labels) if lab == c) for c in uniq]
    try:
        cv = CountVectorizer(ngram_range=(1, 2), token_pattern=_TOKEN.pattern, min_df=1)
        counts = cv.fit_transform(docs).toarray().astype(float)
    except ValueError:
        return {c: [] for c in uniq}
    vocab = np.array(cv.get_feature_names_out())
    tf = counts / (counts.sum(axis=1, keepdims=True) + 1e-12)
    idf = np.log(1 + (counts.sum() / max(len(uniq), 1)) / (counts.sum(axis=0) + 1))
    score = tf * idf
    out = {}
    for i, c in enumerate(uniq):
        own_stop = (per_label_stop or {}).get(c, set())
        terms: list[str] = []
        for j in np.argsort(-score[i]):
            if score[i, j] <= 0:
                break
            term = vocab[j]
            if set(term.split()) & own_stop or any(term in t or t in term for t in terms):
                continue
            terms.append(term)
            if len(terms) >= n:
                break
        out[c] = terms
    return out


def _merge_small(texts: list[str], labels: np.ndarray, min_size: int) -> np.ndarray:
    """Merge clusters below ``min_size`` into the most similar remaining cluster (TF-IDF centroid cosine)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    labels = labels.copy()
    sizes = pd.Series(labels).value_counts()
    big = [c for c in sizes.index if sizes[c] >= min_size]
    if len(big) <= 1:
        return np.zeros(len(labels), dtype=int)
    X = TfidfVectorizer(token_pattern=_TOKEN.pattern, stop_words="english").fit_transform(texts)
    cents = np.vstack([np.asarray(X[labels == c].mean(axis=0)).ravel() for c in big])
    cents /= np.linalg.norm(cents, axis=1, keepdims=True) + 1e-12
    small = ~np.isin(labels, big)
    if small.any():
        sims = X[np.flatnonzero(small)] @ cents.T
        labels[small] = np.array(big)[np.asarray(sims).argmax(axis=1)]
    return pd.factorize(labels)[0]


def _silhouette(texts: list[str], labels: np.ndarray) -> float | None:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics import silhouette_score

    if labels.max() < 1 or len(labels) <= labels.max() + 1:
        return None
    X = TfidfVectorizer(token_pattern=_TOKEN.pattern, stop_words="english").fit_transform(texts)
    return float(silhouette_score(X, labels, metric="cosine"))


def discover(listings: pd.DataFrame) -> DiscoveryResult:
    from dip.pipeline import clustering

    if "category" not in listings or listings["category"].isna().all():
        res = clustering.discover(listings)
        res.method = {**res.method, "basis": "text clusters (no marketplace sub-category in source)"}
        return res
    cfg = config()["segmentation"]
    df = listings.reset_index(drop=True).copy()
    n = len(df)
    leaf = df["category"].fillna("(no sub-category)").astype(str)
    seg_ids = np.empty(n, dtype=object)
    conf = np.ones(n)
    split_info: dict = {}
    for name in leaf.unique():
        idx = np.flatnonzero((leaf == name).to_numpy())
        fid = _fid(name)
        if len(idx) >= cfg["split_min_listings"]:
            sub = clustering.discover(df.iloc[idx].reset_index(drop=True))
            titles = df.iloc[idx]["title"].fillna("").astype(str).tolist()
            min_size = int(np.clip(round(cfg["min_segment_share"] * len(idx)), cfg["min_segment_listings"],
                                   cfg["max_segment_listings_floor"]))
            local = _merge_small(titles, pd.factorize(sub.frame["segment_id"])[0], min_size)
            sil = _silhouette(titles, local)
            if local.max() >= 1 and sil is not None and sil >= cfg["min_split_silhouette"]:
                seg_ids[idx] = [f"{fid}-S{k}" for k in local]
                conf[idx] = sub.frame["segment_confidence"].to_numpy()
                split_info[name] = {"segments": int(local.max()) + 1, "silhouette": round(sil, 3), "method": sub.method}
            else:
                seg_ids[idx] = f"{fid}-S0"
                split_info[name] = {"segments": 1, "silhouette": None if sil is None else round(sil, 3),
                                    "note": "clusters not distinct enough -- kept as one segment"}
        else:
            seg_ids[idx] = f"{fid}-S0"
    df["specs"] = df["title"].map(extract_specs)
    df["family_id"] = leaf.map(_fid)
    df["family_label"] = leaf
    df["segment_id"] = seg_ids
    df["segment_confidence"] = np.round(conf, 3)

    brands = {w for b in df["brand"].dropna().astype(str) for w in _words(b)}
    leaf_stop = {sid: set(_words(lf)) for sid, lf in zip(df["segment_id"], leaf)}
    texts = df["title"].fillna("").astype(str).tolist()
    kw = keywords(texts, df["segment_id"].to_numpy(), cfg["keywords"] * 2, brands, leaf_stop)
    # terms shared by most sibling segments of a split sub-category are not distinctive there
    seg_leaf = df.drop_duplicates("segment_id").set_index("segment_id")["family_label"]
    for lf, sids in seg_leaf.groupby(seg_leaf).groups.items():
        if len(sids) < 2:
            continue
        counts = pd.Series([t for sid in sids for t in kw.get(sid, [])]).value_counts()
        common = set(counts[counts > cfg["sibling_common_share"] * len(sids)].index)
        for sid in sids:
            leaf_stop[sid] = leaf_stop.get(sid, set()) | {w for t in common for w in t.split()}
    kw = keywords(texts, df["segment_id"].to_numpy(), cfg["keywords"], brands, leaf_stop)
    rows = []
    for sid, g in df.groupby("segment_id", sort=False):
        name = g["family_label"].iloc[0]
        split = split_info.get(name, {}).get("segments", 1) > 1
        terms = kw.get(sid, [])
        label = f"{name} · {', '.join(terms[: cfg['label_terms']])}" if split and terms else name
        spec_txt = _dominant_specs(g["specs"], g["title"])
        rows.append({"segment_id": sid, "segment_label": label, "family_id": g["family_id"].iloc[0], "family_label": name,
                     "subcategory": name, "listings": int(len(g)), "top_terms": terms, "dominant_specs": spec_txt,
                     "mean_confidence": float(g["segment_confidence"].mean())})
    segments = pd.DataFrame(rows).sort_values(["family_label", "listings"], ascending=[True, False]).reset_index(drop=True)
    df["segment_label"] = df["segment_id"].map(segments.set_index("segment_id")["segment_label"])
    families = (segments.groupby(["family_id", "family_label"], as_index=False)
                .agg(segments=("segment_id", "count"), listings=("listings", "sum")))
    method = {"basis": "marketplace sub-category", "sub_categories": int(leaf.nunique()), "split": split_info}
    return DiscoveryResult(df, segments, families, method)


def _money(v) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    v = float(v)
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:,.0f}"


def describe(segments: pd.DataFrame, listings: pd.DataFrame) -> pd.DataFrame:
    """Generated, statistics-only descriptions (en + zh) for each segment."""
    tpl = config()["segmentation"]["description_template"]
    s = segments.copy()
    stats = listings.groupby("segment_id").agg(n=("id", "size"), p_lo=("price", lambda x: x.quantile(0.1)),
                                               p_hi=("price", lambda x: x.quantile(0.9)),
                                               entrants=("is_entrant", "sum") if "is_entrant" in listings else ("id", "size"))
    out_en, out_zh = [], []
    for _, r in s.iterrows():
        st = stats.loc[r["segment_id"]] if r["segment_id"] in stats.index else None
        if st is None:
            out_en.append(None)
            out_zh.append(None)
            continue
        kws = r.get("top_terms")
        kws = list(kws) if isinstance(kws, (list, np.ndarray)) else []
        top = r.get("top_brand_est")
        vals = dict(n=int(st["n"]), price=f"${st['p_lo']:,.0f}–{st['p_hi']:,.0f}" if pd.notna(st["p_lo"]) else "n/a",
                    revenue=_money(r.get("revenue_est")), rev_lo=_money(r.get("revenue_lo")), rev_hi=_money(r.get("revenue_hi")),
                    brand=top or "n/a", share="", entrants=int(st["entrants"]) if "is_entrant" in listings else "n/a",
                    keywords=", ".join(kws[:6]) or "n/a")
        hhi = r.get("hhi_est")
        vals["share"] = f"HHI {hhi:,.0f}" if hhi is not None and pd.notna(hhi) else "n/a"
        out_en.append(tpl["en"].format(**vals))
        out_zh.append(tpl["zh"].format(**vals))
    s["segment_description"] = out_en
    s["segment_description_zh"] = out_zh
    return s


# ---------------------------------------------------------------- stable segment ids across snapshots
_SEG_ID = re.compile(r"^(?P<fam>.+)-S(?P<k>\d+)$")


def stabilize(disc: DiscoveryResult, previous: pd.DataFrame | None) -> tuple[DiscoveryResult, dict]:
    """Keep segment ids stable across runs (analogous to product_resolution.stable_ids).

    Segments are rediscovered on every run, and a split sub-category's clusters are numbered in discovery
    order, so the same segment can come back as ``C…-S1`` instead of ``C…-S0``. After discovery each new
    segment inherits the previous run's id of the same sub-category when it holds the majority of that
    previous segment's still-present listings; each previous id is used at most once (on a split, the larger
    part keeps it). A segment with no such predecessor gets an id never used by the previous run, so an old
    id never comes back with a different meaning. Clustering itself is unchanged.

    ``previous``: listing id -> segment_id of the last run (columns id, segment_id)."""
    frame, segs = disc.frame, disc.segments
    if previous is None or previous.empty or "segment_id" not in frame or "id" not in frame:
        return disc, {"status": "no previous run"}
    prev = previous.dropna(subset=["id", "segment_id"]).drop_duplicates("id", keep="last").set_index("id")["segment_id"].astype(str)
    cur = frame.dropna(subset=["segment_id"]).drop_duplicates("id", keep="last").set_index("id")["segment_id"].astype(str)
    present_prev = prev.loc[cur.index.intersection(prev.index)]
    prev_size = present_prev.value_counts()

    def fam(sid: str) -> str:
        m = _SEG_ID.match(sid)
        return m.group("fam") if m else sid

    groups = cur.groupby(cur).groups
    order = sorted(groups, key=lambda g: (-len(groups[g]), g))
    used, remap = set(), {}
    for g in order:
        members = [i for i in groups[g] if i in present_prev.index]
        if not members:
            continue
        counts = present_prev.loc[members].value_counts()
        counts = counts[[fam(p) == fam(g) for p in counts.index]]        # ids stay inside their sub-category
        if counts.empty:
            continue
        best_n = int(counts.max())
        cand = min(p for p, n in counts.items() if n == best_n)
        if cand not in used and best_n * 2 > prev_size[cand]:
            used.add(cand)
            remap[g] = cand
    # segments without a predecessor: an id unused by the previous run and by the inherited ids
    taken = set(prev.unique()) | set(remap.values())
    next_k: dict[str, int] = {}
    for sid in list(taken) + list(groups):
        m = _SEG_ID.match(sid)
        if m:
            next_k[m.group("fam")] = max(next_k.get(m.group("fam"), 0), int(m.group("k")) + 1)
    for g in order:
        if g in remap:
            continue
        if g in taken:
            f = fam(g)
            k = next_k.get(f, 0)
            remap[g] = f"{f}-S{k}"
            next_k[f] = k + 1
            taken.add(remap[g])
        else:
            remap[g] = g
    changed = {k: v for k, v in remap.items() if k != v}
    if changed:
        frame = frame.assign(segment_id=frame["segment_id"].astype(str).replace(changed))
        segs = segs.assign(segment_id=segs["segment_id"].astype(str).replace(changed))
    final = frame.dropna(subset=["segment_id"]).drop_duplicates("id", keep="last").set_index("id")["segment_id"].astype(str)
    both = final.index.intersection(prev.index)
    same = int((final.loc[both] == prev.loc[both]).sum())
    stats = {"status": "ok", "listings_in_both": int(len(both)), "listings_same_segment": same,
             "share_same_segment": round(same / len(both), 4) if len(both) else None,
             "segments_carried": len(used), "segments_new": int(segs["segment_id"].nunique() - len(used)),
             "ids_changed": len(changed)}
    method = {**(disc.method or {}), "stable_ids": stats}
    return DiscoveryResult(frame, segs, disc.families, method), stats

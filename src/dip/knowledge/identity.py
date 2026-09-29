"""Product identity after resolution (spec 19-20, 95-96, 132, 146-147): configuration-aware products,
human identity decisions, identity confidence and the duplicate review queue.

Runs right after product resolution, on the resolved listings:

1. **Human merge decisions** join the products of the two listings (union-find).
2. **Configuration split** (spec 19 stage 5, 105): listings of one resolved product that name different
   component sets (``configuration_key``, e.g. ``control_unit=N3|handpiece=H37L1`` vs ``...=102L``) become
   separate products. Listings without a key stay with the product's most common configuration and are
   flagged ``configuration_uncertain``.
3. **Human keep-separate decisions** move the second listing of the pair out into its own product.

Product IDs keep the resolver's formula ("P" + sha1 of the sorted member listing ids), and the Product
Master is rebuilt with the resolver's own builder, so every downstream stage sees the usual contract.

Identity confidence (spec 147) per product with >1 listing: the mean of the evidence components that
exist -- model-number agreement, specification agreement, image agreement, pairwise match score (text,
brand, price) and brand consistency. A single-listing product has no identity question (None).

Duplicate candidates (spec 95): scored pairs close to the match threshold, both merged and not merged,
for a person to decide Merge / Keep separate / Needs more evidence. Every decision is stored and becomes
evaluation data: precision and recall of the automatic decisions against people (spec 96, 132).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

import numpy as np
import pandas as pd

from dmie.engine.config import section
from dmie.engine.dedup import build_product_master
from dmie.engine.features import extract_specs, image_id

DECISIONS = ("merge", "keep_separate", "needs_evidence")


class _UF:
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _pid(ids) -> str:
    return "P" + hashlib.sha1("|".join(sorted(map(str, ids))).encode()).hexdigest()[:12]


def regroup(frame: pd.DataFrame, config_keys: pd.Series, decisions: pd.DataFrame | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Apply human decisions and the configuration split. Returns (frame with new product_id and
    ``configuration_key`` / ``configuration_uncertain`` / ``identity_changes`` columns, change log)."""
    df = frame.copy()
    df["id"] = df["id"].astype(str)
    df["configuration_key"] = config_keys.to_numpy()
    changes: list[dict] = []
    decisions = decisions if decisions is not None and len(decisions) else pd.DataFrame(columns=["listing_a", "listing_b", "decision"])
    present = set(df["id"])
    # 1. merges
    uf = _UF(df["product_id"].unique().tolist())
    prod_of = dict(zip(df["id"], df["product_id"]))
    for r in decisions[decisions["decision"] == "merge"].itertuples():
        a, b = str(r.listing_a), str(r.listing_b)
        if a in present and b in present and uf.find(prod_of[a]) != uf.find(prod_of[b]):
            uf.union(prod_of[a], prod_of[b])
            changes.append({"change": "human_merge", "listing_a": a, "listing_b": b})
    df["_grp"] = df["product_id"].map(uf.find)
    # 2. configuration split
    df["configuration_uncertain"] = False
    new_grp = df["_grp"].astype(str).copy()
    for g, sub in df.groupby("_grp", sort=False):
        keys = sub["configuration_key"].dropna()
        distinct = keys.unique()
        if len(distinct) < 2:
            continue
        major = Counter(keys).most_common(1)[0][0]
        for idx, k in sub["configuration_key"].items():
            new_grp.at[idx] = f"{g}~{k if isinstance(k, str) else major}"
        unk = sub.index[sub["configuration_key"].isna()]
        df.loc[unk, "configuration_uncertain"] = True
        changes.append({"change": "configuration_split", "product": str(g), "configurations": sorted(map(str, distinct)),
                        "unassigned_listings": int(len(unk))})
    df["_grp"] = new_grp
    # 3. keep-separate
    for r in decisions[decisions["decision"] == "keep_separate"].itertuples():
        a, b = str(r.listing_a), str(r.listing_b)
        if a in present and b in present:
            ia, ib = df.index[df["id"] == a][0], df.index[df["id"] == b][0]
            if df.at[ia, "_grp"] == df.at[ib, "_grp"]:
                df.loc[df["id"] == b, "_grp"] = f"{df.at[ib, '_grp']}~sep~{b}"
                changes.append({"change": "human_keep_separate", "listing_a": a, "listing_b": b})
    ids = df.groupby("_grp")["id"].apply(list)
    df["product_id"] = df["_grp"].map({g: _pid(v) for g, v in ids.items()})
    return df.drop(columns=["_grp"]), changes


def rebuild(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Product Master from the regrouped listings (the resolver's own builder)."""
    df = frame.copy()
    if "specs" not in df:
        df["specs"] = df["title"].map(extract_specs)
    products, best = build_product_master(df)
    df["is_best_listing"] = df.index.isin(best)
    return df, products


def identity_confidence(frame: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Per product: identity confidence 0-100 and its components (spec 147)."""
    df = frame.copy()
    df["id"] = df["id"].astype(str)
    score_of: dict[tuple[str, str], float] = {}
    if len(edges):
        for a, b, s in zip(edges["listing_a"].astype(str), edges["listing_b"].astype(str), edges["score"]):
            score_of[(min(a, b), max(a, b))] = float(s)
    rows = []
    for pid, g in df.groupby("product_id", sort=False):
        n = len(g)
        if n < 2:
            rows.append({"product_id": pid, "identity_confidence": None, "identity_components": json.dumps({"single_listing": True})})
            continue
        comps: dict[str, float] = {}
        toks = [frozenset((s or {}).get("model_tokens", [])) for s in g["specs"]] if "specs" in g else []
        withtok = [t for t in toks if t]
        if len(withtok) >= 2:
            common = frozenset.intersection(*withtok)
            comps["model_number"] = 1.0 if common else 0.0
        specs = [s or {} for s in g["specs"]] if "specs" in g else []
        keys = {k for s in specs for k in s if k != "model_tokens"}
        if keys:
            agree = []
            for k in keys:
                vals = [s[k] for s in specs if k in s]
                if len(vals) >= 2:
                    agree.append(1.0 if (max(vals) - min(vals)) <= 0.05 * max(max(vals), 1e-9) else 0.0)
            if agree:
                comps["specification"] = float(np.mean(agree))
        imgs = [image_id(u) for u in g["image"]] if "image" in g else []
        imgs = [i for i in imgs if i]
        if len(imgs) >= 2:
            comps["image"] = Counter(imgs).most_common(1)[0][1] / len(imgs)
        ids = sorted(g["id"])
        pair_scores = [score_of[(a, b)] for i, a in enumerate(ids) for b in ids[i + 1:] if (a, b) in score_of]
        if pair_scores:
            comps["match_score"] = float(np.clip(np.mean(pair_scores), 0, 1))
        brands = g["brand"].dropna().astype(str).str.lower() if "brand" in g else pd.Series(dtype=str)
        if len(brands):
            comps["brand"] = Counter(brands).most_common(1)[0][1] / len(brands)
        conf = round(100.0 * float(np.mean(list(comps.values()))), 1) if comps else None
        rows.append({"product_id": pid, "identity_confidence": conf, "identity_components": json.dumps({k: round(v, 3) for k, v in comps.items()})})
    return pd.DataFrame(rows)


def duplicate_candidates(frame: pd.DataFrame, edges: pd.DataFrame, decisions: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pairs near the match threshold for human review, with both listings side by side (spec 95)."""
    cols = ["listing_a", "listing_b", "product_a", "product_b", "score", "system_decision", "reason", "title_a", "title_b",
            "brand_a", "brand_b", "price_a", "price_b", "configuration_a", "configuration_b", "same_image", "status"]
    if not len(edges):
        return pd.DataFrame(columns=cols)
    cfg = section("dedup")
    thr = float(cfg.get("match_threshold", 0.72))
    band = float((cfg.get("review_band") or 0.15))
    e = edges.copy()
    e["listing_a"], e["listing_b"] = e["listing_a"].astype(str), e["listing_b"].astype(str)
    near = ((e["decision"] == "no_match") & (e["score"] >= thr - band)) | ((e["decision"] == "match") & (e["score"] < thr + band / 2))
    e = e[near]
    if not len(e):
        return pd.DataFrame(columns=cols)
    L = frame.assign(id=frame["id"].astype(str)).drop_duplicates("id").set_index("id")
    get = lambda col, ids: [L[col].get(i) if col in L else None for i in ids]  # noqa: E731
    out = pd.DataFrame({
        "listing_a": e["listing_a"].to_numpy(), "listing_b": e["listing_b"].to_numpy(),
        "product_a": get("product_id", e["listing_a"]), "product_b": get("product_id", e["listing_b"]),
        "score": e["score"].to_numpy(), "system_decision": e["decision"].to_numpy(), "reason": e["reason"].to_numpy(),
        "title_a": get("title", e["listing_a"]), "title_b": get("title", e["listing_b"]),
        "brand_a": get("brand", e["listing_a"]), "brand_b": get("brand", e["listing_b"]),
        "price_a": get("price", e["listing_a"]), "price_b": get("price", e["listing_b"]),
        "configuration_a": get("configuration_key", e["listing_a"]), "configuration_b": get("configuration_key", e["listing_b"]),
    })
    ia = [image_id(x) if isinstance(x, str) else None for x in get("image", e["listing_a"])]
    ib = [image_id(x) if isinstance(x, str) else None for x in get("image", e["listing_b"])]
    out["same_image"] = [bool(a) and a == b for a, b in zip(ia, ib)]
    status = {}
    if decisions is not None and len(decisions):
        for r in decisions.itertuples():
            a, b = sorted((str(r.listing_a), str(r.listing_b)))
            status[(a, b)] = r.decision
    out["status"] = [status.get(tuple(sorted((a, b))), "pending") for a, b in zip(out["listing_a"], out["listing_b"])]
    return out.sort_values("score", ascending=False).reset_index(drop=True)[cols]


def evaluate(edges: pd.DataFrame, decisions: pd.DataFrame) -> dict:
    """Precision / recall of the automatic match decisions against human decisions (spec 132).
    Only pairs a person decided merge or keep_separate count; needs_evidence is excluded."""
    if decisions is None or not len(decisions) or not len(edges):
        return {"labelled_pairs": 0, "precision": None, "recall": None, "tp": 0, "fp": 0, "fn": 0, "tn": 0}
    sysd = {tuple(sorted((str(a), str(b)))): d for a, b, d in zip(edges["listing_a"], edges["listing_b"], edges["decision"])}
    tp = fp = fn = tn = 0
    for r in decisions.itertuples():
        if r.decision not in ("merge", "keep_separate"):
            continue
        key = tuple(sorted((str(r.listing_a), str(r.listing_b))))
        pred = sysd.get(key) == "match"
        truth = r.decision == "merge"
        tp += pred and truth
        fp += pred and not truth
        fn += (not pred) and truth
        tn += (not pred) and not truth
    n = tp + fp + fn + tn
    return {"labelled_pairs": n, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def load_decisions(market: str) -> pd.DataFrame:
    """Latest human decision per listing pair of a market."""
    from dip.storage import business as b
    with b.session() as s:
        rows = s.query(b.IdentityDecision).filter(b.IdentityDecision.market_name == market).order_by(b.IdentityDecision.at).all()
        data = [{"listing_a": r.listing_a, "listing_b": r.listing_b, "decision": r.decision, "at": r.at} for r in rows]
    df = pd.DataFrame(data, columns=["listing_a", "listing_b", "decision", "at"])
    if not len(df):
        return df
    df["_k"] = [tuple(sorted((a, b))) for a, b in zip(df["listing_a"], df["listing_b"])]
    return df.drop_duplicates("_k", keep="last").drop(columns=["_k"]).reset_index(drop=True)

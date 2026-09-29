"""Stage 3 -- Dental relevance with an explained confidence score.

Wraps ``dmie.engine.relevance.classify_relevance`` (TF-IDF prototypes +
lexicon + marketplace category + feedback-trained model) and adds a
per-signal explanation -- title, category, image -- so every score says
*why*. Image evidence is only reported when the image reference contains
readable words; opaque marketplace image IDs are reported as "no signal"
rather than guessed.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from dmie.engine.config import load_yaml
from dmie.engine.relevance import classify_relevance

_WORD = re.compile(r"[a-z]{4,}")


def _terms(domain: str) -> tuple[set, set]:
    cfg = load_yaml("relevance_domains.yaml")["domains"][domain]
    return set(cfg.get("core_terms", [])), set(cfg.get("negative_terms", []))


def _signal(text: str | None, pos: set, neg: set) -> dict:
    text = text if isinstance(text, str) else None
    words = set(_WORD.findall((text or "").lower())) | set((text or "").lower().split())
    p, n = sorted(words & pos)[:5], sorted(words & neg)[:5]
    verdict = "dental" if p and not n else "non-dental" if n and not p else "mixed" if p and n else "neutral"
    return {"verdict": verdict, "dental_terms": p, "non_dental_terms": n}


def explain(row: pd.Series, pos: set, neg: set) -> dict:
    image = row.get("image") if isinstance(row.get("image"), str) else ""
    readable = " ".join(_WORD.findall(image.lower().split("/")[-1])) if image else ""
    return {
        "confidence": float(row["relevance_score"]),
        "status": row["relevance_status"],
        "title": {"text": (row.get("title") if isinstance(row.get("title"), str) else "")[:120], **_signal(row.get("title"), pos, neg)},
        "category": {"text": row.get("category"), **_signal(row.get("category"), pos, neg)},
        "image": ({"text": readable, **_signal(readable, pos, neg)} if readable
                  else {"verdict": "no signal", "note": "image reference has no readable words"}),
        "model": row.get("relevance_evidence"),
    }


def _text_key(frame: pd.DataFrame) -> pd.Series:
    """Everything the relevance model and the explanation look at, as one key (vectorised)."""
    def txt(c):
        return frame[c].where(frame[c].notna(), "").astype(str)
    img_words = txt("image").str.rsplit("/", n=1).str[-1].str.lower().str.findall(r"[a-z]{4,}").str.join(" ")
    return (txt("title") + "\x1f" + txt("category") + "\x1f" + txt("description").str.slice(0, 500)
            + "\x1f" + img_words.fillna(""))


CACHE_STATS: dict = {}
MODEL_VERSION = "relevance-v2.1"   # bump when scoring code changes (invalidates the cache)


def model_fingerprint(domain: str, feedback: pd.DataFrame | None, feedback_digest) -> str:
    """Everything a relevance score depends on besides the text itself."""
    import hashlib

    from dmie.engine.config import section

    cfg = json.dumps({"domain": load_yaml("relevance_domains.yaml")["domains"].get(domain),
                      "relevance": section("relevance")}, sort_keys=True, default=str)
    return hashlib.sha256(f"{MODEL_VERSION}|{domain}|{cfg}|{feedback_digest(feedback)}".encode()).hexdigest()


def classify(frame: pd.DataFrame, feedback: pd.DataFrame | None = None, corrections: dict | None = None,
             domain: str = "dental", use_cache: bool = False) -> pd.DataFrame:
    """Score each *unique* listing text once and map results back to every row.

    Marketplace exports repeat the same listing text across monthly snapshots
    and sellers; at 10^6 rows the unique texts are a small fraction. Human
    corrections are applied per native id afterwards, exactly as in v1.
    The explanation is stored as JSON text (one string per unique text).
    With ``use_cache`` scores of texts seen before under the same model
    (config + corrections) are reused from the lake's relevance cache.
    """
    out = frame.copy()
    if len(out) == 0:
        res = classify_relevance(out, feedback, corrections, domain)
        res["relevance_explanation"] = pd.Series(dtype=object)
        return res
    tk = _text_key(out)
    codes, uniques = pd.factorize(tk)
    first = pd.Series(np.arange(len(out))).groupby(codes).first().to_numpy()
    uniq = out.iloc[first].reset_index(drop=True)
    cols = ["relevance_score", "relevance_evidence", "relevance_status", "relevance_explanation"]
    res = pd.DataFrame(index=range(len(uniq)), columns=cols, dtype=object)
    keys = cache = None
    if use_cache:
        from dip.cache import RelevanceCache, feedback_digest, text_keys

        keys = text_keys(pd.Series(uniques)).to_numpy()
        cache = RelevanceCache(model_fingerprint(domain, feedback, feedback_digest))
        known = cache.load().set_index("key")
        hit = pd.Series(keys).isin(known.index).to_numpy()
        if hit.any():
            res.loc[hit, cols] = known.loc[keys[hit], cols].to_numpy()
    else:
        hit = np.zeros(len(uniq), dtype=bool)
    miss = np.flatnonzero(~hit)
    if len(miss):
        scored = classify_relevance(uniq.iloc[miss].reset_index(drop=True), feedback, None, domain)
        pos, neg = _terms(domain)
        scored["relevance_explanation"] = [json.dumps(explain(r, pos, neg), ensure_ascii=False) for _, r in scored.iterrows()]
        res.loc[miss, cols] = scored[cols].to_numpy()
        if cache is not None:
            cache.add(scored[cols].assign(key=keys[miss]))
    for c in cols:
        vals = res[c].to_numpy()
        out[c] = vals[codes]
    out["relevance_score"] = out["relevance_score"].astype(float)
    CACHE_STATS.update(texts=len(uniq), cached=int(hit.sum()))
    human = json.dumps({"status": "human correction", "note": "a person set this decision"})
    for native_id, is_rel in (corrections or {}).items():
        m = out["id"] == native_id
        if m.any():
            out.loc[m, "relevance_score"] = 100.0 if is_rel else 0.0
            out.loc[m, "relevance_status"] = "human_relevant" if is_rel else "human_irrelevant"
            out.loc[m, "relevance_evidence"] = "human correction"
            out.loc[m, "relevance_explanation"] = human
    out["is_relevant"] = out["relevance_status"].isin(["relevant", "human_relevant"])
    return out

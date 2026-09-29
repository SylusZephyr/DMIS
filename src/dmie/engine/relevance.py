"""Domain Relevance Engine (Module 3).

"Is this actually a dental product?" -- answered locally, with no LLM.

Signals, all deterministic:

1. Prototype similarity: TF-IDF (word 1-2 grams + character 3-5 grams)
   cosine similarity of the listing text (title + category + description)
   to the domain's positive and negative prototype phrases in
   config/engine/relevance_domains.yaml.
2. Lexical evidence: whole-word hits on the domain's core / negative terms.
3. Supervised correction model: once humans have corrected enough
   listings (mi_relevance_feedback), a LogisticRegression is trained on
   those corrections and blended in -- its weight grows with the number
   of labels, so the model improves as people use it.

A listing with a stored human correction is never re-scored: the human
decision wins and is marked ``human_verified``.

Output: ``relevance_score`` 0..100, ``relevance_status`` and a short
``relevance_evidence`` string explaining the score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics.pairwise import cosine_similarity

from dmie.engine.config import load_yaml, section

MIN_FEEDBACK_FOR_MODEL = 8


def feedback_classifier(n_labels: int, pos_share: float):
    """The supervised model trained on human corrections.

    ``relevance.feedback_model`` in config/engine/engine.yaml: ``logistic``,
    ``xgboost``, or ``auto`` (default) -- XGBoost once there are at least
    ``xgboost_min_labels`` corrections *and* the optional ``xgboost`` package is
    installed; logistic regression otherwise (boosting overfits a handful of
    labels). Returns (classifier, name).
    """
    cfg = section("relevance")
    choice = str(cfg.get("feedback_model", "auto")).lower()
    want_xgb = choice == "xgboost" or (choice == "auto" and n_labels >= int(cfg.get("xgboost_min_labels", 200)))
    if want_xgb:
        try:
            from xgboost import XGBClassifier

            balance = (1 - pos_share) / pos_share if 0 < pos_share < 1 else 1.0
            return XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.1, subsample=0.9,
                                 colsample_bytree=0.5, scale_pos_weight=balance, random_state=0, n_jobs=1,
                                 eval_metric="logloss"), "xgboost"
        except ImportError:
            pass
    return LogisticRegression(max_iter=1000, C=2.0, class_weight="balanced"), "logistic"


def listing_text(frame: pd.DataFrame, use_category: bool = True) -> pd.Series:
    parts = [frame["title"].fillna("")]
    if use_category:
        parts.append(frame["category"].fillna(""))
    parts.append(frame["description"].fillna("").str.slice(0, 500))
    text = parts[0]
    for p in parts[1:]:
        text = text + " " + p
    return text.str.lower().str.strip()


def _term_regex(terms: list[str]) -> re.Pattern | None:
    terms = [t for t in terms if t]
    if not terms:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(t.lower()) for t in sorted(terms, key=len, reverse=True)) + r")\b")


@dataclass
class RelevanceModel:
    domain: str
    positive: list[str]
    negative: list[str]
    core_terms: list[str]
    negative_terms: list[str]
    feedback: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["text", "label"]))

    @classmethod
    def from_config(cls, domain: str = "dental", feedback: pd.DataFrame | None = None) -> "RelevanceModel":
        cfg = load_yaml("relevance_domains.yaml")["domains"][domain]
        fb = feedback if feedback is not None else pd.DataFrame(columns=["text", "label"])
        return cls(domain, cfg["positive"], cfg["negative"], cfg.get("core_terms", []), cfg.get("negative_terms", []), fb)

    def _vectorizers(self, corpus: list[str]):
        word = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)
        char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1)
        word.fit(corpus)
        char.fit(corpus)
        return lambda texts: hstack([word.transform(texts), char.transform(texts)]).tocsr()

    def score(self, texts: pd.Series, categories: pd.Series | None = None) -> pd.DataFrame:
        texts = texts.fillna("").astype(str).str.lower()
        cats = (categories if categories is not None else pd.Series("", index=texts.index)).fillna("").astype(str).str.lower()
        fb_texts = self.feedback["text"].astype(str).str.lower().tolist() if len(self.feedback) else []
        corpus = list(texts) + self.positive + self.negative + fb_texts
        vec = self._vectorizers(corpus)
        X = vec(list(texts))
        P = vec(self.positive)
        N = vec(self.negative)

        def top_mean(sim: np.ndarray, k: int = 2) -> np.ndarray:
            k = min(k, sim.shape[1])
            return np.sort(sim, axis=1)[:, -k:].mean(axis=1)

        sim_pos = cosine_similarity(X, P)
        sim_neg = cosine_similarity(X, N)
        pos, neg = top_mean(sim_pos), top_mean(sim_neg)
        core_re, neg_re = _term_regex(self.core_terms), _term_regex(self.negative_terms)
        core_hits = texts.map(lambda t: sorted(set(core_re.findall(t))) if core_re else [])
        neg_hits = texts.map(lambda t: sorted(set(neg_re.findall(t))) if neg_re else [])
        kw_pos = np.minimum(core_hits.map(len).to_numpy(), 2) / 2
        kw_neg = np.minimum(neg_hits.map(len).to_numpy(), 2) / 2

        # The marketplace's own category is the strongest single signal:
        # "Women's Drop & Dangle Earrings" outweighs keyword-stuffed titles.
        cat_pos = cats.map(lambda t: bool(core_re and core_re.search(t))).to_numpy(dtype=float)
        cat_neg = cats.map(lambda t: bool(neg_re and neg_re.search(t))).to_numpy(dtype=float)

        logit = -0.6 + 5.0 * (pos - neg) + 3.2 * kw_pos - 3.2 * kw_neg + 1.2 * cat_pos - 3.0 * cat_neg
        prior = 1 / (1 + np.exp(-logit))

        n_labels = len(self.feedback)
        model_prob = None
        if n_labels >= MIN_FEEDBACK_FOR_MODEL and self.feedback["label"].nunique() == 2:
            Xf = vec(fb_texts)
            extra_f = np.column_stack([top_mean(cosine_similarity(Xf, P)), top_mean(cosine_similarity(Xf, N))])
            clf, model_name = feedback_classifier(n_labels, float(self.feedback["label"].astype(int).mean()))
            clf.fit(hstack([Xf, extra_f]).tocsr(), self.feedback["label"].astype(int))
            model_prob = clf.predict_proba(hstack([X, np.column_stack([pos, neg])]).tocsr())[:, 1]
            w = n_labels / (n_labels + 50)
            prob = (1 - w) * prior + w * model_prob
        else:
            prob = prior

        best_pos = np.argmax(sim_pos, axis=1)
        evidence = []
        for i in range(len(texts)):
            bits = []
            if core_hits.iloc[i]:
                bits.append("domain terms: " + ", ".join(core_hits.iloc[i][:4]))
            if neg_hits.iloc[i]:
                bits.append("off-domain terms: " + ", ".join(neg_hits.iloc[i][:4]))
            bits.append(f"closest prototype: '{self.positive[best_pos[i]]}' ({sim_pos[i, best_pos[i]]:.2f})")
            if model_prob is not None:
                bits.append(f"feedback model ({model_name}) p={model_prob[i]:.2f} ({n_labels} labels)")
            evidence.append("; ".join(bits))
        return pd.DataFrame({
            "relevance_score": np.round(prob * 100, 1),
            "relevance_evidence": evidence,
        }, index=texts.index)


def classify_relevance(frame: pd.DataFrame, feedback: pd.DataFrame | None = None,
                       corrections: dict[str, bool] | None = None, domain: str | None = None) -> pd.DataFrame:
    """Add relevance_score / relevance_status / relevance_evidence to ``frame``.

    ``feedback``: DataFrame[text, label] of all stored human corrections (training data).
    ``corrections``: {native id -> is_relevant} for records in *this* dataset.
    """
    cfg = section("relevance")
    domain = domain or cfg.get("domain", "dental")
    model = RelevanceModel.from_config(domain, feedback)
    texts = listing_text(frame, cfg.get("use_category_field", True))
    out = frame.copy()
    if len(out) == 0:
        for c in ("relevance_score", "relevance_status", "relevance_evidence"):
            out[c] = pd.Series(dtype="object")
        return out
    scored = model.score(texts, frame["category"] if cfg.get("use_category_field", True) else None)
    out["relevance_score"] = scored["relevance_score"]
    out["relevance_evidence"] = scored["relevance_evidence"]
    hi, lo = cfg.get("relevant_threshold", 60), cfg.get("uncertain_threshold", 40)
    out["relevance_status"] = np.select(
        [out["relevance_score"] >= hi, out["relevance_score"] >= lo], ["relevant", "uncertain"], "irrelevant"
    )
    for native_id, is_rel in (corrections or {}).items():
        m = out["id"] == native_id
        out.loc[m, "relevance_score"] = 100.0 if is_rel else 0.0
        out.loc[m, "relevance_status"] = "human_relevant" if is_rel else "human_irrelevant"
        out.loc[m, "relevance_evidence"] = "human correction"
    out["is_relevant"] = out["relevance_status"].isin(["relevant", "human_relevant"])
    return out


def score_texts(texts: list[str], domain: str = "dental", feedback: pd.DataFrame | None = None) -> list[float]:
    """Convenience: score free-text titles (used by the dashboard and tests)."""
    model = RelevanceModel.from_config(domain, feedback)
    return model.score(pd.Series(texts)).relevance_score.tolist()

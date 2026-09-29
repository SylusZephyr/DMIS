"""Customer Pain Analysis (Module 9) -- local text processing, no LLM.

Pipeline over review text:

1. sentence split + lexicon sentiment (with negation handling)
2. aspect detection from config/engine/review_aspects.yaml
3. per-aspect complaint / praise frequency and mean sentiment
4. keyword extraction: n-grams over-represented in negative sentences
   (surfaces complaint themes that no configured aspect covers yet)
5. missing-feature requests via regex patterns

Output per scope (market, segment or product): common complaints with
the product-improvement opportunity each implies, common advantages,
missing features, and the discovered complaint keywords.

Reviews come from a ``review_text`` column in the dataset or a separate
reviews table (columns: id or product_id, text, optional rating).
With no review text the result is ``status="no_review_data"``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer

from dmie.engine.config import load_yaml

_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_TOKEN = re.compile(r"[a-z']+")
_NEGATIONS = {"not", "no", "never", "dont", "don't", "doesn't", "didn't", "isn't", "wasn't", "won't", "cannot", "can't", "without"}


@dataclass
class PainReport:
    scope: str
    status: str                      # ok | no_review_data
    reviews: int = 0
    sentences: int = 0
    avg_sentiment: float | None = None
    complaints: list[dict] = field(default_factory=list)
    advantages: list[dict] = field(default_factory=list)
    missing_features: list[dict] = field(default_factory=list)
    complaint_keywords: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _config():
    cfg = load_yaml("review_aspects.yaml")
    aspects = {}
    for name, a in cfg["aspects"].items():
        pats = [re.compile(r"\b" + re.escape(k.lower()) + r"\b") for k in a["keywords"]]
        aspects[name] = (pats, a.get("opportunity", ""))
    missing = [re.compile(p, re.I) for p in cfg.get("missing_feature_patterns", [])]
    lex = {**cfg["sentiment"]["positive"], **cfg["sentiment"]["negative"]}
    return aspects, missing, lex


def sentence_sentiment(sentence: str, lex: dict) -> float:
    tokens = _TOKEN.findall(sentence.lower())
    score = 0.0
    for i, t in enumerate(tokens):
        w = lex.get(t)
        if w is None:
            continue
        if any(x in _NEGATIONS for x in tokens[max(0, i - 3):i]):
            w = -w * 0.8
        score += w
    return float(np.tanh(score / 3))


def analyze_reviews(texts: list[str], ratings: list[float | None] | None = None, scope: str = "market") -> PainReport:
    texts = [t for t in texts if isinstance(t, str) and t.strip()]
    if not texts:
        return PainReport(scope, "no_review_data")
    aspects, missing_pats, lex = _config()
    rows = []
    missing = Counter()
    for r_i, text in enumerate(texts):
        rating = ratings[r_i] if ratings and r_i < len(ratings) else None
        for sent in _SENT_SPLIT.split(text):
            s = sent.strip()
            if len(s) < 3:
                continue
            senti = sentence_sentiment(s, lex)
            if rating is not None and not np.isnan(rating):
                senti = 0.7 * senti + 0.3 * ((rating - 3) / 2)  # star rating nudges ambiguous sentences
            low = s.lower()
            hit = [name for name, (pats, _) in aspects.items() if any(p.search(low) for p in pats)]
            rows.append({"review": r_i, "sentence": s, "sentiment": senti, "aspects": hit})
            for p in missing_pats:
                for m in p.findall(low):
                    missing[m.strip()] += 1
    df = pd.DataFrame(rows)
    n_reviews = len(texts)
    complaints, advantages = [], []
    for name, (_, opp) in aspects.items():
        m = df["aspects"].map(lambda a, n=name: n in a)
        if not m.any():
            continue
        sub = df[m]
        neg = sub[sub["sentiment"] < -0.1]
        pos = sub[sub["sentiment"] > 0.1]
        if len(neg):
            complaints.append({
                "aspect": name, "mentions": int(neg["review"].nunique()),
                "share_of_reviews": round(neg["review"].nunique() / n_reviews, 4),
                "avg_sentiment": round(float(neg["sentiment"].mean()), 3),
                "opportunity": opp, "example": neg.sort_values("sentiment").iloc[0]["sentence"][:200],
            })
        if len(pos):
            advantages.append({
                "aspect": name, "mentions": int(pos["review"].nunique()),
                "share_of_reviews": round(pos["review"].nunique() / n_reviews, 4),
                "example": pos.sort_values("sentiment", ascending=False).iloc[0]["sentence"][:200],
            })
    complaints.sort(key=lambda x: (-x["mentions"], x["avg_sentiment"]))
    advantages.sort(key=lambda x: -x["mentions"])

    keywords = []
    neg_s = df[df["sentiment"] < -0.1]["sentence"].tolist()
    pos_s = df[df["sentiment"] >= -0.1]["sentence"].tolist()
    if len(neg_s) >= 2:
        cv = CountVectorizer(ngram_range=(1, 2), stop_words="english", min_df=2 if len(neg_s) >= 10 else 1)
        try:
            cv.fit(neg_s + pos_s)
            neg_c = np.asarray(cv.transform(neg_s).sum(axis=0)).ravel() + 0.5
            pos_c = np.asarray(cv.transform(pos_s).sum(axis=0)).ravel() + 0.5 if pos_s else np.full_like(neg_c, 0.5)
            ratio = np.log((neg_c / neg_c.sum()) / (pos_c / pos_c.sum()))
            vocab = cv.get_feature_names_out()
            for j in np.argsort(-ratio * np.log1p(neg_c))[:15]:
                if neg_c[j] >= 1.5:
                    keywords.append({"term": vocab[j], "negative_mentions": int(neg_c[j] - 0.5), "log_odds": round(float(ratio[j]), 3)})
        except ValueError:
            pass
    return PainReport(
        scope, "ok", n_reviews, len(df), round(float(df["sentiment"].mean()), 3),
        complaints, advantages,
        [{"feature": k, "mentions": v} for k, v in missing.most_common(10)],
        keywords,
    )


def pain_by_scope(reviews: pd.DataFrame, key: str) -> dict[str, PainReport]:
    """``reviews``: columns [key, text, (rating)] -> one PainReport per key value."""
    out = {}
    if reviews is None or reviews.empty or "text" not in reviews:
        return out
    for k, g in reviews.dropna(subset=["text"]).groupby(key):
        ratings = g["rating"].tolist() if "rating" in g else None
        out[str(k)] = analyze_reviews(g["text"].tolist(), ratings, scope=str(k))
    return out


def review_pain_score(report: PainReport | None) -> float | None:
    """0..1: how much unresolved customer pain exists (an improvement opportunity)."""
    if report is None or report.status != "ok" or not report.reviews:
        return None
    share = sum(c["share_of_reviews"] for c in report.complaints[:3])
    return float(np.clip(0.6 * min(share, 1.0) + 0.4 * max(0.0, -(report.avg_sentiment or 0)), 0, 1))

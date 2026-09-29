"""Consumer Mode recommendation engine (Module 15).

    "I need a micromotor under $400"

1. Parse the query: budget (under / below / max / between / range),
   minimum rating ("4+ stars") and the remaining keywords.
2. Filter products by budget and rating.
3. Rank by a transparent weighted score:
   relevance to the keywords (TF-IDF cosine) 0.45, rating 0.20,
   popularity (log sales rank) 0.20, value (price headroom in budget) 0.15.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_MONEY = r"\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(k)?"
_BETWEEN = re.compile(r"(?:between|from)\s+" + _MONEY + r"\s*(?:and|to|-)\s*" + _MONEY, re.I)
_RANGE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*-\s*\$?\s*(\d+(?:\.\d+)?)")
_MAX = re.compile(r"(?:under|below|less than|max(?:imum)?|up to|<=?|within|budget(?: of)?)\s*" + _MONEY, re.I)
_MIN = re.compile(r"(?:over|above|more than|at least|min(?:imum)?|>=?)\s*" + _MONEY, re.I)
_RATING = re.compile(r"(\d(?:\.\d)?)\s*\+?\s*stars?", re.I)
_FILLER = re.compile(r"\b(i|need|want|looking|for|a|an|the|me|find|show|best|good|cheap|with|under|below|over|above|"
                     r"between|and|to|than|less|more|budget|of|stars?|star|up|at|least|max|min|please|some|that|is|dollars?|usd)\b", re.I)


def _money(num: str, k: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * 1000 if k else v


@dataclass
class ParsedQuery:
    keywords: str
    min_price: float | None
    max_price: float | None
    min_rating: float | None


def parse_query(q: str) -> ParsedQuery:
    text = q
    lo = hi = rating = None
    m = _BETWEEN.search(text) or None
    if m:
        lo, hi = _money(m.group(1), m.group(2)), _money(m.group(3), m.group(4))
        text = text.replace(m.group(0), " ")
    else:
        m = _RANGE.search(text)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            text = text.replace(m.group(0), " ")
    m = _RATING.search(text)
    if m:
        rating = float(m.group(1))
        text = text.replace(m.group(0), " ")
    if hi is None and (m := _MAX.search(text)):
        hi = _money(m.group(1), m.group(2))
        text = text.replace(m.group(0), " ")
    if lo is None and (m := _MIN.search(text)):
        lo = _money(m.group(1), m.group(2))
        text = text.replace(m.group(0), " ")
    kw = re.sub(r"[^\w\s\-]", " ", _FILLER.sub(" ", text))
    return ParsedQuery(re.sub(r"\s+", " ", kw).strip(), lo, hi, rating)


def recommend(products: pd.DataFrame, query: str, top_n: int = 10) -> tuple[ParsedQuery, pd.DataFrame]:
    pq = parse_query(query)
    p = products.copy()
    if p.empty:
        return pq, p
    # match on the product's own title: segment / type labels are shared by many products and dilute the match
    text = p["title"].fillna("").str.lower()
    if pq.keywords:
        vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, analyzer="word").fit(text.tolist() + [pq.keywords.lower()])
        rel = cosine_similarity(vec.transform([pq.keywords.lower()]), vec.transform(text)).ravel()
        cvec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit(text.tolist() + [pq.keywords.lower()])
        rel = np.maximum(rel, 0.8 * cosine_similarity(cvec.transform([pq.keywords.lower()]), cvec.transform(text)).ravel())
    else:
        rel = np.ones(len(p))
    p["match"] = rel
    mask = pd.Series(True, index=p.index)
    if pq.max_price is not None:
        mask &= p["price"] <= pq.max_price
    if pq.min_price is not None:
        mask &= p["price"] >= pq.min_price
    if pq.min_rating is not None:
        mask &= p["rating"] >= pq.min_rating
    if pq.keywords:
        mask &= p["match"] >= max(0.08, 0.35 * p["match"].max())
    c = p[mask].copy()
    if c.empty:
        return pq, c
    rating = ((c["rating"].fillna(c["rating"].median() if c["rating"].notna().any() else 4.0) - 3) / 2).clip(0, 1)
    pop = np.log1p(c["monthly_sales"]).rank(pct=True).fillna(0.3)
    value = (1 - c["price"] / pq.max_price).clip(0, 1) if pq.max_price else (1 - c["price"].rank(pct=True)).fillna(0.5)
    c["recommendation_score"] = np.round(100 * (0.45 * c["match"] / (c["match"].max() or 1) + 0.20 * rating + 0.20 * pop + 0.15 * value), 1)
    c["why"] = [
        f"match {m:.2f}; rating {r if pd.notna(r) else 'n/a'}; sales {int(s) if pd.notna(s) else 'n/a'}/mo; ${pr:,.2f}"
        for m, r, s, pr in zip(c["match"], c["rating"], c["monthly_sales"], c["price"])
    ]
    cols = [x for x in ["product_id", "title", "brand", "price", "rating", "monthly_sales", "listing_count", "image",
                        "product_type", "recommendation_score", "why"] if x in c]
    return pq, c.sort_values("recommendation_score", ascending=False)[cols].head(top_n).reset_index(drop=True)

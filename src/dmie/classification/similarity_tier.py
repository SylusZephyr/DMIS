"""Lightweight ML tier (architecture directive's cascade: rules -> ML/
similarity -> LLM -> human). A deterministic TF-IDF + cosine-similarity
nearest-neighbor classifier, using pure numpy -- no embedding API, no
new external ML dependency (this project's own "ladder" discipline:
stdlib/already-available first). Sits between the rules stage and the
AI stage in both relevance classification and product-type
classification: a title that closely resembles already-labeled
examples (from rules OR from a real, previously-confident AI answer)
gets classified without ever calling the LLM; only genuinely novel
vocabulary reaches AI.

This is NOT a semantic embedding model -- it only recognizes vocabulary
overlap with labeled examples, same honest limitation
product_type_discovery.py's own clustering already documents ("groups by
shared vocabulary, not by understanding what a product is"). A title
using entirely different words for the same real concept won't match
here and correctly falls through to AI, which is the intended cascade
behavior, not a bug.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "for", "and", "with", "a", "an", "of", "to", "in", "on", "no",
    "is", "are", "by", "or", "this", "that", "it", "at", "as",
}


def _tokenize(text: str | None) -> list[str]:
    words = _WORD_RE.findall((text or "").lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


@dataclass
class SimilarityIndex:
    """A fitted TF-IDF index over a set of labeled reference titles."""
    vocabulary: dict[str, int]       # word -> column index
    idf: np.ndarray                  # (vocab_size,)
    vectors: np.ndarray              # (n_examples, vocab_size), L2-normalized
    labels: list[str]                # one per row of `vectors`


def build_similarity_index(titles: list[str], labels: list[str]) -> SimilarityIndex:
    """Fits a TF-IDF index over `titles` (each with a real, already-known
    `labels[i]`). Standard TF-IDF: term frequency within a title, inverse
    document frequency across the reference set (log((N+1)/(df+1)) + 1,
    the same smoothed formula scikit-learn's TfidfVectorizer uses, so
    this behaves like the library this project didn't need to add)."""
    assert len(titles) == len(labels)
    tokenized = [_tokenize(t) for t in titles]

    vocabulary: dict[str, int] = {}
    doc_freq: Counter[str] = Counter()
    for words in tokenized:
        for w in set(words):
            if w not in vocabulary:
                vocabulary[w] = len(vocabulary)
            doc_freq[w] += 1

    n_docs = len(titles)
    idf = np.zeros(len(vocabulary))
    for word, col in vocabulary.items():
        idf[col] = math.log((n_docs + 1) / (doc_freq[word] + 1)) + 1

    vectors = np.zeros((n_docs, len(vocabulary)))
    for row, words in enumerate(tokenized):
        tf = Counter(words)
        for word, count in tf.items():
            vectors[row, vocabulary[word]] = count * idf[vocabulary[word]]

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # avoid divide-by-zero for an empty/all-stopword title
    vectors = vectors / norms

    return SimilarityIndex(vocabulary=vocabulary, idf=idf, vectors=vectors, labels=list(labels))


def _vectorize_query(title: str, index: SimilarityIndex) -> np.ndarray:
    words = _tokenize(title)
    vec = np.zeros(len(index.vocabulary))
    tf = Counter(words)
    for word, count in tf.items():
        col = index.vocabulary.get(word)
        if col is not None:
            vec[col] = count * index.idf[col]
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


@dataclass
class SimilarityResult:
    label: str | None
    confidence: float  # 0-1, the winning label's best cosine similarity
    status: str         # "ok" | "no_confident_match"
    nearest_title: str | None
    basis: str


def classify_by_similarity(
    title: str, index: SimilarityIndex, min_confidence: float = 0.6, reference_titles: list[str] | None = None,
) -> SimilarityResult:
    """Nearest-neighbor lookup: cosine similarity of `title` against
    every reference vector, label = the single nearest neighbor's label,
    confidence = that cosine similarity. Below `min_confidence`, this
    correctly refuses to guess (status="no_confident_match", label=None)
    -- the whole point of this tier is not resolving what it can't,
    exactly like apply_rules() returning None defers rather than forces
    a low-confidence match. `min_confidence` is deliberately a plain
    parameter, not a hidden constant -- the right threshold is a real
    empirical/business tradeoff (precision vs. how much reaches AI),
    same as classify_listing's own config-driven thresholds.yaml."""
    if not index.labels:
        return SimilarityResult(None, 0.0, "no_confident_match", None, "empty reference index")

    query = _vectorize_query(title, index)
    similarities = index.vectors @ query
    best_idx = int(np.argmax(similarities))
    best_score = float(similarities[best_idx])

    if best_score < min_confidence:
        return SimilarityResult(
            None, best_score, "no_confident_match", None,
            f"best real cosine similarity {best_score:.2f} below {min_confidence} threshold",
        )

    nearest_title = reference_titles[best_idx] if reference_titles else None
    return SimilarityResult(
        index.labels[best_idx], best_score, "ok", nearest_title,
        f"nearest labeled neighbor (cosine similarity {best_score:.2f})",
    )

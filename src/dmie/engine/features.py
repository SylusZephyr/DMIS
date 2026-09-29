"""Shared feature extraction for discovery (M4), deduplication (M5),
simulation (M10) and recommendation (M15).

* ``extract_specs`` -- a generic, unit-driven specification parser. It
  knows physical units (rpm, W, V, mm, g, ml, nm, pack counts), not
  product categories, so it works for any industry.
* ``text_matrix`` -- TF-IDF -> TruncatedSVD dense embedding.
* ``feature_matrix`` -- text embedding + scaled numeric + spec features.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

_NUM = r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)"
SPEC_PATTERNS: dict[str, re.Pattern] = {
    "rpm": re.compile(_NUM + r"\s*(k)?\s*rpm\b", re.I),
    "watt": re.compile(_NUM + r"\s*(?:w|watts?)\b", re.I),
    "volt": re.compile(_NUM + r"\s*(?:v|volts?)\b", re.I),
    "mm": re.compile(_NUM + r"\s*mm\b", re.I),
    "gram": re.compile(_NUM + r"\s*(?:g|grams?|gr)\b", re.I),
    "ounce": re.compile(_NUM + r"\s*(?:oz|ounces?)\b", re.I),
    "ml": re.compile(_NUM + r"\s*ml\b", re.I),
    "nm": re.compile(_NUM + r"\s*nm\b", re.I),
    "pack": re.compile(_NUM + r"\s*(?:pcs|pieces?|pack|pk|count|ct|sheets?|sets?|pairs?)\b", re.I),
}
_MODEL_TOKEN = re.compile(r"\b(?=[a-z]*\d)(?=\d*[a-z])[a-z0-9]{2,10}\b", re.I)
_NOT_MODEL = re.compile(r"^\d+(k|mm|ml|g|gr|oz|w|v|nm|pcs|pc|ct|pk|x|rpm|in|cm|lb|lbs|s|th|nd|rd|st|pack)$", re.I)
_IMAGE_ID = re.compile(r"/images/I/([A-Za-z0-9+\-_%]+?)(?:\.|$)")

STOP_WORDS = "english"


def _num(text: str) -> float:
    return float(re.sub(r"[,\s]", "", text))


def extract_specs(title: str | None) -> dict:
    """Parse physical specifications from free text. Returns {} when none."""
    if not title:
        return {}
    specs: dict = {}
    for key, pat in SPEC_PATTERNS.items():
        m = pat.search(title)
        if not m:
            continue
        value = _num(m.group(1))
        if key == "rpm" and m.group(2):
            value *= 1000
        specs[key] = value
    models = [t.upper() for t in _MODEL_TOKEN.findall(title) if not _NOT_MODEL.match(t)]
    if models:
        specs["model_tokens"] = sorted(set(models))[:6]
    return specs


def spec_conflict(a: dict, b: dict, keys: tuple[str, ...] = ("rpm", "watt", "volt", "pack", "ml", "gram", "nm")) -> list[str]:
    """Spec keys present in both with materially different values."""
    out = []
    for k in keys:
        if k in a and k in b:
            x, y = a[k], b[k]
            if max(x, y) > 0 and abs(x - y) / max(x, y) > 0.05:
                out.append(k)
    return out


def image_id(url: str | None) -> str | None:
    if not url:
        return None
    m = _IMAGE_ID.search(url)
    return m.group(1) if m else url.rsplit("/", 1)[-1].split(".")[0] or None


def clean_title(title: str | None, brand: str | None = None) -> str:
    text = (title or "").lower()
    if brand:
        text = text.replace(brand.lower(), " ")
    text = re.sub(r"[^a-z0-9\s\.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def text_matrix(texts: list[str], n_components: int = 40, min_df: int = 2, seed: int = 0):
    """Return (dense L2-normalized embedding, fitted vectorizer, tfidf sparse matrix)."""
    n = len(texts)
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words=STOP_WORDS, sublinear_tf=True,
                          min_df=min_df if n >= 20 else 1, max_df=0.9 if n >= 20 else 1.0,
                          token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b")
    try:
        tfidf = vec.fit_transform(texts)
    except ValueError:  # empty vocabulary
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4))
        tfidf = vec.fit_transform([t or "empty" for t in texts])
    k = max(1, min(n_components, tfidf.shape[1] - 1, n - 1))
    if k >= 2:
        dense = TruncatedSVD(n_components=k, random_state=seed).fit_transform(tfidf)
    else:
        dense = tfidf.toarray()
    return normalize(dense), vec, tfidf


def numeric_block(frame: pd.DataFrame, specs: pd.Series) -> np.ndarray:
    """Scaled log-price + spec features (missing -> column median)."""
    cols = []
    price = np.log1p(frame["price"].astype(float))
    cols.append(price)
    for key in ("rpm", "watt", "pack"):
        cols.append(np.log1p(specs.map(lambda s, k=key: s.get(k, np.nan)).astype(float)))
    M = np.column_stack(cols)
    for j in range(M.shape[1]):
        col = M[:, j]
        if np.all(np.isnan(col)):
            M[:, j] = 0
            continue
        med = np.nanmedian(col)
        col = np.where(np.isnan(col), med, col)
        sd = col.std() or 1.0
        M[:, j] = (col - col.mean()) / sd
    return M


def feature_matrix(frame: pd.DataFrame, n_components: int = 40, numeric_weight: float = 0.35):
    """Combined text + numeric + spec embedding for clustering."""
    specs = frame["title"].map(extract_specs)
    texts = [clean_title(t, b) for t, b in zip(frame["title"], frame["brand"])]
    dense, vec, tfidf = text_matrix(texts, n_components)
    num = numeric_block(frame, specs) * (numeric_weight / np.sqrt(4))
    return np.hstack([dense, num]), vec, tfidf, specs

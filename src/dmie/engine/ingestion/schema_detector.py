"""Automatic column detection (Module 1).

Given an arbitrary tabular dataset, decide which source column holds the
title, brand, price, sales, revenue, reviews, rating, image, category,
seller and timestamp -- without assuming fixed column names.

Two independent pieces of evidence are combined per (column, field):

* header evidence -- the column name against the synonym lists in
  config/engine/schema_synonyms.yaml (exact, token-contained, fuzzy);
* value evidence  -- a statistical profile of the column's values (numeric
  share, integer share, value range, URL/image share, date-parse share,
  text length, uniqueness, ASIN pattern).

The final assignment is a one-to-one optimal matching (Hungarian
algorithm) so two fields never claim the same column. Everything is
deterministic.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from dmie.engine.config import load_yaml

MIN_ASSIGN_SCORE = 0.35

_UNIT_RE = re.compile(r"[\(\（\[].*?[\)\）\]]")
_PUNCT_RE = re.compile(r"[_\-\./:#%$]+")
_ASIN_RE = re.compile(r"^B0[0-9A-Z]{8}$|^[0-9]{9}[0-9X]$")
_URL_RE = re.compile(r"^https?://", re.I)
_IMG_RE = re.compile(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)|/images?/", re.I)


def normalize_header(name) -> str:
    text = str(name).strip().lower()
    text = _UNIT_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def synonyms() -> dict[str, list[str]]:
    raw = load_yaml("schema_synonyms.yaml")["fields"]
    return {f: [normalize_header(s) for s in syns] for f, syns in raw.items()}


def parent_level_headers() -> dict[str, list[str]]:
    """Field -> parent-level (variation family) header synonyms (schema_synonyms.yaml ``parent_level``)."""
    raw = load_yaml("schema_synonyms.yaml").get("parent_level") or {}
    return {f: [normalize_header(s) for s in syns] for f, syns in raw.items()}


def header_score(header: str, field_synonyms: list[str]) -> float:
    h = normalize_header(header)
    if not h:
        return 0.0
    best = 0.0
    h_tokens = set(h.split())
    for syn in field_synonyms:
        if h == syn:
            return 1.0
        syn_tokens = set(syn.split())
        if syn_tokens and syn_tokens <= h_tokens:
            best = max(best, 0.8 if len(syn_tokens) > 1 else 0.7)
        elif len(syn) >= 2 and not syn.isascii() and syn in h:
            best = max(best, 0.75)  # CJK headers have no spaces to tokenize on
        ratio = difflib.SequenceMatcher(None, h, syn).ratio()
        if ratio >= 0.85:
            best = max(best, 0.6)
    return best


@dataclass
class ColumnProfile:
    name: str
    non_null: float
    numeric: float
    integer: float
    min: float | None
    max: float | None
    median: float | None
    url: float
    image: float
    date: float
    avg_len: float
    unique_ratio: float
    asin: float
    has_space: float


def _to_numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = series.astype(str).str.replace(r"[,$¥€£\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def _trimmed(values: pd.Series, low: bool) -> float | None:
    if not len(values):
        return None
    v = np.sort(values.to_numpy(dtype=float))
    k = max(1, int(0.02 * len(v))) if len(v) >= 20 else 0
    return float(v[k] if low else v[len(v) - 1 - k])


def profile_column(name: str, series: pd.Series) -> ColumnProfile:
    s = series.dropna()
    s = s[s.astype(str).str.strip() != ""]
    n_total = max(len(series), 1)
    n = len(s)
    if n == 0:
        return ColumnProfile(str(name), 0, 0, 0, None, None, None, 0, 0, 0, 0, 0, 0, 0)
    sample = s.head(500)
    text = sample.astype(str).str.strip()
    nums = _to_numeric(sample)
    num_ok = nums.dropna()
    numeric = len(num_ok) / len(sample)
    integer = float((np.abs(num_ok - np.round(num_ok)) < 1e-9).mean()) if len(num_ok) else 0.0
    if pd.api.types.is_datetime64_any_dtype(sample):
        date = 1.0
    elif numeric > 0.8:
        date = 0.0
    else:
        parsed = pd.to_datetime(text, errors="coerce", format="mixed")
        date = float(parsed.notna().mean())
    return ColumnProfile(
        name=str(name),
        non_null=n / n_total,
        numeric=numeric,
        integer=integer,
        # robust range: a few bad rows (a -5 price, a 9-star rating) must not hide the column -- with 20+
        # values the lowest/highest max(1, 2%) are ignored (an interpolated quantile still lands on a single
        # outlier in a small sample); the bad values are reported by data-quality checks instead
        min=_trimmed(num_ok, low=True),
        max=_trimmed(num_ok, low=False),
        median=float(num_ok.median()) if len(num_ok) else None,
        url=float(text.str.match(_URL_RE).mean()),
        image=float(text.str.contains(_IMG_RE).mean()),
        date=date,
        avg_len=float(text.str.len().mean()),
        unique_ratio=s.astype(str).nunique() / n,
        asin=float(text.str.upper().str.match(_ASIN_RE).mean()),
        has_space=float(text.str.contains(" ").mean()),
    )


def value_score(field_name: str, p: ColumnProfile) -> float:
    """How plausible are this column's values for the given field? 0..1."""
    if p.non_null == 0:
        return 0.0
    num_like = p.numeric > 0.9 and p.date < 0.5
    text_like = p.numeric < 0.2 and p.url < 0.2 and p.date < 0.5
    if field_name == "id":
        if p.asin > 0.8:
            return 1.0
        return 0.5 if (p.unique_ratio > 0.95 and p.has_space < 0.05 and p.url < 0.1 and p.avg_len < 40 and p.numeric < 0.5) else 0.0
    if field_name == "title":
        return min(1.0, p.avg_len / 60) * (0.9 if p.has_space > 0.8 else 0.3) if text_like and p.unique_ratio > 0.3 else 0.0
    if field_name == "brand":
        return 0.45 if text_like and p.avg_len < 25 and p.unique_ratio < 0.9 else 0.0
    if field_name in ("seller", "category"):
        return 0.3 if text_like and p.avg_len < 40 and p.unique_ratio < 0.6 else 0.0
    if field_name == "description":
        return 0.4 if text_like and p.avg_len > 150 else 0.0
    if field_name == "review_text":
        return 0.3 if text_like and p.avg_len > 60 else 0.0
    if field_name == "image":
        return 1.0 if p.image > 0.7 else 0.0
    if field_name == "url":
        return 0.8 if p.url > 0.7 and p.image < 0.3 else 0.0
    if field_name == "timestamp":
        return 0.9 if p.date > 0.8 else 0.0
    if field_name == "launch_date":
        return 0.4 if p.date > 0.8 else 0.0
    if not num_like or p.min is None:
        return 0.0
    if field_name == "rating":
        return 0.85 if 0 <= p.min and p.max <= 5 and p.integer < 0.9 else 0.0
    if field_name == "price":
        return 0.55 if p.min >= 0 and p.integer < 0.7 and (p.median or 0) < 20000 else 0.15
    if field_name == "sales":
        return 0.45 if p.min >= 0 and p.integer > 0.9 else 0.1
    if field_name == "reviews":
        return 0.4 if p.min >= 0 and p.integer > 0.95 else 0.0
    if field_name == "revenue":
        return 0.35 if p.min >= 0 else 0.0
    if field_name == "bsr":
        # whole-number ranks >= 1; the sub-category rank (smaller numbers) is preferred over the whole-category one
        if p.min < 1 or p.integer < 0.95:
            return 0.0
        return 0.45 + (0.05 if (p.median or 0) < 5000 else 0.0)
    return 0.0


@dataclass
class DetectionResult:
    mapping: dict[str, str]                     # universal field -> source column
    confidence: dict[str, float]                # universal field -> 0..1
    unmapped_columns: list[str]
    warnings: list[str] = field(default_factory=list)
    candidates: dict[str, list[tuple[str, float]]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mapping": self.mapping,
            "confidence": {k: round(v, 3) for k, v in self.confidence.items()},
            "unmapped_columns": self.unmapped_columns,
            "warnings": self.warnings,
        }


HEADER_ONLY = {"bsr"}


def detect_schema(df: pd.DataFrame, overrides: dict[str, str] | None = None) -> DetectionResult:
    """Detect the universal-field -> column mapping for ``df``.

    ``overrides`` (field -> column) pins assignments, e.g. from a human
    correcting the detector in the dashboard.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v in df.columns}
    syn = synonyms()
    fields = list(syn.keys())
    columns = [c for c in df.columns if c not in overrides.values()]
    profiles = {c: profile_column(c, df[c]) for c in columns}

    scores = np.zeros((len(fields), len(columns)))
    for i, f in enumerate(fields):
        if f in overrides:
            continue
        for j, c in enumerate(columns):
            h = header_score(c, syn[f])
            v = value_score(f, profiles[c])
            if h > 0:
                # A header match is only trusted if the values don't flatly contradict it.
                s = 0.7 * h + 0.3 * v if (v > 0 or f in ("brand", "seller", "category", "id", "description", "review_text")) else 0.35 * h
            else:
                # a rank is recognised by its header only: whole numbers >= 1 look like counts, weights, ids...
                s = 0.0 if f in HEADER_ONLY else 0.6 * v
            scores[i, j] = s

    # a parent-level column (family total, parent ASIN) never stands in for the listing's own id / sales / revenue
    # while the file has a child-level column for that field; without one it may, flagged below
    parents = parent_level_headers()
    parent_of = {c: next((f for f, ps in parents.items() if header_score(c, ps) >= 0.75), None) for c in columns}
    for i, f in enumerate(fields):
        if f in overrides:
            continue
        child = any(parent_of[c] is None and header_score(c, syn[f]) > 0 for c in columns)
        for j, c in enumerate(columns):
            if parent_of[c] is None:
                continue
            scores[i, j] = (0.5 * (0.7 + 0.3 * value_score(f, profiles[c])) if parent_of[c] == f and not child else 0.0)

    mapping: dict[str, str] = dict(overrides)
    confidence: dict[str, float] = {f: 1.0 for f in overrides}
    candidates: dict[str, list[tuple[str, float]]] = {}
    for i, f in enumerate(fields):
        order = np.argsort(-scores[i])[:3]
        candidates[f] = [(str(columns[j]), round(float(scores[i, j]), 3)) for j in order if scores[i, j] > 0]

    if columns:
        rows, cols = linear_sum_assignment(-scores)
        for i, j in zip(rows, cols):
            f = fields[i]
            if f in overrides:
                continue
            if scores[i, j] >= MIN_ASSIGN_SCORE:
                mapping[f] = columns[j]
                confidence[f] = float(min(1.0, scores[i, j]))

    _infer_price_revenue(df, mapping, confidence, profiles)
    warnings = _validate_revenue(df, mapping, confidence)
    for f in parents:
        c = mapping.get(f)
        if c is not None and f not in overrides and parent_of.get(c) is not None:
            warnings.append(f"'{c}' is a parent-level (variation family) column used as {f}: correct only if each row "
                            "is one parent; on child rows the family total would be counted once per child")
    for required in ("title",):
        if required not in mapping:
            warnings.append(f"no column detected for required field '{required}'")
    for f in ("price", "sales"):
        if f not in mapping:
            warnings.append(f"no column detected for '{f}' -- market metrics will be limited")
    used = set(mapping.values())
    unmapped = [str(c) for c in df.columns if c not in used]
    return DetectionResult(mapping, confidence, unmapped, warnings, candidates)


def _consistency(price: pd.Series, sales: pd.Series, rev: pd.Series) -> float:
    ok = price.notna() & sales.notna() & rev.notna() & (rev > 0) & (sales > 0)
    if ok.sum() < 5:
        return 0.0
    ratio = (price[ok] * sales[ok]) / rev[ok]
    return float(((ratio > 0.6) & (ratio < 1.6)).mean())


def _infer_price_revenue(df: pd.DataFrame, mapping: dict, confidence: dict, profiles: dict) -> None:
    """Unknown headers: find the numeric columns that satisfy
    revenue ~= price * sales. The identity itself is the evidence."""
    if "sales" not in mapping or ("price" in mapping and "revenue" in mapping):
        return
    used = set(mapping.values())
    numeric = [c for c, p in profiles.items() if c not in used and p.numeric > 0.9 and p.date < 0.5 and (p.min or 0) >= 0]
    sales = _to_numeric(df[mapping["sales"]])
    cols = {c: _to_numeric(df[c]) for c in numeric}
    if "price" in mapping:
        cols[mapping["price"]] = _to_numeric(df[mapping["price"]])
    if "revenue" in mapping:
        cols[mapping["revenue"]] = _to_numeric(df[mapping["revenue"]])
    price_opts = [mapping["price"]] if "price" in mapping else numeric
    rev_opts = [mapping["revenue"]] if "revenue" in mapping else numeric
    best = (0.0, None, None)
    for pc in price_opts:
        for rc in rev_opts:
            if pc == rc:
                continue
            score = _consistency(cols[pc], sales, cols[rc])
            if score > best[0]:
                best = (score, pc, rc)
    score, pc, rc = best
    if score >= 0.6:
        for f, c in (("price", pc), ("revenue", rc)):
            if f not in mapping:
                mapping[f] = c
                confidence[f] = round(0.5 + 0.5 * score, 3)


def _validate_revenue(df: pd.DataFrame, mapping: dict, confidence: dict) -> list[str]:
    """Cross-check revenue ~= price * sales; a strong correlation confirms
    the revenue column even when its header was unknown."""
    warnings: list[str] = []
    if not {"price", "sales", "revenue"} <= mapping.keys():
        return warnings
    price = _to_numeric(df[mapping["price"]])
    sales = _to_numeric(df[mapping["sales"]])
    rev = _to_numeric(df[mapping["revenue"]])
    ok = price.notna() & sales.notna() & rev.notna() & (rev > 0)
    if ok.sum() >= 5:
        ratio = (price[ok] * sales[ok]) / rev[ok]
        consistent = float(((ratio > 0.6) & (ratio < 1.6)).mean())
        confidence["revenue"] = max(confidence["revenue"], consistent)
        if consistent < 0.5:
            warnings.append(
                f"revenue column '{mapping['revenue']}' is inconsistent with price*sales for "
                f"{1 - consistent:.0%} of rows"
            )
    return warnings

"""Supplier Intelligence (Module 12).

A supplier database that is only ever populated from user-supplied data
(CSV/XLSX import -- see data/templates/suppliers_template.csv -- or the
dashboard form). Nothing is fabricated: an empty table means no supplier
data has been provided yet.

Supplier score (0-100, weights in config/engine/engine.yaml):
manufacturer vs trader, OEM, ODM, certifications (ISO 13485, CE, FDA,
MDR ...), and category match to the market's discovered segments.

Segment matching uses TF-IDF cosine similarity between the supplier's
product categories and each segment's label/top terms.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from dmie.engine.config import section

SUPPLIER_COLUMNS = ["supplier_id", "name", "country", "website", "business_type", "oem", "odm",
                    "certifications", "product_categories", "contact", "notes"]
KNOWN_CERTS = {"iso13485": 1.0, "iso 13485": 1.0, "fda": 1.0, "510k": 1.0, "ce": 0.8, "mdr": 0.9,
               "iso9001": 0.5, "iso 9001": 0.5, "gmp": 0.7, "nmpa": 0.6, "cfda": 0.6, "health canada": 0.7}
_ALIASES = {
    "name": ["name", "supplier", "supplier name", "company", "company name", "供应商", "公司名称"],
    "country": ["country", "region", "location", "国家", "地区"],
    "website": ["website", "url", "site", "web", "网址"],
    "business_type": ["business_type", "business type", "type", "manufacturer/trader", "类型"],
    "oem": ["oem", "oem capability"],
    "odm": ["odm", "odm capability"],
    "certifications": ["certifications", "certification", "certs", "认证"],
    "product_categories": ["product_categories", "product categories", "categories", "products", "main products", "主营产品"],
    "contact": ["contact", "email", "phone", "联系方式"],
    "notes": ["notes", "note", "remarks", "备注"],
}


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "是", "有", "x", "✓"}


def normalize_suppliers(raw: pd.DataFrame) -> pd.DataFrame:
    cols = {str(c).strip().lower(): c for c in raw.columns}
    out = pd.DataFrame(index=raw.index)
    for field, aliases in _ALIASES.items():
        src = next((cols[a] for a in aliases if a in cols), None)
        out[field] = raw[src] if src is not None else None
    out = out[out["name"].notna() & (out["name"].astype(str).str.strip() != "")]
    out["oem"] = out["oem"].map(_truthy)
    out["odm"] = out["odm"].map(_truthy)
    for c in ("country", "website", "business_type", "certifications", "product_categories", "contact", "notes"):
        out[c] = out[c].where(out[c].notna(), None)
    out["supplier_id"] = out["name"].map(lambda n: "S" + hashlib.sha1(str(n).strip().lower().encode()).hexdigest()[:10])
    return out[SUPPLIER_COLUMNS].drop_duplicates("supplier_id").reset_index(drop=True)


def cert_score(certs) -> float:
    if not isinstance(certs, str) or not certs.strip():
        return 0.0
    text = certs.lower()
    hits = [w for k, w in KNOWN_CERTS.items() if re.search(r"\b" + re.escape(k) + r"\b", text)]
    return float(min(1.0, sum(sorted(hits, reverse=True)[:2]) / 1.8)) if hits else 0.1


def match_segments(suppliers: pd.DataFrame, segments: pd.DataFrame, min_score: float = 0.08) -> pd.DataFrame:
    """Supplier x segment similarity (TF-IDF cosine)."""
    cols = ["supplier_id", "segment_id", "segment_label", "match_score"]
    if suppliers.empty or segments.empty:
        return pd.DataFrame(columns=cols)
    seg_text = (segments["segment_label"].fillna("") + " " +
                segments.get("top_terms", pd.Series([[]] * len(segments))).map(lambda t: " ".join(t) if isinstance(t, list) else "") + " " +
                segments.get("family_label", pd.Series([""] * len(segments))).fillna("")).str.lower().tolist()
    sup_text = suppliers["product_categories"].fillna("").astype(str).str.lower().tolist()
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True).fit(seg_text + sup_text)
    S = cosine_similarity(vec.transform(sup_text), vec.transform(seg_text))
    rows = []
    for i, sid in enumerate(suppliers["supplier_id"]):
        for j in np.argsort(-S[i])[:5]:
            if S[i, j] >= min_score:
                rows.append({"supplier_id": sid, "segment_id": segments["segment_id"].iloc[j],
                             "segment_label": segments["segment_label"].iloc[j], "match_score": round(float(S[i, j]), 4)})
    return pd.DataFrame(rows, columns=cols)


def score_suppliers(suppliers: pd.DataFrame, segments: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    w = section("suppliers").get("weights", {})
    s = suppliers.copy()
    if s.empty:
        s["supplier_score"] = pd.Series(dtype="float64")
        return s, pd.DataFrame(columns=["supplier_id", "segment_id", "segment_label", "match_score"])
    btype = s["business_type"].fillna("").astype(str).str.lower()
    manuf = np.select([btype.str.contains("manufactur|factory|工厂|生产"), btype.str.contains("trad|distribut|贸易")], [1.0, 0.3], 0.5)
    matches = match_segments(s, segments) if segments is not None else pd.DataFrame(columns=["supplier_id", "match_score"])
    best = matches.groupby("supplier_id")["match_score"].max() if len(matches) else pd.Series(dtype=float)
    cat = s["supplier_id"].map(best).fillna(0).clip(0, 0.6) / 0.6
    certs = s["certifications"].map(cert_score)
    total = (w.get("manufacturer", .25) * manuf + w.get("oem", .15) * s["oem"].astype(float)
             + w.get("odm", .15) * s["odm"].astype(float) + w.get("certifications", .25) * certs
             + w.get("category_match", .20) * cat)
    s["supplier_score"] = np.round(100 * total / sum(w.values() or [1]), 1)
    s["category_match"] = cat.round(3)
    s["cert_score"] = certs.round(3)
    return s.sort_values("supplier_score", ascending=False).reset_index(drop=True), matches

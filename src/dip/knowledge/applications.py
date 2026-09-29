"""Dental application ontology and Dental Confidence (spec 11, 13.1, 89-90).

Dental Confidence (0-100) answers "how sure are we this product belongs to the dental industry?":

    score = sum(w_i * c_i) / sum(w_i)   over the components that HAVE evidence
            text 0.30 + application 0.25 + image 0.20 + specification 0.15 + industry 0.10

* text           dental vs non-dental vocabulary in title, sub-category and description, averaged with the
                 platform's trained relevance model (TF-IDF prototypes + human feedback) when present
* application    the dental application classes the text names (multi-label, spec 90)
* image          readable words in the image reference (opaque marketplace IDs are "no evidence";
                 a vision model can fill this component later)
* specification  dental-specific attributes extracted for the market's schema (``attributes``)
* industry       the brand appears in an offline industry registry (manufacturers, exhibitors)

A component without evidence is dropped and the rest re-weighted: low online evidence never counts
as "non-dental" (spec 11). The score is capped when only one or two components carry evidence.
Human decisions (relevance corrections, category-scope decisions) are reported as ``dental_verified``
and always win over the score.
"""

from __future__ import annotations

import glob
import re
from functools import lru_cache

import numpy as np
import pandas as pd

from dip.knowledge import config
from dip.knowledge.attributes import _term_rx
from dip.settings import PROJECT_ROOT

_WORD = re.compile(r"[a-z]{4,}")


@lru_cache(maxsize=1)
def _vocab() -> dict:
    cfg = config()
    apps = cfg["applications"]
    return {
        "dental": [(t, _term_rx([t])) for t in cfg["dental_terms"]],
        "non_dental": [(t, _term_rx([t])) for t in cfg["non_dental_terms"]],
        "apps": {name: _term_rx([str(t) for t in terms]) for name, terms in apps.items()},
    }


@lru_cache(maxsize=1)
def industry_brands() -> frozenset:
    """Brands found in offline industry registries (CSV with a ``brand`` column). Empty when none exist."""
    pat = config()["dental_confidence"].get("industry_registry_glob")
    names: set[str] = set()
    for f in glob.glob(str(PROJECT_ROOT / pat)) if pat else []:
        try:
            df = pd.read_csv(f)
        except (OSError, ValueError):
            continue
        if "brand" in df:
            names |= {str(x).strip().lower() for x in df["brand"].dropna()}
    return frozenset(names)


def _terms_in(text: str | None, table) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    return [t for t, rx in table if rx.search(text)]


def text_component(row: dict) -> tuple[float | None, dict]:
    cfg = config()["dental_confidence"]["text"]
    v = _vocab()
    fields = {"title": cfg["title_weight"], "category": cfg["category_weight"], "description": cfg["description_weight"]}
    d = n = 0.0
    hits: dict[str, list[str]] = {"dental": [], "non_dental": []}
    for f, w in fields.items():
        dt, nt = _terms_in(row.get(f), v["dental"]), _terms_in(row.get(f), v["non_dental"])
        d += w * len(dt)
        n += w * len(nt)
        hits["dental"] += [t for t in dt if t not in hits["dental"]]
        hits["non_dental"] += [t for t in nt if t not in hits["non_dental"]]
    lex = None
    if d or n:
        purity = d / (d + n)
        strength = min(1.0, d / cfg["saturation"])
        lex = purity * (0.5 + 0.5 * strength) if d else 0.0
    model = row.get("relevance_score")
    model = float(model) / 100.0 if model is not None and not pd.isna(model) else None
    parts = [x for x in (lex, model) if x is not None]
    return (float(np.mean(parts)) if parts else None), {**hits, "lexicon": lex, "model": model}


def application_labels(row: dict) -> dict[str, list[str]]:
    """Application class -> the terms that named it (multi-label)."""
    out: dict[str, list[str]] = {}
    text = " | ".join(str(row.get(f)) for f in ("title", "category", "description") if isinstance(row.get(f), str))
    for name, rx in _vocab()["apps"].items():
        found = sorted({m.group(0).lower() for m in rx.finditer(text)})
        if found:
            out[name] = found
    return out


def application_component(apps: dict) -> float | None:
    if not apps:
        return None
    dental = [a for a in apps if a != "general_laboratory"]
    if dental and "general_laboratory" not in apps:
        return 1.0
    return 0.5 if dental else 0.2


def image_component(row: dict) -> tuple[float | None, list[str]]:
    img = row.get("image")
    if not isinstance(img, str) or not img:
        return None, []
    words = " ".join(_WORD.findall(img.lower().rsplit("/", 1)[-1]))
    if not words:
        return None, []
    v = _vocab()
    dt, nt = _terms_in(words, v["dental"]), _terms_in(words, v["non_dental"])
    if not dt and not nt:
        return None, []
    return (1.0 if dt and not nt else 0.0 if nt and not dt else 0.5), dt + nt


def spec_component(row: dict) -> float | None:
    cov = row.get("attribute_coverage")
    if cov is None or pd.isna(cov) or not row.get("_schema_specific"):
        return None
    return float(min(1.0, 0.5 + cov)) if cov > 0 else None


def industry_component(row: dict) -> float | None:
    reg = industry_brands()
    brand = row.get("brand")
    if not reg or not isinstance(brand, str):
        return None
    return 1.0 if brand.strip().lower() in reg else None   # absence from a registry is not evidence


def band(score: float | None) -> str:
    if score is None:
        return "review"
    for lo, label in config()["dental_confidence"]["bands"]:
        if score >= lo:
            return label
    return "non_dental"


def score_row(row: dict) -> dict:
    cfg = config()["dental_confidence"]
    w = cfg["weights"]
    text, text_ev = text_component(row)
    apps = application_labels(row)
    img, img_terms = image_component(row)
    comps = {"text": text, "application": application_component(apps), "image": img,
             "specification": spec_component(row), "industry": industry_component(row)}
    present = {k: v for k, v in comps.items() if v is not None}
    if present:
        score = 100.0 * sum(w[k] * v for k, v in present.items()) / sum(w[k] for k in present)
        cap = (cfg.get("cap_by_components") or {}).get(len(present))
        if cap is not None:
            score = min(score, float(cap))
        score = round(score, 1)
    else:
        score = None
    status = str(row.get("relevance_status") or "")
    verified = True if status == "human_relevant" else False if status == "human_irrelevant" else None
    b = band(score)
    if verified is True:
        b = "verified_dental"
    elif verified is False:
        b = "verified_non_dental"
    dental_apps = [a for a in apps if a != "general_laboratory"]
    primary = max(dental_apps, key=lambda a: (len(apps[a]), a)) if dental_apps else None
    if primary is None:
        primary = "non_dental" if b in ("non_dental", "probably_non_dental", "verified_non_dental") else "ambiguous"
    return {"dental_confidence": score, "dental_band": b, "dental_verified": verified,
            "dental_components": {k: (None if v is None else round(v, 3)) for k, v in comps.items()},
            "applications": sorted(apps), "primary_application": primary,
            "dental_evidence": {"dental_terms": text_ev["dental"][:8], "non_dental_terms": text_ev["non_dental"][:8],
                                "application_terms": {k: v[:4] for k, v in apps.items()}, "image_terms": img_terms[:4],
                                "model_score": text_ev["model"]}}


def score(frame: pd.DataFrame, schema_specific: bool) -> pd.DataFrame:
    """Dental Confidence for every row (same index). ``schema_specific`` says whether the market has its
    own attribute schema (only then is extracted-spec coverage evidence of a dental product)."""
    cols = [c for c in ("title", "category", "description", "image", "brand", "relevance_score", "relevance_status",
                        "attribute_coverage") if c in frame]
    rows = frame[cols].to_dict("records")
    res = [score_row({**r, "_schema_specific": schema_specific}) for r in rows]
    import json
    return pd.DataFrame({
        "dental_confidence": [r["dental_confidence"] for r in res],
        "dental_band": [r["dental_band"] for r in res],
        "dental_verified": [r["dental_verified"] for r in res],
        "dental_components": [json.dumps(r["dental_components"]) for r in res],
        "applications": [",".join(r["applications"]) or None for r in res],
        "primary_application": [r["primary_application"] for r in res],
        "dental_evidence": [json.dumps(r["dental_evidence"], ensure_ascii=False) for r in res],
    }, index=frame.index)

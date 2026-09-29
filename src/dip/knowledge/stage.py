"""Pipeline stage ``knowledge``: attributes, component role, Dental Confidence, applications, provenance.

Runs after the metrics engine, on every imported record (so excluded and uncertain listings also get a
dental classification, spec 168 A) and rolls the listing results up to canonical products:

* product dental confidence = median over its listings (band from the median), primary application = the
  most common one, configuration key / role = the most common one, attributes = the value most listings
  agree on, with the share that agrees (``attribute_agreement``)
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dip.knowledge import applications, attributes, llm_extract, provenance

KN_LISTING_COLS = ["kn_attributes", "attribute_coverage", "extraction_confidence", "component_role", "component_role_confidence",
                   "component_role_reason", "configuration_key", "attribute_conflicts", "needs_llm", "dental_confidence",
                   "dental_band", "dental_verified", "dental_components", "applications", "primary_application", "dental_evidence"]


@dataclass
class KnowledgeResult:
    records: pd.DataFrame          # knowledge columns for every record (index = record frame index)
    listings: pd.DataFrame
    products: pd.DataFrame
    observations: pd.DataFrame
    evidence: pd.DataFrame
    schema: str
    llm: dict | None = None

    def summary(self) -> dict:
        r = self.records
        return {"schema": self.schema, "records": int(len(r)),
                "dental_band": r["dental_band"].value_counts().to_dict() if len(r) else {},
                "roles": r["component_role"].value_counts().to_dict() if len(r) else {},
                "attribute_coverage": round(float(r["attribute_coverage"].mean()), 3) if len(r) and r["attribute_coverage"].notna().any() else None,
                "needs_llm": int(r["needs_llm"].sum()) if len(r) else 0,
                "observations": int(len(self.observations)), "evidence": int(len(self.evidence)), "llm": self.llm}


def _mode(values) -> object:
    vals = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
    return Counter(vals).most_common(1)[0][0] if vals else None


def _consensus(attr_jsons: list[str]) -> tuple[dict, dict]:
    """Per attribute: the value most listings agree on, and the share of listings with that attribute agreeing."""
    by: dict[str, list] = {}
    for s in attr_jsons:
        a = json.loads(s) if isinstance(s, str) else {}
        for k, v in a.items():
            val = v.get("value")
            by.setdefault(k, []).append(json.dumps(val) if isinstance(val, list) else val)
    out, agree = {}, {}
    for k, vals in by.items():
        top, n = Counter(vals).most_common(1)[0]
        out[k] = json.loads(top) if isinstance(top, str) and top.startswith("[") else top
        agree[k] = round(n / len(vals), 3)
    return out, agree


def rollup_products(products: pd.DataFrame, listings: pd.DataFrame) -> pd.DataFrame:
    L = listings[["product_id"] + [c for c in KN_LISTING_COLS if c in listings]].dropna(subset=["product_id"])
    rows = []
    for pid, g in L.groupby("product_id", sort=False):
        dc = pd.to_numeric(g["dental_confidence"], errors="coerce")
        med = float(dc.median()) if dc.notna().any() else None
        cons, agree = _consensus(g["kn_attributes"].tolist())
        verified = g["dental_verified"].dropna()
        rows.append({"product_id": pid, "dental_confidence": None if med is None else round(med, 1),
                     "dental_band": ("verified_dental" if len(verified) and verified.all() else
                                     "verified_non_dental" if len(verified) and not verified.any() else applications.band(med)),
                     "primary_application": _mode(g["primary_application"]),
                     "applications": ",".join(sorted({a for s in g["applications"].dropna() for a in s.split(",")})) or None,
                     "configuration_key": _mode(g["configuration_key"]), "component_role": _mode(g["component_role"]),
                     "kn_attributes": json.dumps(cons, ensure_ascii=False), "attribute_agreement": json.dumps(agree),
                     "extraction_confidence": round(float(pd.to_numeric(g["extraction_confidence"], errors="coerce").mean()), 3)})
    kn = pd.DataFrame(rows)
    base = products.drop(columns=[c for c in kn.columns if c != "product_id" and c in products], errors="ignore")
    return base.merge(kn, on="product_id", how="left") if len(kn) else base


def build(records: pd.DataFrame, listings: pd.DataFrame, products: pd.DataFrame, segments: pd.DataFrame, market: str,
          dataset_id: str, source: str, observed_at: str, category_summary: dict | None) -> KnowledgeResult:
    schema = attributes.schema_name(market)
    at = attributes.extract(records, market)
    at, llm_summary = llm_extract.fill(records, at, market)       # optional tier: unavailable without a provider key
    rec = pd.concat([records[[c for c in ("record_id", "title", "category", "description", "image", "brand",
                                          "relevance_score", "relevance_status") if c in records]], at], axis=1)
    dc = applications.score(rec, schema_specific=schema != "generic")
    kn_rec = pd.concat([at, dc], axis=1)
    kn_rec["record_id"] = records["record_id"].to_numpy()
    by_rec = kn_rec.set_index("record_id")
    by_rec = by_rec[~by_rec.index.duplicated(keep="last")]      # a record id can repeat in a raw export
    L = listings.drop(columns=[c for c in by_rec.columns if c in listings], errors="ignore")
    L = L.join(by_rec, on="record_id")
    P = rollup_products(products, L)
    obs = provenance.observations(L, P, segments, category_summary or {}, market, source, dataset_id, observed_at)
    ev = provenance.evidence(L, source, observed_at)
    return KnowledgeResult(kn_rec.drop(columns=["record_id"]), L, P, obs, ev, schema, llm_summary)

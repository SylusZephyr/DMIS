"""Market observations and evidence (spec 14, 80, 81; architectural rules 3 and 7).

``observations`` -- one row per (entity, metric) value the platform shows, never overwritten between runs
(each run appends under its ``dataset_id``; ``observation_history`` keeps them all):

    entity_type  listing | product | segment | category
    metric       price | rating | sales | revenue | units_est | revenue_est | hhi | ...
    value, lo, hi, unit
    kind         observed  -- read from the source as-is (a listed price, a rating)
                 estimated -- an external estimate taken as-is (SellerSprite sales are estimates, spec 92)
                 modeled   -- computed by the platform's statistical model (interval-censored demand)
                 derived   -- arithmetic over other values (a median, a sum, a count)
    source, source_record_id, dataset_id, observed_at, method, confidence (0-1)

``evidence`` -- the reasons behind judgments (dental relevance terms, extracted attribute spans, the
application terms), each with the source text excerpt, so "why does the system believe this?" can be
answered from records instead of prose (spec 14).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

OBS_COLUMNS = ["entity_type", "entity_id", "metric", "value", "lo", "hi", "unit", "kind", "source", "source_record_id",
               "dataset_id", "observed_at", "method", "confidence"]
EVIDENCE_COLUMNS = ["entity_type", "entity_id", "evidence_type", "subject", "source", "source_url", "source_record_id",
                    "text_excerpt", "captured_at", "confidence"]

# how each listing-level column was produced
_LISTING_METRICS = [
    # column, metric, unit, kind, method
    ("price", "price", "USD", "observed", "listed price in the source export"),
    ("rating", "rating", "stars", "observed", "average star rating in the source export"),
    ("sales", "sales", "units/month", "estimated", "marketplace sales estimate as exported (a badge rung / tool estimate, not an observed count)"),
    ("revenue", "revenue", "USD/month", "estimated", "marketplace revenue estimate as exported"),
]


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _rows(df: pd.DataFrame, entity: str, id_col: str, metric: str, col: str, unit: str, kind: str, method: str,
          source: str, dataset_id: str, observed_at: str, conf, lo_col: str | None = None, hi_col: str | None = None,
          rec_col: str | None = None) -> pd.DataFrame:
    if col not in df:
        return pd.DataFrame(columns=OBS_COLUMNS)
    v = pd.to_numeric(df[col], errors="coerce")
    m = v.notna().to_numpy()
    if not m.any():
        return pd.DataFrame(columns=OBS_COLUMNS)
    sub = df.loc[m]
    out = pd.DataFrame({
        "entity_type": entity, "entity_id": sub[id_col].astype(str).to_numpy(), "metric": metric,
        "value": v[m].to_numpy(dtype=float),
        "lo": pd.to_numeric(sub[lo_col], errors="coerce").to_numpy(dtype=float) if lo_col and lo_col in sub else np.nan,
        "hi": pd.to_numeric(sub[hi_col], errors="coerce").to_numpy(dtype=float) if hi_col and hi_col in sub else np.nan,
        "unit": unit, "kind": kind, "source": source,
        "source_record_id": sub[rec_col].astype(str).to_numpy() if rec_col and rec_col in sub else None,
        "dataset_id": dataset_id, "observed_at": observed_at, "method": method,
        "confidence": (pd.to_numeric(sub[conf], errors="coerce").to_numpy(dtype=float) if isinstance(conf, str) else conf),
    })
    return out[OBS_COLUMNS]


def observations(listings: pd.DataFrame, products: pd.DataFrame, segments: pd.DataFrame, category: dict,
                 market: str, source: str, dataset_id: str, observed_at: str) -> pd.DataFrame:
    """Observation rows for the values this run shows, each labelled with how it was produced."""
    L = listings.copy()
    # listing confidence (0-1): data confidence of the record where present
    L["_conf"] = pd.to_numeric(L.get("data_confidence"), errors="coerce") / 100.0 if "data_confidence" in L else np.nan
    parts = []
    for col, metric, unit, kind, method in _LISTING_METRICS:
        if col == "sales" and "sales_observation" in L:
            method = method + "; observation: " + L["sales_observation"].fillna("n/a").astype(str).mode().iat[0]
        parts.append(_rows(L, "listing", "id", metric, col, unit, kind, method, source, dataset_id, observed_at, "_conf",
                           rec_col="record_id"))
    parts.append(_rows(L, "listing", "id", "units_est", "units_est", "units/month", "modeled",
                       "interval-censored demand model (metrics v3): posterior mean with a 95% interval",
                       "platform", dataset_id, observed_at, "_conf", "units_lo", "units_hi", "record_id"))
    parts.append(_rows(L, "listing", "id", "revenue_est", "revenue_est", "USD/month", "modeled",
                       "modeled units x listed price", "platform", dataset_id, observed_at, "_conf", rec_col="record_id"))
    P = products.copy()
    P["_conf"] = pd.to_numeric(P.get("confidence_score"), errors="coerce") / 100.0 if "confidence_score" in P else np.nan
    for col, metric, unit, kind, method, lo, hi in [
        ("units_est", "units_est", "units/month", "modeled", "sum of the product's listing estimates (canonical product; no double count)", "units_lo", "units_hi"),
        ("revenue_est", "revenue_est", "USD/month", "modeled", "sum of the product's listing revenue estimates", "revenue_lo", "revenue_hi"),
        ("price_median", "price_median", "USD", "derived", "median listed price across the product's listings", "price_min", "price_max"),
        ("listing_count", "listings", "count", "derived", "listings resolved to this canonical product", None, None),
        ("sellers", "sellers", "count", "derived", "distinct sellers of the product's listings", None, None),
    ]:
        parts.append(_rows(P, "product", "product_id", metric, col, unit, kind, method, "platform", dataset_id, observed_at,
                           "_conf", lo, hi))
    S = segments.copy()
    S["_conf"] = pd.to_numeric(S.get("confidence_score"), errors="coerce") / 100.0 if "confidence_score" in S else np.nan
    for col, metric, unit, kind, method, lo, hi in [
        ("revenue_est", "revenue_est", "USD/month", "modeled", "joint simulation over the segment's canonical products", "revenue_lo", "revenue_hi"),
        ("units_est", "units_est", "units/month", "modeled", "sum of canonical-product unit estimates", None, None),
        ("hhi_est", "hhi", "HHI", "modeled", "brand concentration on modeled revenue", None, None),
        ("price_median", "price_median", "USD", "derived", "median listed price", "price_min", "price_max"),
        ("products", "products", "count", "derived", "canonical products in the segment", None, None),
        ("listings", "listings", "count", "derived", "listings in the segment", None, None),
    ]:
        parts.append(_rows(S, "segment", "segment_id", metric, col, unit, kind, method, "platform", dataset_id, observed_at,
                           "_conf", lo, hi))
    cat_rows = []
    for key, metric, unit in [("revenue_month", "revenue_est", "USD/month"), ("units_month", "units_est", "units/month")]:
        v = (category or {}).get(key)
        if isinstance(v, dict) and _f(v.get("estimate")) is not None:
            cat_rows.append({"entity_type": "category", "entity_id": market, "metric": metric,
                             "value": _f(v.get("estimate")), "lo": _f(v.get("low")), "hi": _f(v.get("high")),
                             "unit": unit, "kind": "modeled", "source": "platform", "source_record_id": None,
                             "dataset_id": dataset_id, "observed_at": observed_at,
                             "method": "market total from the demand model, aggregated over canonical products", "confidence": None})
    if cat_rows:
        parts.append(pd.DataFrame(cat_rows, columns=OBS_COLUMNS))
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=OBS_COLUMNS)


def evidence(listings: pd.DataFrame, source: str, captured_at: str) -> pd.DataFrame:
    """Evidence rows from the knowledge columns of the listings (dental terms, application terms, attribute spans)."""
    rows = []
    cols = [c for c in ("id", "record_id", "url", "dental_evidence", "kn_attributes", "dental_confidence") if c in listings]
    for r in listings[cols].to_dict("records"):
        base = {"entity_type": "listing", "entity_id": str(r["id"]), "source": source, "source_url": r.get("url"),
                "source_record_id": r.get("record_id"), "captured_at": captured_at}
        ev = json.loads(r["dental_evidence"]) if isinstance(r.get("dental_evidence"), str) else {}
        if ev.get("dental_terms") or ev.get("non_dental_terms"):
            rows.append({**base, "evidence_type": "dental_relevance", "subject": "dental_confidence",
                         "text_excerpt": "dental terms: " + ", ".join(ev.get("dental_terms", []))
                         + ("; non-dental terms: " + ", ".join(ev["non_dental_terms"]) if ev.get("non_dental_terms") else ""),
                         "confidence": _f(r.get("dental_confidence")) / 100.0 if _f(r.get("dental_confidence")) is not None else None})
        for app, terms in (ev.get("application_terms") or {}).items():
            rows.append({**base, "evidence_type": "application", "subject": app, "text_excerpt": ", ".join(terms),
                         "confidence": None})
        at = json.loads(r["kn_attributes"]) if isinstance(r.get("kn_attributes"), str) else {}
        for name, a in at.items():
            rows.append({**base, "evidence_type": "attribute", "subject": name,
                         "text_excerpt": f"{a.get('field')}: \"{a.get('span')}\" -> {a.get('value')}{' ' + a['unit'] if a.get('unit') else ''}",
                         "confidence": a.get("confidence")})
    return pd.DataFrame(rows, columns=EVIDENCE_COLUMNS)


def append_history(prev: pd.DataFrame | None, cur: pd.DataFrame, keep_listing_metrics=("price", "sales", "rating")) -> pd.DataFrame:
    """Observation history (spec 69/80): previous runs + this run. Listing-level rows are kept only for the
    metrics that make price/sales/rating history; the rest of the listing detail stays in the current table."""
    keep = cur[(cur["entity_type"] != "listing") | cur["metric"].isin(keep_listing_metrics)]
    if prev is None or not len(prev):
        return keep.reset_index(drop=True)
    prev = prev[~prev["dataset_id"].isin(keep["dataset_id"].unique())]
    return pd.concat([prev, keep], ignore_index=True)

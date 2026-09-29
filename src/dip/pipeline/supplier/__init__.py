"""Stage 9 -- Supplier intelligence.

Suppliers live in the business DB (PostgreSQL / SQLite) and come only from
user-supplied sources (CSV/XLSX imports, the UI, APIs) -- never fabricated.
Scoring and segment matching wrap ``dmie.engine.suppliers``.
"""

from __future__ import annotations

import pandas as pd

from dmie.engine.suppliers import normalize_suppliers, score_suppliers
from dip.storage import business as b


def import_suppliers(raw: pd.DataFrame, source: str, org_id: str | None = None) -> int:
    norm = normalize_suppliers(raw)
    city_col = next((c for c in raw.columns if str(c).strip().lower() in ("city", "城市")), None)
    new_ids: list[str] = []
    with b.session() as s:
        for i, r in norm.iterrows():
            existing = s.get(b.Supplier, r["supplier_id"])
            if existing is None:
                new_ids.append(r["supplier_id"])
            rec = existing or b.Supplier(id=r["supplier_id"])
            for k in ("name", "country", "website", "business_type", "certifications", "product_categories", "contact", "notes"):
                v = r.get(k)
                setattr(rec, k, None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
            rec.oem, rec.odm, rec.source = bool(r["oem"]), bool(r["odm"]), source
            if existing is None:
                rec.org_id = org_id
            if city_col is not None:
                match = raw[raw.index == i]
                rec.city = str(match[city_col].iloc[0]) if len(match) and pd.notna(match[city_col].iloc[0]) else None
            s.merge(rec)
    if new_ids:
        announce_new_suppliers(new_ids, source)
    return len(norm)


def announce_new_suppliers(ids: list[str], source: str) -> None:
    """supplier.new events: one per market the new supplier's products match (so that market's
    owners are alerted), else one unmatched info event. Matching is by text similarity to the
    market's products; nothing about the supplier is inferred."""
    from dip import events
    from dip.connectors import match_markets

    with b.session() as s:
        sups = [s.get(b.Supplier, i) for i in ids]
        rows = [(x.id, x.name, x.country, x.oem, x.odm, x.product_categories or "") for x in sups if x is not None]
    for sid, name, country, oem, odm, cats in rows[:200]:
        payload = {"supplier_id": sid, "name": name, "country": country, "oem": oem, "odm": odm, "source": source}
        label = ("OEM " if oem else "") + "supplier"
        matches = match_markets(f"{name} {cats}") if cats else []
        if not matches:
            events.publish("supplier.new", None, f"New {label}: {name}", payload, "info", source)
        for m in matches:
            events.publish("supplier.new", m["market"], f"New {label} in {country or 'unknown country'}: {name}",
                           {**payload, "match_score": m["score"]}, "notice", source)


def load_suppliers(org_id: str | None = None) -> pd.DataFrame:
    """Suppliers of one organization (NULL org = default organization)."""
    org = org_id or b.DEFAULT_ORG
    with b.session() as s:
        rows = [b.row_dict(x) for x in s.query(b.Supplier).all() if (x.org_id or b.DEFAULT_ORG) == org]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.rename(columns={"id": "supplier_id"})


def score_and_match(segments: pd.DataFrame | None, org_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    sup = load_suppliers(org_id)
    if sup.empty:
        return sup, pd.DataFrame(columns=["supplier_id", "segment_id", "segment_label", "match_score"])
    cols = ["supplier_id", "name", "country", "website", "business_type", "oem", "odm", "certifications",
            "product_categories", "contact", "notes"]
    scored, matches = score_suppliers(sup[cols], segments)
    with b.session() as s:
        for _, r in scored.iterrows():
            rec = s.get(b.Supplier, r["supplier_id"])
            if rec is not None:
                rec.score = float(r["supplier_score"])
                rec.score_breakdown = {"category_match": float(r["category_match"]), "certifications": float(r["cert_score"])}
    return scored, matches

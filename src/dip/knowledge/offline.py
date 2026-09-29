"""Offline evidence (spec 31-34): exhibitions, distributor catalogs, manufacturers, trade data, surveys.

The platform does not collect offline evidence itself; people load it as a table (or a curated reference file
in data/reference/offline_evidence/<market>.csv applies until they do) (CSV / Excel), one row per
piece of evidence:

    source_type   exhibition | distributor_catalog | manufacturer | trade_data | clinic_survey | other
    source_name   e.g. "IDS 2025 exhibitor list", "Henry Schein catalog"
    applies_to    what it is about: a segment label, a taxonomy node label or an application; empty = the whole category
    signal        what was observed, e.g. "exhibitors", "catalog listings", "import shipments"
    value         optional number for the signal (e.g. 14 exhibitors)
    url, observed_at, notes   optional

Validation rejects rows with an unknown source type or no source name, each with its reason (never silently).

Offline evidence score of a scope (0-100):

    sum over matching rows of  weight(source_type) x (1 + log10(1 + value))  x (category_factor if category-wide)

capped at 100. A row matches a scope when its ``applies_to`` occurs in the scope's label or applications
(case-insensitive). The score is ``offline_evidence_score`` for the opportunity engine's offline strength
dimension; scopes without any matching evidence stay unmeasured (never zero). Settings: ``offline`` in
config/platform/knowledge.yaml.
"""

from __future__ import annotations

import math

import pandas as pd

from dip.knowledge import config

COLUMNS = ["source_type", "source_name", "applies_to", "signal", "value", "url", "observed_at", "notes"]


def _cfg() -> dict:
    return config()["offline"]


def validate(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """(accepted rows with the fixed columns, rejected rows with row number and reason)."""
    df = frame.rename(columns={c: str(c).strip().lower() for c in frame.columns})
    for c in COLUMNS:
        if c not in df:
            df[c] = None
    types = set(_cfg()["source_weights"])
    ok, rejected = [], []
    for i, r in enumerate(df[COLUMNS].to_dict("records"), start=2):          # row 1 is the header
        st = str(r.get("source_type") or "").strip().lower()
        name = str(r.get("source_name") or "").strip()
        why = ("unknown source_type: " + (st or "(empty)") + f" (allowed: {', '.join(sorted(types))})" if st not in types
               else "source_name is empty" if not name or name.lower() == "nan" else None)
        v = r.get("value")
        try:
            v = None if v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() == "" else float(v)
        except (TypeError, ValueError):
            why, v = why or f"value is not a number: {v}", None
        if v is not None and v < 0:
            why = why or f"value is negative: {v}"
        if why:
            rejected.append({"row": i, "reason": why})
            continue
        at = r.get("applies_to")
        at = None if at is None or (isinstance(at, float) and math.isnan(at)) or not str(at).strip() else str(at).strip()
        ok.append({**{k: (None if (isinstance(r.get(k), float) and math.isnan(r[k])) else r.get(k)) for k in COLUMNS},
                   "source_type": st, "source_name": name, "applies_to": at, "value": v})
    return pd.DataFrame(ok, columns=COLUMNS), rejected


def _target(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _matches(applies_to: str | None, label: str, applications: set[str]) -> bool:
    if applies_to is None:
        return True
    a = applies_to.lower()
    return a in label.lower() or a in {x.lower() for x in applications}


def score(evidence: pd.DataFrame, label: str, applications: set[str]) -> dict:
    """Offline evidence score of one scope, the evidence rows it rests on, and its basis."""
    c = _cfg()
    if evidence is None or not len(evidence):
        return {}
    w = c["source_weights"]
    total, used, sources = 0.0, 0, set()
    for r in evidence.to_dict("records"):
        target = _target(r.get("applies_to"))
        if not _matches(target, label, applications):
            continue
        v = r.get("value")
        mag = 1 + math.log10(1 + float(v)) if v is not None and not (isinstance(v, float) and math.isnan(v)) else 1.0
        f = float(c["category_factor"]) if target is None else 1.0
        total += float(w.get(r["source_type"], 0)) * mag * f
        used += 1
        sources.add(r["source_name"])
    if not used:
        return {}
    return {"offline_evidence_score": round(min(total, 100.0), 1), "offline_evidence_rows": used,
            "offline_basis": f"{used} offline evidence row(s) from {len(sources)} source(s): " + ", ".join(sorted(sources)[:4])}


def reference_path(market: str):
    from dip.settings import PROJECT_ROOT

    return PROJECT_ROOT / "data" / "reference" / "offline_evidence" / f"{market}.csv"


def load(market: str) -> pd.DataFrame:
    """Evidence loaded for the market (upload), else the curated reference file in
    data/reference/offline_evidence/<market>.csv (public sources, each row with its URL), else none."""
    from dip.storage import lake

    if lake.has_curated("offline_evidence", market):
        return lake.read_curated("offline_evidence", market)
    ref = reference_path(market)
    if ref.exists():
        ok, _ = validate(pd.read_csv(ref))
        return ok
    return pd.DataFrame(columns=COLUMNS)

"""Supplier intelligence upgrade: cooperation history, price level and ranking.

* ``add_interaction`` records an inquiry, quote, sample, order, audit or issue with a supplier
  (price, MOQ, lead time, 1-5 rating, linked product / market / project).
* After each quote the supplier's **price level** (low / mid / high) is recomputed from its median
  quote against the median of all quotes in the same market.
* ``ranking`` orders an organization's suppliers for a market or segment by fit, cooperation,
  price, lead time and reliability (weights in config/platform/sourcing.yaml); each component
  shows its evidence and missing components are left out, never guessed.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import yaml

from dip import audit
from dip.settings import PROJECT_ROOT
from dip.storage import business as b
from dip.storage import lake


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "sourcing.yaml").read_text(encoding="utf-8"))


class SourcingError(ValueError):
    pass


FIELDS = ("kind", "product", "market_name", "project_id", "unit_price", "currency", "moq", "lead_time_days", "rating", "note")


def add_interaction(supplier_id: str, data: dict, principal=None) -> dict:
    if data.get("kind") not in config()["interaction_kinds"]:
        raise SourcingError(f"kind must be one of {config()['interaction_kinds']}")
    if data.get("rating") is not None and not 1 <= int(data["rating"]) <= 5:
        raise SourcingError("rating must be 1..5")
    if data.get("unit_price") is not None and float(data["unit_price"]) <= 0:
        raise SourcingError("unit_price must be positive")
    with b.session() as s:
        if s.get(b.Supplier, supplier_id) is None:
            raise KeyError(supplier_id)
        row = b.SupplierInteraction(supplier_id=supplier_id, by=getattr(principal, "email", None) or "local user",
                                    **{k: data.get(k) for k in FIELDS})
        s.add(row)
        s.flush()
        out = b.row_dict(row)
    if data.get("kind") == "quote" and data.get("unit_price"):
        update_price_levels(data.get("market_name"))
    audit.record("supplier.interaction", principal, "suppliers", supplier_id, {"kind": data.get("kind")})
    return out


def history(supplier_id: str) -> list[dict]:
    with b.session() as s:
        return [b.row_dict(x) for x in s.query(b.SupplierInteraction).filter_by(supplier_id=supplier_id)
                .order_by(b.SupplierInteraction.at.desc()).all()]


def _interactions(ids: list[str]) -> pd.DataFrame:
    with b.session() as s:
        rows = [b.row_dict(x) for x in s.query(b.SupplierInteraction).filter(b.SupplierInteraction.supplier_id.in_(ids)).all()]
    cols = ["supplier_id", "kind", "market_name", "unit_price", "lead_time_days", "rating", "at"]
    return pd.DataFrame(rows)[cols] if rows else pd.DataFrame(columns=cols)


def update_price_levels(market: str | None) -> None:
    """Price level of every supplier with quotes in ``market`` (all quotes when None)."""
    with b.session() as s:
        q = s.query(b.SupplierInteraction).filter(b.SupplierInteraction.kind == "quote",
                                                  b.SupplierInteraction.unit_price.isnot(None))
        if market:
            q = q.filter(b.SupplierInteraction.market_name == market)
        rows = [(x.supplier_id, float(x.unit_price)) for x in q.all()]
    if not rows:
        return
    df = pd.DataFrame(rows, columns=["supplier_id", "price"])
    med = float(df["price"].median())
    lv = config()["price_level"]
    per = df.groupby("supplier_id")["price"].median() / med
    with b.session() as s:
        for sid, r in per.items():
            sup = s.get(b.Supplier, sid)
            if sup is not None:
                sup.price_level = "low" if r <= lv["low"] else "high" if r >= lv["high"] else "mid"


def ranking(org_id: str | None, market: str | None = None, segment_id: str | None = None, limit: int = 50) -> list[dict]:
    cfg = config()
    w = cfg["ranking_weights"]
    org = org_id or b.DEFAULT_ORG
    with b.session() as s:
        sups = [b.row_dict(x) for x in s.query(b.Supplier).all() if (x.org_id or b.DEFAULT_ORG) == org]
    if not sups:
        return []
    ids = [x["id"] for x in sups]
    fit = {}
    if market and lake.has_curated("supplier_matches", market):
        m = lake.read_curated("supplier_matches", market)
        if segment_id:
            m = m[m["segment_id"] == segment_id]
        fit = m.groupby("supplier_id")["match_score"].max().to_dict()
    inter = _interactions(ids)
    out = []
    lt = cfg["lead_time_days"]
    for sup in sups:
        comp, ev = {}, []
        if market:
            f = fit.get(sup["id"])
            if f is not None:
                comp["fit"] = min(float(f) / 0.5, 1.0)
                ev.append(f"segment match {f:.2f}")
        elif sup.get("score") is not None:
            comp["fit"] = float(sup["score"]) / 100
            ev.append(f"catalogue score {sup['score']:.0f}")
        mine = inter[inter["supplier_id"] == sup["id"]] if len(inter) else inter
        ratings = mine["rating"].dropna().astype(float) if len(mine) else pd.Series(dtype=float)
        if len(ratings):
            prior = cfg["cooperation_prior"]
            mean = (ratings.sum() + 3 * prior) / (len(ratings) + prior)
            comp["cooperation"] = (mean - 1) / 4
            ev.append(f"{len(ratings)} rated interaction(s), mean {ratings.mean():.1f}/5")
        if sup.get("price_level"):
            comp["price"] = {"low": 1.0, "mid": 0.6, "high": 0.3}[sup["price_level"]]
            ev.append(f"price level {sup['price_level']}")
        lead = mine["lead_time_days"].dropna().astype(float) if len(mine) else pd.Series(dtype=float)
        if len(lead):
            med = float(lead.median())
            comp["lead_time"] = float(np.clip((lt["worst"] - med) / (lt["worst"] - lt["best"]), 0, 1))
            ev.append(f"median lead time {med:.0f} days")
        if len(mine):
            issues = int((mine["kind"] == "issue").sum())
            comp["reliability"] = 1 - issues / len(mine)
            orders = int((mine["kind"] == "order").sum())
            ev.append(f"{orders} order(s), {issues} issue(s)")
        if not comp:
            continue
        score = sum(w[k] * v for k, v in comp.items()) / sum(w[k] for k in comp)
        out.append({"supplier_id": sup["id"], "name": sup["name"], "country": sup.get("country"), "oem": sup.get("oem"),
                    "odm": sup.get("odm"), "certifications": sup.get("certifications"), "price_level": sup.get("price_level"),
                    "rank_score": round(score * 100, 1), "components": {k: round(v * 100) for k, v in comp.items()},
                    "missing": [k for k in w if k not in comp], "evidence": ev,
                    "interactions": int(len(mine))})
    out.sort(key=lambda r: -r["rank_score"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out[:limit]

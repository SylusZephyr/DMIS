"""What we are sourcing: a product concept with the facts supplier offers are judged against.

From a sub-category or taxonomy node (``from_scope``), the concept reuses the requirements brief the platform
already computes -- price band, must-have attributes, differentiators, the max-FOB sourcing ceiling -- plus the
scope's median FBA fee and package weight (landed cost) and its expected monthly units (launch recommendation),
so a supplier's quote becomes a margin at the price Amazon buyers actually pay. ``from_text`` is the same for a
free-text idea. Search queries are built in English and Chinese (category ``sourcing_terms_zh`` + glossary).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from dip.sourcing_intel.platforms import config
from dip.storage import lake

STOP = {"and", "for", "with", "the", "of", "to", "in", "on", "a", "an", "set", "pack", "pcs", "kit", "kits", "dental",
        "other", "products", "product", "materials", "material", "&", "·"}


@dataclass
class Concept:
    market: str
    label: str
    scope: str | None = None
    scope_id: str | None = None
    terms_en: list[str] = field(default_factory=list)       # key words the offer must be about
    terms_zh: list[str] = field(default_factory=list)       # Chinese search phrases
    queries_en: list[str] = field(default_factory=list)
    must_have: list[dict] = field(default_factory=list)     # [{attribute, value}]
    features: list[str] = field(default_factory=list)
    target_price: float | None = None                       # Amazon selling price, USD
    price_band: list[float] | None = None
    first_order_qty: int = 500
    expected_units_month: float | None = None
    unit_weight_g: float | None = None
    fba_fee: float | None = None
    max_fob: float | None = None
    required_certs: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)          # where each number came from

    def to_dict(self) -> dict:
        return asdict(self)


def _words(text: str) -> list[str]:
    out = []
    for w in re.split(r"[^a-z0-9\-]+", (text or "").lower()):
        if len(w) > 2 and w not in STOP and w not in out:
            out.append(w)
    return out


def zh_for(words: list[str]) -> list[str]:
    """Chinese glossary terms for English words (and two-word phrases)."""
    g = config()["glossary"]
    out: list[str] = []
    text = " ".join(words)
    for en, zhs in sorted(g.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(en)}s?\b", text):
            out += [z for z in zhs if z not in out]
    return out


def _category(market: str) -> dict:
    from dip.pipeline.scope import definitions

    return definitions().get(market) or {}


def _queries(c: Concept, cat: dict) -> None:
    head = c.label.split("·")[0].strip()
    tail = [t.strip() for t in (c.label.split("·")[1].split(",") if "·" in c.label else []) if t.strip()]
    en = [f"{head} {' '.join(tail[:2])}".strip()] if tail else [head]
    en += [f"{head} {f}" for f in c.features[:2]]
    en += [t for t in (cat.get("search_terms") or [])[:2] if t.lower() not in " ".join(en).lower()]
    c.queries_en = [q for i, q in enumerate(en) if q and q not in en[:i]][:4]
    zh_cat = [str(z) for z in (cat.get("sourcing_terms_zh") or [])]
    words = _words(c.label + " " + " ".join(c.features))
    zh_gloss = zh_for(words)
    # category phrases that share a glossary term with this concept first; then a glossary-built phrase
    ranked = sorted(zh_cat, key=lambda z: -sum(1 for g in zh_gloss if g in z))
    built = "".join(zh_gloss[:3])
    c.terms_zh = [q for i, q in enumerate(ranked[:3] + ([built] if built else [])) if q and q not in (ranked[:3] + [built])[:i]][:4]


def _scope_listing_facts(market: str, product_ids: set[str] | None, segment_id: str | None) -> dict:
    """Median FBA fee and package weight of the scope's listings, top images (landed cost reads the same fields)."""
    if not lake.has_curated("listings", market):
        return {}
    cols = [c for c in ["id", "product_id", "segment_id", "price", "attributes", "image", "sales", "revenue"]
            if c in lake.curated_columns("listings", market)]
    L = lake.read_curated("listings", market, columns=cols)
    if segment_id and "segment_id" in L:
        L = L[L["segment_id"].astype(str) == segment_id]
    elif product_ids is not None and "product_id" in L:
        L = L[L["product_id"].astype(str).isin(product_ids)]
    if not len(L):
        return {}
    from dip.knowledge import landed_cost

    lc = landed_cost.listing_costs(L.reset_index(drop=True), market)
    imgs = []
    if "image" in L:
        order = L.sort_values("revenue" if "revenue" in L else "price", ascending=False, na_position="last")
        imgs = [str(u) for u in order["image"].dropna().astype(str) if u.startswith("http")][:3]
    med = lambda s: float(np.nanmedian(s)) if s.notna().any() else None   # noqa: E731
    return {"fba_fee": med(lc["fulfilment_fee"]), "weight_g": med(lc["weight_g"]), "images": imgs, "listings": int(len(L))}


def from_scope(market: str, scope: str, scope_id: str, target_price: float | None = None, qty: int | None = None) -> Concept:
    from dip.knowledge import requirements as rq

    def t(name):
        return lake.read_curated(name, market) if lake.has_curated(name, market) else pd.DataFrame()
    nodes = t("taxonomy_nodes")
    req = rq.build(scope, scope_id, t("capacity"), t("opportunities"), t("products"), nodes, t("recommendations"))
    if req is None:
        raise KeyError(f"no {scope} '{scope_id}' in market '{market}'")
    cat = _category(market)
    rc = config()["run"]
    band = req["price"].get("band")
    tp = target_price or (round((band[0] + band[1]) / 2, 2) if band and band[0] is not None and band[1] is not None else req["price"].get("median"))
    basis = [f"target price {'given' if target_price else ('midpoint of ' + req['price']['basis']) if band else 'scope median price'}"]
    rec = t("recommendations")
    exp_units = None
    if scope == "segment" and len(rec) and "expected_units" in rec:
        r = rec[rec["segment_id"].astype(str) == scope_id]
        if len(r) and pd.notna(r["expected_units"].iloc[0]):
            exp_units = float(r["expected_units"].iloc[0])
    first = qty or (int(round(exp_units * float(rc["first_order_months"]))) if exp_units else int(rc["default_first_order_qty"]))
    months = rc["first_order_months"]
    how = "given" if qty else (f"{exp_units:.0f}/month expected x {months} months" if exp_units else "default")
    basis.append(f"first order {first} units ({how})")
    pids = None
    if scope == "taxonomy" and len(nodes):
        row = nodes[nodes["node_key"] == scope_id]
        pids = set(json.loads(row["product_ids"].iat[0])) if len(row) and isinstance(row["product_ids"].iat[0], str) else None
    facts = _scope_listing_facts(market, pids, scope_id if scope == "segment" else None)
    label = req["label"] or scope_id
    if scope == "taxonomy":                                   # "reline_kit / soft" -> "reline kit / soft"
        label = label.replace("_", " ")
    c = Concept(market=market, label=label, scope=scope, scope_id=scope_id,
                must_have=[{"attribute": a["attribute"], "value": a["value"]} for a in req["must_have"]["attributes"]],
                features=[str(f) for f in req["differentiators"]["features"]], target_price=tp,
                price_band=band if band and band[0] is not None else None, first_order_qty=max(1, first),
                expected_units_month=exp_units, unit_weight_g=facts.get("weight_g"), fba_fee=facts.get("fba_fee"),
                max_fob=req["sourcing"].get("max_fob"), image_urls=facts.get("images", []),
                required_certs=list(config()["compliance"].get(market, config()["compliance"]["default"])))
    if facts.get("fba_fee") is not None:
        basis.append(f"FBA fee {facts['fba_fee']:.2f} and weight {facts.get('weight_g') or 0:.0f} g: medians of {facts['listings']} listings")
    c.basis = basis
    c.terms_en = _words(label)[:8] + [w for w in _words(" ".join(c.features)) if w not in _words(label)][:4]
    _queries(c, cat)
    return c


def from_text(market: str, text: str, target_price: float | None = None, qty: int | None = None) -> Concept:
    cat = _category(market)
    rc = config()["run"]
    c = Concept(market=market, label=text.strip(), target_price=target_price,
                first_order_qty=qty or int(rc["default_first_order_qty"]),
                required_certs=list(config()["compliance"].get(market, config()["compliance"]["default"])))
    facts = _scope_listing_facts(market, None, None) if market else {}
    c.fba_fee, c.unit_weight_g = facts.get("fba_fee"), facts.get("weight_g")
    c.basis = ["target price given" if target_price else "no target price: margins not computed",
               f"first order {c.first_order_qty} units ({'given' if qty else 'default'})"]
    if facts.get("fba_fee") is not None:
        c.basis.append(f"FBA fee and weight: market medians of {facts['listings']} listings")
    c.terms_en = _words(text)[:10]
    _queries(c, cat)
    return c

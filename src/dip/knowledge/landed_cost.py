"""Landed cost and fee headroom (spec 45-47, 72): what is left of the price for the goods.

Per listing, from the export's own fields where they exist:

    referral fee     = price x referral rate                       (config; Amazon US default)
    fulfilment fee   = the export's FBA fee                         (observed)
    freight          = package weight x freight rate per kg         (weight observed, rate assumed in config)
    fee headroom     = (price - referral - fulfilment - freight) / price
    max FOB          = (price x (1 - target margin) - referral - fulfilment - freight) / (1 + duty rate)

``max FOB`` is the highest factory price at which the listing's price still leaves the target margin -- a
sourcing ceiling, not a measured cost. It needs a duty rate: while ``duty_rate`` is unset it is reported
before duty and says so. A listing without an observed fulfilment fee or weight gets no headroom (never a
guessed fee). Scopes report the median over listings that have one and the share of listings that do.
Settings: ``landed_cost`` in config/platform/knowledge.yaml.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from dip.knowledge import config, units


def _cfg() -> dict:
    return config()["landed_cost"]


def _attr(a, fields: list[str]):
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            return None
    if not isinstance(a, dict):
        return None
    for f in fields:
        v = a.get(f)
        if v is not None and not (isinstance(v, float) and math.isnan(v)) and str(v).strip() != "":
            return v
    return None


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def weight_g(v) -> float | None:
    """A package weight in grams from a number (grams) or a text with a unit ("938.94 g", "2.1 pounds")."""
    if v is None:
        return None
    n = _num(v)
    if n is not None:
        return n if n > 0 else None
    q = units.first(str(v), "mass")
    return q.value if q and q.value > 0 else None


def duty_rate(market: str | None) -> float | None:
    c = _cfg()
    by = c.get("duty_rate_by_market") or {}
    r = by.get(market) if market in by else c.get("duty_rate")
    return None if r is None else float(r)


def listing_costs(L: pd.DataFrame, market: str | None = None) -> pd.DataFrame:
    """One row per listing (same index): fees, freight, fee headroom and max FOB."""
    c = _cfg()
    price = pd.to_numeric(L["price"], errors="coerce") if "price" in L else pd.Series(np.nan, index=L.index)
    attrs = L["attributes"] if "attributes" in L else pd.Series([None] * len(L), index=L.index)
    fba = attrs.map(lambda a: _num(_attr(a, c["fulfilment_fee_fields"]))).astype(float)
    w = attrs.map(lambda a: weight_g(_attr(a, c["weight_fields"]))).astype(float)
    referral = price * float(c["referral_fee"])
    kg = np.maximum(w / 1000.0, float(c["min_billable_kg"]))
    freight = kg * float(c["freight_usd_per_kg"])
    ok = price.gt(0) & fba.notna() & w.notna()
    left = (price - referral - fba - freight).where(ok)
    duty = duty_rate(market)
    fob = (price * (1 - float(c["target_margin"])) - referral - fba - freight).where(ok) / (1 + (duty or 0.0))
    return pd.DataFrame({"referral_fee": referral.round(2), "fulfilment_fee": fba, "weight_g": w, "freight": freight.where(w.notna()).round(2),
                         "fee_headroom": (left / price).round(4), "max_fob": fob.round(2)}, index=L.index)


def summarise(L: pd.DataFrame, market: str | None = None) -> dict:
    """Scope-level landed cost: medians over listings with an observed fee and weight, and their share."""
    c = _cfg()
    if not len(L):
        return {"fee_headroom": None, "max_fob_median": None, "landed_cost_coverage": 0.0, "landed_cost_basis": None,
                "max_fob_by_duty": None}
    lc = listing_costs(L, market)
    have = lc["fee_headroom"].notna()
    cov = round(float(have.mean()), 3)
    if have.sum() < int(c["min_listings"]):
        return {"fee_headroom": None, "max_fob_median": None, "landed_cost_coverage": cov, "landed_cost_basis": None,
                "max_fob_by_duty": None}
    duty = duty_rate(market)
    basis = (f"observed FBA fee and package weight on {int(have.sum())} listings; referral {c['referral_fee']:.0%}, freight "
             f"${c['freight_usd_per_kg']}/kg (assumed), target margin {c['target_margin']:.0%}, "
             + (f"duty {duty:.0%}" if duty is not None else "before duty (no duty rate set)"))
    before_duty = lc.loc[have, "max_fob"] * (1 + (duty or 0.0))          # max FOB before any duty
    scen = {k: round(float((before_duty / (1 + float(r))).median()), 2) for k, r in (c.get("duty_scenarios") or {}).items()}
    return {"fee_headroom": round(float(lc.loc[have, "fee_headroom"].median()), 4),
            "max_fob_median": round(float(lc.loc[have, "max_fob"].median()), 2), "landed_cost_coverage": cov,
            "landed_cost_basis": basis, "max_fob_by_duty": json.dumps(scen) if scen else None}

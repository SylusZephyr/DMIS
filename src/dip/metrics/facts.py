"""One reader for the market-level numbers every surface shows, so no route, analyst tool or map computes or picks
its own. Values come from the metrics-v3 summary stored with the market (src/dip/metrics/engine.py).

    market_revenue(market.summary)  -> headline (observed floor), modelled estimate + interval, validation
"""

from __future__ import annotations


def market_revenue(summary: dict | None) -> dict:
    """The market's monthly revenue as displayed: ``headline`` is what the data certainly shows (the observed floor);
    ``estimate``/``low``/``high`` are the demand model's, with ``model_validated``. Markets processed before
    metrics v3 fall back to the older observed sum, labelled as such."""
    su = summary or {}
    rm = (su.get("metrics_v3") or {}).get("revenue_month") or {}
    if rm:
        floor = rm.get("floor")
        return {"headline": rm.get("headline", floor), "basis": rm.get("headline_basis") or "observed: sum of sales-badge floors",
                "estimate": rm.get("estimate"), "low": rm.get("low"), "high": rm.get("high"), "floor": floor,
                "model_validated": rm.get("model_validated")}
    old = (su.get("category") or {}).get("monthly_revenue")
    return {"headline": old, "basis": "observed: sum of listed sales x price (processed before metrics v3)",
            "estimate": None, "low": None, "high": None, "floor": old, "model_validated": None}

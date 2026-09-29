"""Unit economics of one product idea at one Amazon price: what each unit earns, and what a launch needs.

    referral       = price x referral fee                       (landed_cost.referral_fee)
    freight        = max(weight kg, min billable) x $/kg         (landed_cost.freight_usd_per_kg)
    duty           = unit cost x duty rate                       (chosen scenario, or the market's rate)
    landed         = unit cost + freight + duty
    contribution   = price - referral - FBA fee - landed          (per unit, before advertising)
    ad cost        = price x ad share (TACoS)
    profit         = contribution - ad cost
    break-even ACoS = contribution / price  (the share of price advertising may take before a unit loses money)
    first order    = qty x landed; cash to launch = first order + launch costs (samples, photos, ...)

Every input either comes from the request or from config and is echoed back with its source.
"""

from __future__ import annotations

from dip.knowledge import landed_cost


def unit(price: float, unit_cost: float, *, fba_fee: float, weight_g: float, qty: int = 500, ad_share: float = 0.1,
         duty_rate: float | None = None, duty_scenario: str | None = None, market: str | None = None,
         launch_costs: float = 0.0, units_per_month: float | None = None) -> dict:
    if price <= 0 or unit_cost < 0 or fba_fee < 0 or weight_g <= 0 or qty <= 0 or not 0 <= ad_share < 1:
        raise ValueError("price, weight and quantity must be positive; costs and fees not negative; ad share in [0, 1)")
    c = landed_cost.config()["landed_cost"]
    src = {}
    if duty_rate is None and duty_scenario:
        sc = c.get("duty_scenarios") or {}
        if duty_scenario not in sc:
            raise ValueError(f"unknown duty scenario '{duty_scenario}' (known: {', '.join(sc)})")
        duty_rate, src["duty"] = float(sc[duty_scenario]), f"scenario {duty_scenario}"
    elif duty_rate is None:
        duty_rate = landed_cost.duty_rate(market)
        src["duty"] = "market duty rate (config)" if duty_rate is not None else "no duty rate set: before duty"
    else:
        src["duty"] = "given"
    referral = price * float(c["referral_fee"])
    freight = max(weight_g / 1000.0, float(c["min_billable_kg"])) * float(c["freight_usd_per_kg"])
    duty = unit_cost * (duty_rate or 0.0)
    landed = unit_cost + freight + duty
    contribution = price - referral - fba_fee - landed
    ad = price * ad_share
    profit = contribution - ad
    first_order = qty * landed
    out = {
        "inputs": {"price": price, "unit_cost": unit_cost, "fba_fee": fba_fee, "weight_g": weight_g, "qty": qty,
                   "ad_share": ad_share, "duty_rate": duty_rate, "launch_costs": launch_costs},
        "per_unit": {k: round(v, 4) for k, v in {"referral": referral, "fba_fee": fba_fee, "freight": freight, "duty": duty,
                                                 "landed": landed, "contribution": contribution, "ad_cost": ad,
                                                 "profit": profit}.items()},
        "margin": round(profit / price, 4), "contribution_margin": round(contribution / price, 4),
        "break_even_acos": round(max(contribution, 0.0) / price, 4),
        "max_unit_cost_for_target": round((price * (1 - float(c["target_margin"]) - ad_share) - referral - fba_fee - freight)
                                          / (1 + (duty_rate or 0.0)), 4),
        "first_order_cost": round(first_order, 2), "cash_to_launch": round(first_order + launch_costs, 2),
        "sources": {"referral": f"{c['referral_fee']:.0%} of price (config)",
                    "freight": f"${c['freight_usd_per_kg']}/kg, min {c['min_billable_kg']} kg (config, assumed)",
                    "target_margin": f"{c['target_margin']:.0%} (config)", **src},
    }
    if units_per_month:
        out["monthly"] = {"units": units_per_month, "profit": round(profit * units_per_month, 2),
                          "payback_months": round((first_order + launch_costs) / (profit * units_per_month), 1) if profit > 0 else None}
    return out

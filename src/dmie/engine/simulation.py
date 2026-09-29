"""Product Simulation Engine (Module 10) -- Monte Carlo.

"We plan to launch X at price P with unit cost C. How likely is it to work?"

1. Comparables: the ``comparables`` nearest real products by title/spec
   text similarity (optionally restricted to a segment) that have a
   known monthly sales value.
2. Demand model: log-normal fitted to the comparables' log sales.
3. Price response: log-log regression of sales on price across the
   comparables gives an elasticity if it is sane (-4 < e < -0.2);
   otherwise the configured default is used and reported as such.
4. New-entrant ramp: Beta(2, 3) share of a comparable's steady-state
   demand (mean 40%) -- an explicit, documented assumption.
5. Each scenario: demand x price effect x ramp; profit =
   (price x (1 - fee) - cost) x units - fixed monthly cost.

Output: P(profit > 0), percentiles, and a verdict:
High success / Medium success / Failure risk (thresholds in config).
Seeded for reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from dmie.engine.config import section


@dataclass
class SimulationInput:
    title: str
    price: float
    unit_cost: float
    specs: str = ""
    segment_id: str | None = None
    marketplace_fee: float = 0.15
    fixed_monthly_cost: float = 0.0


@dataclass
class SimulationOutput:
    status: str
    verdict: str | None = None
    probability_profitable: float | None = None
    probability_by_level: dict = field(default_factory=dict)
    units_p10_p50_p90: tuple | None = None
    revenue_p10_p50_p90: tuple | None = None
    profit_p10_p50_p90: tuple | None = None
    unit_margin: float | None = None
    elasticity: float | None = None
    elasticity_source: str | None = None
    comparables: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _pct(x: np.ndarray) -> tuple:
    return tuple(round(float(v), 2) for v in np.percentile(x, [10, 50, 90]))


def find_comparables(products: pd.DataFrame, text: str, k: int, segment_id: str | None = None) -> pd.DataFrame:
    pool = products[products["monthly_sales"].notna() & (products["monthly_sales"] > 0) & products["price"].notna()]
    if segment_id is not None:
        seg_pool = pool[pool["segment_id"] == segment_id]
        if len(seg_pool) >= 5:
            pool = seg_pool
    if pool.empty:
        return pool
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
    M = vec.fit_transform(pool["title"].fillna("").tolist() + [text])
    sims = cosine_similarity(M[-1], M[:-1]).ravel()
    pool = pool.assign(similarity=sims)
    return pool.sort_values("similarity", ascending=False).head(k)


def simulate(products: pd.DataFrame, spec: SimulationInput) -> SimulationOutput:
    cfg = section("simulation")
    rng = np.random.default_rng(cfg.get("seed", 20260924))
    n = int(cfg.get("n_simulations", 10000))
    comps = find_comparables(products, f"{spec.title} {spec.specs}", int(cfg.get("comparables", 15)), spec.segment_id)
    if len(comps) < 3:
        return SimulationOutput("insufficient_data", note=f"only {len(comps)} comparable products with known sales; need >= 3")

    log_sales = np.log(comps["monthly_sales"].to_numpy(float))
    mu, sigma = float(log_sales.mean()), float(max(log_sales.std(ddof=1) if len(log_sales) > 1 else 0.5, 0.25))
    prices = comps["price"].to_numpy(float)
    ref_price = float(np.median(prices))

    elasticity, source = cfg.get("default_price_elasticity", -1.2), "default (config)"
    if len(comps) >= 8 and np.std(np.log(prices)) > 0.05:
        slope = float(np.polyfit(np.log(prices), log_sales, 1)[0])
        if -4 < slope < -0.2:
            elasticity, source = slope, f"estimated from {len(comps)} comparables"

    base = rng.lognormal(mu, sigma, n)
    e = rng.normal(elasticity, 0.3, n)
    # Elasticity is only trusted near observed prices: clamp the ratio so a
    # price far outside the comparables' range is not extrapolated into
    # fantasy demand. Flagged in the assumptions when it happens.
    ratio = spec.price / ref_price
    clamped = float(np.clip(ratio, 0.5, 2.0))
    price_effect = clamped ** e
    ramp = rng.beta(2, 3, n)
    units = base * price_effect * ramp
    unit_margin = spec.price * (1 - spec.marketplace_fee) - spec.unit_cost
    revenue = units * spec.price
    profit = units * unit_margin - spec.fixed_monthly_cost

    p_profit = float((profit > 0).mean())
    th = cfg.get("success_thresholds", {"high": 0.7, "medium": 0.4})
    target = spec.fixed_monthly_cost + 1000.0
    if unit_margin <= 0:
        verdict = "Failure risk"
    elif p_profit >= th["high"] and float((profit > 1000).mean()) >= 0.5:
        verdict = "High success"
    elif p_profit >= th["medium"]:
        verdict = "Medium success"
    else:
        verdict = "Failure risk"
    return SimulationOutput(
        "ok", verdict, round(p_profit, 4),
        {"profit>0": round(p_profit, 4), "profit>$1k/mo": round(float((profit > 1000).mean()), 4),
         **({f"profit>fixed+$1k (${target:,.0f})/mo": round(float((profit > target).mean()), 4)} if spec.fixed_monthly_cost > 0 else {})},
        _pct(units), _pct(revenue), _pct(profit), round(unit_margin, 2), round(float(elasticity), 3), source,
        comps[["product_id", "title", "brand", "price", "monthly_sales", "similarity"]].round(3).to_dict("records"),
        [f"{n:,} scenarios, seed {cfg.get('seed')}",
         f"demand ~ lognormal fitted to {len(comps)} comparables (median {np.exp(mu):.0f} units/mo)",
         f"price elasticity {elasticity:.2f} ({source}), reference price ${ref_price:.2f}",
         "new-entrant ramp ~ Beta(2,3) of comparable demand (mean 40%)",
         f"marketplace fee {spec.marketplace_fee:.0%}"]
        + ([f"WARNING: target price is {ratio:.2f}x the comparables' median -- price effect clamped to {clamped:.1f}x; "
            "comparables may not represent this price tier"] if clamped != ratio else []),
    )

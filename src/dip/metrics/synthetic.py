"""Synthetic ground-truth markets for measuring accuracy without real ground truth.

A generator draws listings with *known true* monthly units from a configurable data-generating
process (sub-categories with different demand levels, price effect, rating effect, age effect,
noise that can be normal or heavy-tailed), then applies the real observation process: the
marketplace badge ladder (values below the first rung disappear; others are floored to a rung).
The estimators are run on the observed data only and scored against the truth.

The default scenario draws units from exactly the demand model's functional form (the best case).
Misspecification options measure how the model degrades when the world is not log-linear and
independent: brand random effects (unmodelled, on skewed brand sizes), a curved (quadratic) price
response, zero-inflated "dead" listings that sell nothing, and noise correlated within brands. An
optional review-count column (reviews accumulate with units and age) exercises that covariate.
All options draw from a separate random stream, so the default scenario is unchanged by them.

Nothing here is used for real results; it exists so accuracy is measured continuously in CI,
independent of any particular dataset.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dip.metrics import config


@dataclass
class Scenario:
    n: int = 400
    categories: int = 5
    base_log_units: float = 3.2            # median ~25 units/month -> most listings below the first rung
    category_spread: float = 0.8           # sd of sub-category effects (log units)
    price_elasticity: float = -0.9
    rating_effect: float = 0.6             # log units per rating point
    age_effect: float = 0.25               # log units per log(day)
    noise_sd: float = 1.1
    heavy_tails: bool = False              # Student-t(3) noise instead of normal (misspecification test)
    missing_covariates: float = 0.1        # share of rating / launch date missing
    seed: int = 1
    # ---- misspecification (all off by default = the model's own functional form)
    brands: int = 30                       # brand column (sizes skewed: P(brand k) ~ 1 / k)
    brand_sd: float = 0.0                  # sd of brand random effects on log units (not in the model)
    price_curvature: float = 0.0           # adds -c * (log price - log 25)^2: a curved (concave) price response
    dead_share: float = 0.0                # share of listings that sell nothing at all (zero-inflation)
    brand_noise_corr: float = 0.0          # share of the noise variance shared within a brand (total sd unchanged)
    reviews: bool = False                  # add a review-count column (~1.5 % of cumulative units, overdispersed)
    bsr: bool = False                      # add a best-sellers-rank column: rank of noisy daily sales in a category
                                           # population ~20x the export (the export is a sample of the category)
    name: str = ""                         # scenario label for reports


#: Named scenario overrides; the accuracy dashboard evaluates the list in config ``accuracy.synthetic_scenarios``.
SCENARIOS: dict[str, dict] = {
    "specified": {},
    "heavy_tails": {"heavy_tails": True},
    "brand_effects": {"brand_sd": 0.7},
    "curved_price": {"price_curvature": 0.6},
    "curved_price_brands": {"price_curvature": 0.6, "brand_sd": 0.7},
    "dead_listings": {"dead_share": 0.3},
    "brand_noise": {"brand_noise_corr": 0.3},
    "reviews": {"reviews": True},
    "dead_listings_reviews": {"dead_share": 0.3, "reviews": True},
    "bsr": {"bsr": True},
    "dead_listings_bsr": {"dead_share": 0.3, "bsr": True},
    "brand_effects_bsr": {"brand_sd": 0.7, "bsr": True},
}


def scenario(name: str, **kw) -> Scenario:
    """A named scenario (``SCENARIOS``) with further field overrides (n, seed, ...)."""
    return Scenario(**{**SCENARIOS[name], **kw, "name": name})


def generate(sc: Scenario) -> tuple[pd.DataFrame, np.ndarray]:
    """Returns (observed frame with a badge 'sales' column, true units)."""
    rng = np.random.default_rng(sc.seed)
    cats = np.array([f"Cat {i}" for i in range(sc.categories)])
    cat_eff = rng.normal(0, sc.category_spread, sc.categories)
    ci = rng.integers(0, sc.categories, sc.n)
    price = np.exp(rng.normal(np.log(25) + 0.3 * cat_eff[ci], 0.6))
    rating = np.clip(rng.normal(4.3, 0.35, sc.n), 1, 5).round(1)
    age = np.exp(rng.normal(np.log(500), 0.9, sc.n)).clip(5, 5000)
    def _noise(k):
        return rng.standard_t(3, k) * sc.noise_sd / np.sqrt(3) if sc.heavy_tails else rng.normal(0, sc.noise_sd, k)

    base = (sc.base_log_units + cat_eff[ci] + sc.price_elasticity * (np.log(price) - np.log(25))
            + sc.rating_effect * (rating - 4.3) + sc.age_effect * (np.log(age) - np.log(500)))
    # no listing can exceed the top badge rung's upper bound: impossible draws are redrawn (a smoothly
    # truncated tail), never clipped (clipping would pile mass on one value)
    cfg = config()["sales_observation"]
    cap = np.log(cfg["badge_ladder"][-1] * cfg["top_rung_multiplier"])
    log_u = base + _noise(sc.n)
    for _ in range(100):
        bad = log_u >= cap
        if not bad.any():
            break
        log_u[bad] = base[bad] + _noise(int(bad.sum()))
    # ---- misspecification: separate stream, so the default scenario's draws above are unchanged
    rng2 = np.random.default_rng([sc.seed, 7919])
    wb = 1.0 / np.arange(1, sc.brands + 1)
    bi = rng2.choice(sc.brands, sc.n, p=wb / wb.sum())
    brand_eff = rng2.normal(0, sc.brand_sd, sc.brands) if sc.brand_sd > 0 else np.zeros(sc.brands)
    extra = brand_eff[bi] - sc.price_curvature * (np.log(price) - np.log(25)) ** 2
    if sc.brand_noise_corr > 0:                          # re-split the noise: shared brand part + own part
        rho = sc.brand_noise_corr
        u = rng2.normal(0, 1, sc.brands)
        resid = (log_u - base) * np.sqrt(1 - rho) + sc.noise_sd * np.sqrt(rho) * u[bi]
        log_u = base + resid
    if extra.any() or sc.brand_noise_corr > 0:
        log_u = log_u + extra
        for _ in range(100):
            bad = log_u >= cap
            if not bad.any():
                break
            log_u[bad] = base[bad] + extra[bad] + _noise(int(bad.sum()))
    true = np.exp(np.minimum(log_u, cap - 1e-6))
    if sc.dead_share > 0:
        true = np.where(rng2.random(sc.n) < sc.dead_share, 0.0, true)
    ladder = np.array(config()["sales_observation"]["badge_ladder"], dtype=float)
    idx = np.searchsorted(ladder, true, side="right") - 1
    obs = np.where(idx >= 0, ladder[np.clip(idx, 0, None)], np.nan)
    as_of = pd.Timestamp("2026-07-01")
    launch = as_of - pd.to_timedelta(age.round(), unit="D")
    frame = pd.DataFrame({"price": price.round(2), "rating": rating, "launch_date": launch, "category": cats[ci], "sales": obs,
                          "brand": np.array([f"Brand {k}" for k in range(sc.brands)])[bi]})
    if sc.reviews:                                       # reviews accumulate with sales over the listing's life
        lam = 0.015 * true * np.minimum(age, 1500) / 30.4 * np.exp(rng2.normal(0, 0.5, sc.n))
        frame["reviews"] = rng2.poisson(lam).astype(float)
    if sc.bsr:                                           # rank of a noisy daily sales reading, category-wide
        rng3 = np.random.default_rng([sc.seed, 104729])
        daily = true * np.exp(rng3.normal(0, 0.35, sc.n)) + rng3.exponential(0.05, sc.n)   # dead listings rank last, in any order
        pop = 20                                          # listings in the category per listing in the export
        rank = pd.Series(-daily).rank(method="first").to_numpy()
        frame["bsr"] = np.round(rank * pop * np.exp(rng3.normal(0, 0.15, sc.n)) + rng3.integers(0, pop, sc.n))
    miss = rng.random(sc.n) < sc.missing_covariates
    frame.loc[miss, "rating"] = np.nan
    miss = rng.random(sc.n) < sc.missing_covariates
    frame.loc[miss, "launch_date"] = pd.NaT
    frame.attrs["as_of"] = as_of
    return frame, true


def evaluate(sc: Scenario, bootstrap_reps: int = 30, sims: int = 300) -> dict:
    """Run the demand model on one synthetic market and score it against the truth."""
    from dip.metrics.demand import estimate
    from dip.metrics.observation import observe

    frame, true = generate(sc)
    obs = observe(frame["sales"])
    res = estimate(frame, obs, frame.attrs["as_of"], bootstrap_reps=bootstrap_reps)
    price = frame["price"].to_numpy()
    covered = (true >= res.units_lo) & (true <= res.units_hi)
    grp = {"market": np.zeros(len(true), dtype=int), "category": pd.factorize(frame["category"])[0]}
    draws = res.simulate(grp, weights=price, sims=sims)
    tot = draws["market"][:, 0]
    true_rev = float((true * price).sum())
    lo, hi = np.percentile(tot, [2.5, 97.5])
    cat_true = pd.Series(true * price).groupby(grp["category"]).sum().to_numpy()
    cat_lo, cat_hi = np.percentile(draws["category"], [2.5, 97.5], axis=0)
    est_rank = np.argsort(-draws["category"].mean(axis=0))
    return {"scenario": sc.name or "custom", "kind_detected": obs.kind, "n": sc.n, "badged_share": float(obs.known.mean()),
            "listing_coverage_95": float(covered.mean()),
            "market_true": true_rev, "market_est": float(tot.mean()), "market_lo": float(lo), "market_hi": float(hi),
            "market_covered": bool(lo <= true_rev <= hi),
            "market_rel_error": float(tot.mean() / true_rev - 1),
            "observed_floor_rel_error": float((res.floor * price).sum() / true_rev - 1),
            "category_coverage_95": float(np.mean((cat_true >= cat_lo) & (cat_true <= cat_hi))),
            "top_category_correct": bool(est_rank[0] == int(np.argmax(cat_true))),
            "elasticity_true": sc.price_elasticity, "elasticity_est": (res.price_elasticity() or {}).get("value")}


def study(scenarios: list[Scenario], **kw) -> pd.DataFrame:
    return pd.DataFrame([{**evaluate(s, **kw), "seed": s.seed, "heavy_tails": s.heavy_tails} for s in scenarios])


def summarize(rows: list[dict] | pd.DataFrame) -> dict:
    """Accuracy summary of a set of evaluated markets (one scenario, or pooled)."""
    df = pd.DataFrame(rows)
    err = df["market_rel_error"].to_numpy(dtype=float)
    return {"markets": int(len(df)), "median_abs_error": float(np.median(np.abs(err))), "mean_error": float(err.mean()),
            "market_coverage_95": float(df["market_covered"].astype(bool).mean()),
            "listing_coverage_95": float(df["listing_coverage_95"].mean()),
            "category_coverage_95": float(df["category_coverage_95"].mean()),
            "top_category_correct": float(df["top_category_correct"].astype(bool).mean()),
            "observed_floor_mean_error": float(df["observed_floor_rel_error"].mean())}


def worst_case(by_scenario: dict[str, dict]) -> dict:
    """Per measure, the worst value over scenarios and which scenario produced it."""
    worst: dict = {}
    for key, bad in (("median_abs_error", max), ("mean_error", lambda v: max(v, key=abs)), ("market_coverage_95", min),
                     ("listing_coverage_95", min), ("category_coverage_95", min), ("top_category_correct", min)):
        vals = {k: s[key] for k, s in by_scenario.items()}
        v = bad(vals.values())
        worst[key] = v
        worst[f"{key}_scenario"] = next(k for k, x in vals.items() if x == v)
    return worst

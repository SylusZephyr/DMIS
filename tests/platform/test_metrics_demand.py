"""Demand model v3 (Phase 5.1): badge-ladder detection, interval-censored estimation, and accuracy
gates measured against synthetic ground truth. These gates fail the build if accuracy regresses."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dip.metrics import config
from dip.metrics.demand import conditional_mean, conditional_quantile, estimate, fit, select_family
from dip.metrics.observation import classify, observe
from dip.metrics.synthetic import Scenario, generate, study


# ---------------------------------------------------------------- observation process
def test_badge_ladder_detected_and_intervals():
    s = pd.Series([50, 100, 200, 1000, 20000, None, None] * 3)
    kind, ev = classify(s)
    assert kind == "badge" and ev["on_ladder_share"] == 1.0
    o = observe(s)
    assert list(o.lower[:7]) == [50, 100, 200, 1000, 20000, 0, 0]
    assert list(o.upper[:7]) == [100, 200, 300, 2000, 30000, 50, 50]      # missing = below the first rung


def test_exact_data_is_not_mistaken_for_badges():
    s = pd.Series([37, 212, 58, 1033, 75, 44, 90, 310, 12, 7, 64, None])
    kind, ev = classify(s)
    assert kind == "exact"
    o = observe(s)
    assert o.lower[0] == o.upper[0] == 37 and np.isinf(o.upper[-1])        # missing = unknown, not censored


def test_too_few_values_are_not_classified_as_badges():
    assert classify(pd.Series([50, 100, None]))[0] == "exact"


# ---------------------------------------------------------------- estimator invariants
def test_estimates_respect_observed_intervals():
    frame, _ = generate(Scenario(n=300, seed=5))
    obs = observe(frame["sales"])
    res = estimate(frame, obs, frame.attrs["as_of"], bootstrap_reps=10)
    assert np.all(res.units_lo >= obs.lower - 1e-9) and np.all(res.units_hi <= obs.upper + 1e-9)
    assert np.all((res.units_est >= res.units_lo - 1e-6) & (res.units_est <= res.units_hi + 1e-6))
    unb = ~obs.known
    assert np.all(res.units_est[unb] < config()["sales_observation"]["badge_ladder"][0])   # no badge -> < 50


def test_conditional_mean_matches_simulation():
    frame, _ = generate(Scenario(n=200, seed=9))
    obs = observe(frame["sales"])
    rng = np.random.default_rng(0)
    mu = rng.normal(3, 1, len(obs.lower))
    cm = conditional_mean(obs, mu, 1.1)
    draws = np.mean([conditional_quantile(obs, mu, 1.1, rng.random(len(mu))) for _ in range(3000)], axis=0)
    assert np.allclose(cm, draws, rtol=0.06, atol=0.5)


def test_heavy_tails_select_student_t():
    frame, _ = generate(Scenario(n=800, seed=3, heavy_tails=True, noise_sd=1.3))
    obs = observe(frame["sales"])
    from dip.metrics.demand import build_design
    d = build_design(frame, frame.attrs["as_of"])
    f = select_family(d.matrix(frame, frame.attrs["as_of"]), obs, d.names)
    assert f.df is not None                                                 # a heavy-tailed family was chosen


def test_recovers_true_parameters_on_large_market():
    sc = Scenario(n=3000, seed=11)
    frame, true = generate(sc)
    obs = observe(frame["sales"])
    res = estimate(frame, obs, frame.attrs["as_of"], bootstrap_reps=10)
    el = res.price_elasticity()
    assert abs(el["value"] - sc.price_elasticity) < 0.2
    assert abs(res.fit.sigma - sc.noise_sd) < 0.2


def test_intercept_only_when_too_few_observations():
    frame = pd.DataFrame({"price": [10.0] * 30, "sales": [50] * 3 + [None] * 27, "category": ["a"] * 30})
    obs = observe(frame["sales"], kind="badge")
    f = fit(np.ones((30, 1)), obs)
    assert np.isfinite(f.beta[0]) and f.sigma > 0


# ---------------------------------------------------------------- accuracy gates (synthetic ground truth)
@pytest.mark.parametrize("heavy", [False, True])
def test_accuracy_gate_synthetic_markets(heavy):
    """Gate: unbiased market size, calibrated intervals, correct top sub-category. Thresholds allow for the
    Monte-Carlo noise of a 16-market study; the full 80-market study is reported in METHODOLOGY.md."""
    seeds = range(301, 317) if not heavy else range(401, 417)
    df = study([Scenario(seed=s, heavy_tails=heavy) for s in seeds], bootstrap_reps=30, sims=250)
    assert abs(df["market_rel_error"].mean()) < 0.05                          # unbiased market size
    assert df["market_rel_error"].abs().median() < 0.06
    assert df["market_covered"].mean() >= 0.85                                # 95 % intervals hold the truth
    assert 0.90 <= df["listing_coverage_95"].mean() <= 0.99
    assert df["category_coverage_95"].mean() >= 0.85
    assert df["top_category_correct"].mean() >= 0.8
    assert df["observed_floor_rel_error"].mean() < -0.2                       # the old "observed only" size is badly biased


# ---------------------------------------------------------------- misspecification scenarios, price spline, reviews
def test_scenario_options_leave_the_default_unchanged():
    from dip.metrics.synthetic import SCENARIOS, scenario
    f0, t0 = generate(Scenario(seed=21))
    f1, t1 = generate(scenario("specified", seed=21))
    assert np.array_equal(t0, t1) and f0["sales"].equals(f1["sales"])                # options draw from their own stream
    fd, td = generate(scenario("dead_listings", seed=21))
    assert 0.2 < (td == 0).mean() < 0.4 and np.all(np.isnan(fd.loc[td == 0, "sales"]))  # dead listings never show a badge
    fr, _ = generate(scenario("reviews", seed=21))
    assert "reviews" in fr and (fr["reviews"] >= 0).all() and "reviews" not in f0
    fb, tb = generate(scenario("brand_effects", seed=21))
    assert fb["brand"].value_counts().iloc[0] > 3 * fb["brand"].value_counts().median()  # skewed brand sizes
    assert not np.array_equal(tb, t0)
    assert set(SCENARIOS) >= {"specified", "curved_price", "brand_effects", "dead_listings", "brand_noise"}


def test_restricted_cubic_spline_basis():
    from dip.metrics.demand import rcs_basis, rcs_derivative
    knots = [0.0, 1.0, 2.0, 3.0]
    x = np.linspace(-2, 5, 701)
    B = rcs_basis(x, knots)
    assert B.shape == (701, 2)
    assert np.allclose(B[x <= 0], 0)                                                   # zero below the first knot
    beyond = x >= 3
    assert np.allclose(np.diff(B[beyond], n=2, axis=0), 0, atol=1e-9)                  # linear beyond the last knot
    D = rcs_derivative(x, knots)
    fd = np.gradient(B, x, axis=0)
    assert np.allclose(D[5:-5], fd[5:-5], atol=1e-3)


def test_price_spline_falls_back_to_linear_on_small_data():
    from dip.metrics.demand import build_design
    frame, _ = generate(Scenario(n=400, seed=4))
    big = build_design(frame, frame.attrs["as_of"], n_informative=200)
    small = build_design(frame, frame.attrs["as_of"], n_informative=10)
    assert big.spline_terms("log_price") and big.knots["log_price"] == sorted(big.knots["log_price"])
    assert not small.spline_terms("log_price") and "log_price" in small.names and not small.knots


def test_price_spline_recovers_a_curved_price_response():
    """Truth: elasticity = -0.9 - 1.2 (log p - log 25): -0.46 at $17.4, -1.43 at $38.8."""
    from dip.metrics.synthetic import scenario
    sc = scenario("curved_price", n=3000, seed=12)
    frame, _ = generate(sc)
    res = estimate(frame, observe(frame["sales"]), frame.attrs["as_of"], bootstrap_reps=10)
    el = res.price_elasticity()
    assert el["form"].startswith("spline")
    loc = {r["percentile"]: r for r in el["local"]}
    for q in (25, 50, 75):
        truth = -0.9 - 1.2 * (np.log(loc[q]["price"]) - np.log(25))
        assert abs(loc[q]["value"] - truth) < 0.35, (q, loc[q], truth)
    assert loc[25]["value"] > loc[75]["value"] + 0.4                                     # the curvature is found


def test_reviews_covariate_and_missing_indicator():
    """log(1 + reviews) is available as a covariate (off by default in config, see METHODOLOGY §1.5)."""
    from dip.metrics.demand import build_design
    from dip.metrics.synthetic import scenario
    frame, _ = generate(scenario("reviews", n=600, seed=13))
    frame.loc[frame.index[:30], "reviews"] = np.nan
    as_of = frame.attrs["as_of"]
    obs = observe(frame["sales"])
    d = build_design(frame, as_of, covariates=["log_price", "rating", "log_age_days", "log_reviews", "category"])
    assert {"log_reviews", "log_reviews_missing"} <= set(d.names)
    f = select_family(d.matrix(frame, as_of), obs, d.names)
    assert f.beta[1 + d.names.index("log_reviews")] > 0.3                              # reviews carry demand information
    assert "log_reviews" not in build_design(frame, as_of).names                        # default config: not used
    frame2, _ = generate(Scenario(n=300, seed=13))
    assert "log_reviews" not in build_design(frame2, as_of, covariates=["log_price", "log_reviews"]).names  # no column


def test_model_card_round_trip_with_spline():
    import json

    from dip.metrics.demand import card_mu, model_card, predict_draws
    from dip.metrics.synthetic import scenario
    frame, _ = generate(scenario("curved_price", n=800, seed=14))
    as_of = frame.attrs["as_of"]
    res = estimate(frame, observe(frame["sales"]), as_of, bootstrap_reps=5)
    card = json.loads(json.dumps(model_card(res, as_of)))
    assert card["design"]["knots"]["log_price"]
    assert np.allclose(card_mu(card, frame), res.X @ res.fit.beta)                    # the card reproduces the fit
    grid = pd.DataFrame({"price": np.geomspace(5, 200, 9), "rating": 4.3, "launch_date": as_of - pd.Timedelta(days=180),
                         "category": frame["category"].mode().iat[0]})
    D = predict_draws(card, grid, 200, 1, 0.0, as_of, shared_noise=True)
    assert D.shape == (200, 9) and np.isfinite(D).all()
    old = {**card, "design": {k: v for k, v in card["design"].items() if k != "knots"}}
    old["design"]["names"] = [n for n in old["design"]["names"] if "_rcs" not in n]      # a card written before splines
    keep = [0] + [i + 1 for i, n in enumerate(card["design"]["names"]) if "_rcs" not in n]
    old["beta"] = [card["beta"][i] for i in keep]
    old["boot"] = [{**b, "beta": [b["beta"][i] for i in keep]} for b in card["boot"]]
    assert predict_draws(old, grid, 20, 1, 0.0, as_of).shape == (20, 9)


@pytest.mark.parametrize("name", ["curved_price", "brand_effects"])
def test_accuracy_gate_misspecified_markets(name):
    """Gate on misspecified worlds (measured on 16 markets per scenario in METHODOLOGY §1.5): the spline keeps
    the curved-price bias small; unmodelled brand effects do not bias the market size."""
    from dip.metrics.synthetic import scenario
    df = study([scenario(name, seed=s) for s in range(501, 513)], bootstrap_reps=30, sims=250)
    assert abs(df["market_rel_error"].mean()) < 0.04
    assert df["market_rel_error"].abs().median() < 0.06
    assert df["market_covered"].mean() >= 0.85
    assert df["top_category_correct"].mean() >= 0.75


# ---------------------------------------------------------------- best-sellers rank (BSR)
def test_bsr_is_ingested_by_header_only_and_prefers_the_sub_category_rank():
    from dmie.engine.ingestion.schema_detector import detect_schema

    df = pd.DataFrame({"ASIN": ["B0AAAAAAA1", "B0AAAAAAA2", "B0AAAAAAA3"], "商品标题": ["a b c", "d e f", "g h i"],
                       "价格($)": [10.5, 20.1, 5.2], "子体销量": [50, 100, None], "评分": [4.1, 4.5, 3.9], "评分数": [10, 200, 3],
                       "大类BSR": [12000, 3400, 90000], "小类BSR": [120, 34, 900]})
    m = detect_schema(df).mapping
    assert m["bsr"] == "小类BSR" and m["reviews"] == "评分数"
    # whole numbers >= 1 alone are never taken for a rank (counts, weights and ids look the same)
    assert "bsr" not in detect_schema(df.drop(columns=["大类BSR", "小类BSR"]).assign(Qty=[3, 7, 12])).mapping


def test_bsr_features_order_listings_and_ignore_missing_ranks():
    from dip.metrics.demand import _raw_features

    f = pd.DataFrame({"price": [10.0] * 5, "bsr": [5.0, 50.0, 500.0, np.nan, 0.0]})
    r = _raw_features(f, None)
    assert r["log_bsr"].iloc[0] < r["log_bsr"].iloc[2] and r["log_bsr"].iloc[3:].isna().all()     # 0 is not a rank
    s = r["bsr_score"]
    assert s.iloc[0] > s.iloc[1] > s.iloc[2] and s.iloc[3:].isna().all() and abs(s.iloc[:3].mean()) < 1e-9

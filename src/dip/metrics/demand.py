"""Interval-censored demand model.

Each listing's monthly units X is observed only as an interval [L, U) (see observation.py).
Model:  log X_i ~ Normal(mu_i, sigma),  mu_i = b0 + b . z_i   (z = standardised covariates)

Covariates: log price (linear plus a restricted cubic spline when there is enough data, so the price
response may curve), rating, log listing age, sub-category indicators and -- when enabled in config and
the source has reviews -- log(1 + review count); missing values are median-imputed with a missing-indicator
term. (log_reviews is off by default: on synthetic markets with realistic review counts it made market
totals worse, see METHODOLOGY §1.5.)

fitted by maximum likelihood over the *intervals* (the likelihood of an interval is
Phi((log U - mu)/sigma) - Phi((log L - mu)/sigma)), with an L2 (ridge) penalty on b for
small-sample stability. This is the standard interval-regression (a generalised Tobit) model.

Given the fit, each listing's units are known to lie in [L, U) and follow the model *truncated
to that interval* -- so a badge "200+" listing is estimated inside 200-299, and a listing with no
badge inside 0-49, placed by its covariates. Aggregates (market, segment, brand) come from joint
simulation over parameter uncertainty (bootstrap refits) and within-interval uncertainty.

Assumptions (reported with every result): log-normal units given covariates; a listing without a
badge sells below the first rung (badge data only); the cross-sectional price coefficient is an
association, not a causal elasticity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import optimize
from scipy.special import log_ndtr, ndtr, ndtri, stdtr, stdtrit
from scipy.stats import t as student_t

from dip.metrics import config
from dip.metrics.observation import Observation

_LOG2PI = np.log(2 * np.pi)


# ------------------------------------------------------------------ design matrix
_RCS = "_rcs"                                          # spline term names: "<covariate>_rcs<j>"


def rcs_basis(x: np.ndarray, knots) -> np.ndarray:
    """Restricted (natural) cubic spline basis without the linear term (Harrell's parametrisation):
    K knots give K - 2 columns; the fitted curve is cubic between knots and linear beyond the boundary
    knots, so extrapolation (e.g. a launch price outside the data) stays linear."""
    t = np.asarray(knots, dtype=float)
    k = len(t)
    x = np.asarray(x, dtype=float)
    scale = (t[-1] - t[0]) ** 2 or 1.0

    def p3(v):
        return np.maximum(v, 0.0) ** 3

    cols = [(p3(x - t[j]) - p3(x - t[-2]) * (t[-1] - t[j]) / (t[-1] - t[-2])
             + p3(x - t[-1]) * (t[-2] - t[j]) / (t[-1] - t[-2])) / scale for j in range(k - 2)]
    return np.column_stack(cols) if cols else np.empty((len(x), 0))


def rcs_derivative(x: np.ndarray, knots) -> np.ndarray:
    """d basis / d x (same columns as ``rcs_basis``)."""
    t = np.asarray(knots, dtype=float)
    k = len(t)
    x = np.asarray(x, dtype=float)
    scale = (t[-1] - t[0]) ** 2 or 1.0

    def d3(v):
        return 3 * np.maximum(v, 0.0) ** 2

    cols = [(d3(x - t[j]) - d3(x - t[-2]) * (t[-1] - t[j]) / (t[-1] - t[-2])
             + d3(x - t[-1]) * (t[-2] - t[j]) / (t[-1] - t[-2])) / scale for j in range(k - 2)]
    return np.column_stack(cols) if cols else np.empty((len(x), 0))


@dataclass
class Design:
    names: list[str]
    means: dict
    sds: dict
    categories: list[str]
    medians: dict
    knots: dict = field(default_factory=dict)           # covariate -> spline knots (on the covariate's scale)

    def _spline(self, raw: pd.DataFrame, name: str) -> np.ndarray:
        base, j = name.rsplit(_RCS, 1)
        v = raw[base].fillna(self.medians[base]).to_numpy(dtype=float)
        return rcs_basis(v, self.knots[base])[:, int(j) - 1]

    def matrix(self, frame: pd.DataFrame, as_of: pd.Timestamp | None) -> np.ndarray:
        raw = _raw_features(frame, as_of)
        cols = []
        for n in self.names:
            if n.startswith("cat="):
                cat = _pool(frame.get("category"), self.categories)
                cols.append((cat == n[4:]).astype(float).to_numpy())
            elif n.endswith("_missing"):
                cols.append(raw[n[:-8]].isna().astype(float).to_numpy())
            elif _RCS in n:
                cols.append((self._spline(raw, n) - self.means[n]) / self.sds[n])
            else:
                v = raw[n].fillna(self.medians[n])
                cols.append(((v - self.means[n]) / self.sds[n]).to_numpy(dtype=float))
        return np.column_stack([np.ones(len(frame))] + cols) if cols else np.ones((len(frame), 1))

    def spline_terms(self, base: str) -> list[str]:
        return [n for n in self.names if n.startswith(base + _RCS)]


def _raw_features(frame: pd.DataFrame, as_of) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    price = pd.to_numeric(frame.get("price"), errors="coerce")
    out["log_price"] = np.log(price.where(price > 0))
    out["rating"] = pd.to_numeric(frame.get("rating"), errors="coerce")
    rv = pd.to_numeric(frame["reviews"], errors="coerce") if "reviews" in frame else pd.Series(np.nan, index=frame.index)
    out["log_reviews"] = np.log1p(rv.where(rv >= 0))
    bsr = pd.to_numeric(frame["bsr"], errors="coerce") if "bsr" in frame else pd.Series(np.nan, index=frame.index)
    out["log_bsr"] = np.log(bsr.where(bsr >= 1))                       # best-sellers rank: sales fall ~ as a power of it
    # normal score of the listing's BSR order within the export: if log sales are ~normal and BSR orders them
    # (noisily), log sales are linear in this score -- unlike log BSR, which flattens for slow sellers -- and it
    # does not depend on the (unknown) number of listings in the whole category
    ok = bsr.where(bsr >= 1)
    q = (ok.rank(method="average") - 0.5) / max(int(ok.notna().sum()), 1)
    out["bsr_score"] = pd.Series(-ndtri(q.to_numpy(dtype=float)), index=frame.index).where(ok.notna())
    ld = pd.to_datetime(frame.get("launch_date"), errors="coerce") if "launch_date" in frame else pd.Series(pd.NaT, index=frame.index)
    if as_of is not None and ld.notna().any():
        age = (pd.Timestamp(as_of) - ld).dt.days.clip(lower=1)
        out["log_age_days"] = np.log(age.where(age > 0))
    else:
        out["log_age_days"] = np.nan
    return out


def _pool(cat: pd.Series | None, keep: list[str]) -> pd.Series:
    if cat is None:
        return pd.Series(dtype=str)
    c = cat.fillna("(unknown)").astype(str)
    return c.where(c.isin(keep), "(other)")


def _spline_knots(v: pd.Series, n_informative: int | None) -> list[float] | None:
    """Knots for the log-price spline, or None (linear) when the data are too few to support a curve."""
    sp = config()["demand_model"].get("price_spline") or {}
    qs = sp.get("quantiles") or []
    if not sp.get("enabled", False) or len(qs) < 3:
        return None
    if n_informative is not None and n_informative < sp.get("min_informative", 0):
        return None
    x = v.dropna()
    if x.nunique() < max(len(qs) * 2, sp.get("min_distinct", 0)):
        return None
    knots = np.quantile(x, qs)
    if np.any(np.diff(knots) <= 1e-9):
        return None
    return [float(k) for k in knots]


def build_design(frame: pd.DataFrame, as_of, covariates: list[str] | None = None,
                 n_informative: int | None = None) -> Design:
    """``n_informative``: observations that carry information on the curve (badged listings for badge data,
    observed values for exact data). Too few -> the price response stays linear (``price_spline``)."""
    cfg = config()["demand_model"]
    covariates = covariates or cfg["covariates"]
    raw = _raw_features(frame, as_of)
    names, means, sds, medians, knots = [], {}, {}, {}, {}
    for c in ("log_price", "rating", "log_age_days", "log_reviews", "log_bsr", "bsr_score"):
        if c not in covariates:
            continue
        v = raw[c]
        if v.notna().sum() < 3 or float(v.std(skipna=True) or 0) == 0:
            continue
        names.append(c)
        medians[c] = float(v.median())
        means[c] = float(v.fillna(medians[c]).mean())
        sds[c] = float(v.fillna(medians[c]).std()) or 1.0
        if v.isna().any():
            names.append(f"{c}_missing")
        if c == "log_price":
            kn = _spline_knots(v, n_informative)
            if kn is not None:
                knots[c] = kn
                B = rcs_basis(v.fillna(medians[c]).to_numpy(dtype=float), kn)
                for j in range(B.shape[1]):
                    nm = f"{c}{_RCS}{j + 1}"
                    names.append(nm)
                    means[nm] = float(B[:, j].mean())
                    sds[nm] = float(B[:, j].std(ddof=1)) or 1.0
    cats: list[str] = []
    if "category" in covariates and "category" in frame:
        counts = frame["category"].fillna("(unknown)").astype(str).value_counts()
        cats = sorted(counts[counts >= cfg["category_min_listings"]].index.tolist())
        pooled = _pool(frame["category"], cats)
        levels = sorted(pooled.unique())
        if len(levels) > 1:
            ref = pooled.value_counts().idxmax()                       # most common level is the baseline
            names += [f"cat={lv}" for lv in levels if lv != ref]
    return Design(names, means, sds, cats, medians, knots)


# ------------------------------------------------------------------ error distributions
class Dist:
    """Standardised error distribution of log units: normal (df=None) or Student-t (df)."""

    def __init__(self, df: float | None = None):
        self.df = df

    @property
    def name(self) -> str:
        return "normal" if self.df is None else f"student-t({self.df:g})"

    def cdf(self, z):
        return ndtr(z) if self.df is None else stdtr(self.df, z)

    def sf(self, z):
        return self.cdf(-z)

    def ppf(self, p):
        return ndtri(p) if self.df is None else stdtrit(self.df, p)

    def pdf(self, z):
        zz = np.where(np.isfinite(z), z, 0.0)
        if self.df is None:
            v = np.exp(-0.5 * zz * zz) / np.sqrt(2 * np.pi)
        else:
            v = student_t.pdf(zz, self.df)
        return v * np.isfinite(z)

    def logcdf(self, z):
        return log_ndtr(z) if self.df is None else np.log(np.maximum(stdtr(self.df, z), 1e-300))

    def nll_exact(self, r):
        if self.df is None:
            return 0.5 * r * r + 0.5 * _LOG2PI
        return -student_t.logpdf(r, self.df)

    def dnll_dr(self, r):
        return r if self.df is None else (self.df + 1) * r / (self.df + r * r)

    def rvs(self, rng, n):
        return rng.standard_normal(n) if self.df is None else rng.standard_t(self.df, n)


def _log_interval_prob(a: np.ndarray, b: np.ndarray, dist: Dist | None = None) -> np.ndarray:
    """log(F(b) - F(a)) computed stably in both tails."""
    dist = dist or Dist()
    out = np.empty_like(a)
    upper = a > 0                                                         # both in the upper tail: use survival
    with np.errstate(divide="ignore", invalid="ignore"):              # an empty interval has log-probability -inf
        la, lb = dist.logcdf(-b[upper]), dist.logcdf(-a[upper])
        out[upper] = lb + np.log1p(-np.exp(np.minimum(la - lb, -1e-300)))
        lo = ~upper
        la, lb = dist.logcdf(a[lo]), dist.logcdf(b[lo])
        out[lo] = lb + np.log1p(-np.exp(np.minimum(la - lb, -1e-300)))
    return out


def _nll_grad(params, X, logL, logU, exact, ridge, dist):
    beta, ls = params[:-1], params[-1]
    s = np.exp(ls)
    mu = X @ beta
    g_mu = np.zeros_like(mu)
    g_ls = 0.0
    nll = 0.0
    if exact.any():
        r = (logL[exact] - mu[exact]) / s
        nll += float(np.sum(dist.nll_exact(r) + ls))
        d = dist.dnll_dr(r)
        g_mu[exact] = -d / s
        g_ls += float(np.sum(1 - d * r))
    iv = ~exact
    if iv.any():
        a = (logL[iv] - mu[iv]) / s                                       # -inf for L = 0
        b = (logU[iv] - mu[iv]) / s
        lp = _log_interval_prob(a, b, dist)
        nll -= float(np.sum(lp))
        D = np.maximum(np.exp(lp), 1e-300)
        pa, pb = dist.pdf(a), dist.pdf(b)
        g_mu[iv] = (pb - pa) / (s * D)
        aa = np.where(np.isfinite(a), a, 0.0)
        bb = np.where(np.isfinite(b), b, 0.0)
        g_ls += float(np.sum((bb * pb - aa * pa) / D))
    reg = beta.copy()
    reg[0] = 0.0
    nll += 0.5 * ridge * float(reg @ reg)
    g_beta = X.T @ g_mu + ridge * reg
    return nll, np.append(g_beta, g_ls)


@dataclass
class Fit:
    beta: np.ndarray
    sigma: float
    names: list[str]
    n: int
    n_bounded: int
    converged: bool
    loglik: float
    df: float | None = None
    boot: list[tuple[np.ndarray, float, float | None]] = field(default_factory=list)
    family_selection: list[dict] = field(default_factory=list)
    bias_correction: dict | None = None

    @property
    def dist(self) -> Dist:
        return Dist(self.df)


def _prep(obs: Observation):
    exact = (obs.lower == obs.upper) & np.isfinite(obs.upper)
    lower = np.where(exact & (obs.lower <= 0), 0.0, obs.lower)
    upper = np.where(exact & (obs.upper <= 0), 0.5, obs.upper)       # an exact 0 = "below one unit"
    exact = exact & (lower > 0)
    with np.errstate(divide="ignore"):
        logL = np.where(lower > 0, np.log(np.maximum(lower, 1e-300)), -np.inf)
        logU = np.where(np.isfinite(upper), np.log(np.maximum(upper, 1e-300)), np.inf)
    return logL, logU, exact


def fit(X: np.ndarray, obs: Observation, ridge: float | None = None, names: list[str] | None = None,
        df: float | None = None) -> Fit:
    cfg = config()["demand_model"]
    ridge = cfg["ridge"] if ridge is None else ridge
    use = obs.bounded
    logL, logU, exact = _prep(obs)
    Xb, lL, lU, ex = X[use], logL[use], logU[use], exact[use]
    n_bounded = int(use.sum())
    n_informative = int((obs.lower[use] > 0).sum()) if obs.kind == "badge" else n_bounded
    if n_bounded < cfg["min_observed"] or (obs.kind == "badge" and n_informative < cfg.get("min_badged_for_covariates", 0)):
        # too little data: intercept only
        Xb = Xb[:, :1]
        names = []
    mid = np.where(np.isfinite(lU), (np.where(np.isfinite(lL), lL, lU - 1.0) + lU) / 2, lL)
    x0 = np.zeros(Xb.shape[1] + 1)
    x0[0] = float(np.nanmedian(mid)) if n_bounded else 0.0
    x0[-1] = np.log(max(float(np.nanstd(mid)) if n_bounded > 1 else 1.0, 0.3))
    dist = Dist(df)
    res = optimize.minimize(_nll_grad, x0, args=(Xb, lL, lU, ex, ridge, dist), jac=True, method="L-BFGS-B",
                            bounds=[(None, None)] * Xb.shape[1] + [(np.log(0.05), np.log(10))])
    beta = res.x[:-1]
    if Xb.shape[1] < X.shape[1]:
        beta = np.concatenate([beta, np.zeros(X.shape[1] - Xb.shape[1])])
    return Fit(beta, float(np.exp(res.x[-1])), (names if names is not None else []), len(obs.lower), n_bounded,
               bool(res.success), float(-res.fun), df)


def select_family(X: np.ndarray, obs: Observation, names: list[str]) -> Fit:
    """Fit every configured error family and keep the one with the best AIC (t families pay one
    extra parameter for their degrees of freedom). Heavy-tailed markets get heavy-tailed errors."""
    dm = config()["demand_model"]
    fams = dm.get("error_families", ["normal"])
    if obs.kind == "badge" and int((obs.lower > 0).sum()) < dm.get("min_badged_for_heavy_tails", 0):
        fams = ["normal"]                                                  # tail shape not identifiable
    fits, table = [], []
    for fam in fams:
        df = None if fam == "normal" else float(str(fam).lstrip("t"))
        f = fit(X, obs, names=names, df=df)
        k = len(f.beta) + 1 + (0 if df is None else 1)
        aic = 2 * k - 2 * f.loglik
        fits.append((aic, f))
        table.append({"family": Dist(df).name, "loglik": round(f.loglik, 2), "aic": round(aic, 2)})
    best = min(fits, key=lambda t: t[0])[1]
    best.family_selection = sorted(table, key=lambda r: r["aic"])
    return best


def _reobserve(units: np.ndarray, like: Observation) -> Observation:
    """Apply the same observation process to simulated units (badge ladder, or exact where observed)."""
    if like.kind == "badge":
        ladder = np.array(config()["sales_observation"]["badge_ladder"], dtype=float)
        idx = np.searchsorted(ladder, units, side="right") - 1
        known = idx >= 0
        lo = np.where(known, ladder[np.clip(idx, 0, None)], 0.0)
        nxt = np.where(idx + 1 < len(ladder), ladder[np.clip(idx + 1, 0, len(ladder) - 1)],
                       ladder[-1] * config()["sales_observation"]["top_rung_multiplier"])
        up = np.where(known, nxt, ladder[0])
        return Observation("badge", lo, up, known, like.evidence)
    known = like.known.copy()
    return Observation("exact", np.where(known, units, 0.0), np.where(known, units, np.inf), known, like.evidence)


def bootstrap(X: np.ndarray, obs: Observation, f: Fit, reps: int | None = None, seed: int | None = None,
              method: str | None = None) -> Fit:
    """Parameter uncertainty by refitting on resampled data.

    parametric (default): simulate units from the fitted model, re-apply the observation process
    (badge censoring), refit. Captures the uncertainty of extrapolating below the first badge rung,
    which a case bootstrap understates in small samples (measured with dip.metrics.synthetic).
    case: resample listings with replacement.
    """
    cfg = config()["demand_model"]
    reps = cfg["bootstrap"] if reps is None else reps
    method = method or cfg.get("bootstrap_method", "parametric")
    rng = np.random.default_rng(cfg["seed"] if seed is None else seed)
    n = len(obs.lower)
    m = min(n, cfg["max_bootstrap_rows"])
    out = []
    mu_hat = X @ f.beta
    dist = f.dist
    for _ in range(reps):
        idx = rng.integers(0, n, m) if m < n or method == "case" else np.arange(n)
        if method == "parametric":
            units = np.exp(np.clip(mu_hat[idx] + f.sigma * dist.rvs(rng, len(idx)), -50, 50))
            sub = _reobserve(units, Observation(obs.kind, obs.lower[idx], obs.upper[idx], obs.known[idx], obs.evidence))
        else:
            sub = Observation(obs.kind, obs.lower[idx], obs.upper[idx], obs.known[idx], obs.evidence)
        if cfg.get("bootstrap_family_selection", True) and method == "parametric":
            g = select_family(X[idx], sub, f.names)                       # model-selection uncertainty
            g.beta = g.beta if len(g.beta) == len(f.beta) else f.beta
        else:
            g = fit(X[idx], sub, names=f.names, df=f.df)
        if m < n:                                                         # subset bootstrap: rescale spread
            g.beta = f.beta + (g.beta - f.beta) * np.sqrt(m / n)
            g.sigma = f.sigma * np.exp((np.log(g.sigma) - np.log(f.sigma)) * np.sqrt(m / n))
        out.append((g.beta, g.sigma, g.df))
    f.boot = out
    if method == "parametric" and cfg.get("bias_correction", True) and len(out) >= 10:
        # bootstrap bias correction: theta_bc = theta_hat - (mean(theta*) - theta_hat); draws recentred on it
        B = np.array([b for b, _, _ in out])
        LS = np.log([sg for _, sg, _ in out])
        beta_bc = 2 * f.beta - B.mean(axis=0)
        ls_bc = 2 * np.log(f.sigma) - LS.mean()
        f.bias_correction = {"beta_shift": np.round(beta_bc - f.beta, 4).tolist(), "sigma_ratio": round(float(np.exp(ls_bc) / f.sigma), 4)}
        f.boot = [(b - B.mean(axis=0) + beta_bc, float(np.exp(ls - LS.mean() + ls_bc)), d)
                  for b, ls, (_, _, d) in zip(B, LS, out)]
        f.beta, f.sigma = beta_bc, float(np.exp(ls_bc))
    return f


# ------------------------------------------------------------------ conditional distribution
def _bounds(obs: Observation, mu: np.ndarray, s):
    logL, logU, exact = _prep(obs)
    a = np.where(np.isfinite(logL), (logL - mu) / s, -np.inf)
    b = np.where(np.isfinite(logU), (logU - mu) / s, np.inf)
    return a, b, exact


def _obs_limits(obs: Observation):
    logL, logU, _ = _prep(obs)
    lo = np.where(np.isfinite(logL), np.exp(np.where(np.isfinite(logL), logL, 0)), 0.0)
    hi = np.where(np.isfinite(logU), np.exp(np.where(np.isfinite(logU), logU, 0)), np.inf)
    return lo, hi


def conditional_quantile(obs: Observation, mu: np.ndarray, s, q: float | np.ndarray, dist: Dist | None = None) -> np.ndarray:
    """Quantile of X given the observed interval: the fitted distribution truncated to [L, U)."""
    dist = dist or Dist()
    a, b, exact = _bounds(obs, mu, s)
    q = np.asarray(q)
    Fa, Fb = dist.cdf(a), dist.cdf(b)
    Sa, Sb = dist.sf(a), dist.sf(b)
    use_up = a > 0                                                        # upper-tail form avoids 1 - 1 cancellation
    with np.errstate(invalid="ignore", divide="ignore"):
        z_up = -dist.ppf(np.clip(Sa - q * (Sa - Sb), 1e-300, 1 - 1e-16))
        z_lo = dist.ppf(np.clip(Fa + q * (Fb - Fa), 1e-300, 1 - 1e-16))
    z = np.where(use_up, z_up, z_lo)
    x = np.exp(np.clip(mu + s * z, -700, 700))
    lo, hi = _obs_limits(obs)
    return np.where(exact, obs.lower, np.clip(x, lo, hi))


def conditional_mean(obs: Observation, mu: np.ndarray, s, dist: Dist | None = None, nodes: int | None = None) -> np.ndarray:
    """E[X | L <= X < U]. Normal errors: closed form
    exp(mu + s^2/2) (Phi(b - s) - Phi(a - s)) / (Phi(b) - Phi(a)); other families: midpoint quadrature
    over conditional quantiles (``quadrature_nodes``)."""
    dist = dist or Dist()
    a, b, exact = _bounds(obs, mu, s)
    lo, hi = _obs_limits(obs)
    if dist.df is None:
        num = _log_interval_prob(a - s, b - s)
        den = _log_interval_prob(a, b)
        with np.errstate(invalid="ignore"):
            m = np.exp(np.clip(mu + s * s / 2 + num - den, -700, 700))
    else:
        k = nodes or config()["demand_model"].get("quadrature_nodes", 64)
        qs = (np.arange(k) + 0.5) / k
        m = np.mean([conditional_quantile(obs, mu, s, q, dist) for q in qs], axis=0)
    return np.where(exact, obs.lower, np.clip(m, lo, hi))


def draw(obs: Observation, mu: np.ndarray, s, rng, dist: Dist | None = None) -> np.ndarray:
    return conditional_quantile(obs, mu, s, rng.random(len(mu)), dist)


# ------------------------------------------------------------------ high-level API
@dataclass
class DemandResult:
    units_est: np.ndarray             # conditional mean per listing
    units_lo: np.ndarray              # conditional interval per listing
    units_hi: np.ndarray
    floor: np.ndarray                 # observation interval
    ceiling: np.ndarray
    fit: Fit
    design: Design
    observation: Observation
    mu: np.ndarray
    X: np.ndarray

    def simulate(self, groups: dict[str, np.ndarray], weights: np.ndarray | None = None, sims: int | None = None,
                 seed: int | None = None) -> dict[str, np.ndarray]:
        """Joint draws of group totals. ``groups``: name -> integer group index per listing (-1 = excluded).
        ``weights`` (e.g. price) turns units into revenue. Returns name -> array (sims, n_groups)."""
        cfg = config()["demand_model"]
        sims = cfg["simulations"] if sims is None else sims
        rng = np.random.default_rng(cfg["seed"] + 1 if seed is None else seed)
        thetas = self.fit.boot or [(self.fit.beta, self.fit.sigma, self.fit.df)]
        w = np.ones(len(self.mu)) if weights is None else np.nan_to_num(weights, nan=0.0)
        out = {k: np.zeros((sims, int(g.max()) + 1 if len(g) and g.max() >= 0 else 0)) for k, g in groups.items()}
        for i in range(sims):
            beta, s, df = thetas[i % len(thetas)]
            x = draw(self.observation, self.X @ beta, s, rng, Dist(df)) * w
            for k, g in groups.items():
                if out[k].shape[1]:
                    ok = g >= 0
                    out[k][i] = np.bincount(g[ok], weights=x[ok], minlength=out[k].shape[1])
        # Every listing's draw lies inside its observation interval, so every group total lies inside
        # [sum of floors, sum of ceilings]. Inflation and anchoring below work on the excess over the group's
        # floor, so they can never push a total under what was certainly sold (they used to: a market's low
        # could fall below its own observed floor).
        fl = np.nan_to_num(self.floor) * w
        ce = np.where(w == 0, 0.0, self.ceiling * w)
        base = self.units_est * w
        c = cfg.get("interval_inflation", 1.0)
        for k, g in groups.items():
            if not out[k].shape[1]:
                continue
            ok = g >= 0
            n = out[k].shape[1]
            gfloor = np.bincount(g[ok], weights=fl[ok], minlength=n)
            gcap = np.bincount(g[ok], weights=np.where(np.isfinite(ce[ok]), ce[ok], 0.0), minlength=n)
            gcap = np.where(np.bincount(g[ok], weights=(~np.isfinite(ce[ok])).astype(float), minlength=n) > 0, np.inf, gcap)
            e = np.maximum(out[k] - gfloor, 0.0)
            if c != 1.0:                                                  # width calibrated on synthetic markets
                me = e.mean(axis=0, keepdims=True)
                e = np.maximum(me + c * (e - me), 0.0)
            # Anchor every group's draws on the sum of its listings' conditional means: point estimates are then
            # deterministic and add up exactly across levels (listing -> product -> segment -> market), and the
            # intervals, shares and ranks computed from the draws stay consistent with them. Rescaling the excess
            # (not shifting) keeps draws at or above the floor; the factor is ~1 (Monte Carlo and
            # parameter-averaging noise, plus the mean lift of the clips).
            target = np.maximum(np.bincount(g[ok], weights=base[ok], minlength=n) - gfloor, 0.0)
            # A group with finite ceilings also keeps its draws at or under the sum of its ceilings: draws that
            # hit it are held there and the others are rescaled until the mean is back on target (feasible:
            # the conditional mean lies inside [floor, ceiling]).
            room = gcap - gfloor
            held = np.zeros_like(e, dtype=bool)
            for _ in range(50):
                free = np.where(held, 0.0, e).sum(axis=0)
                need = target * len(e) - np.where(held, room, 0.0).sum(axis=0)
                e = np.where(held, room, e * np.divide(need, free, out=np.ones_like(free), where=free > 0))
                over = ~held & (e > room + 1e-9)
                if not over.any():
                    break
                held |= over
            out[k] = gfloor + np.minimum(e, room)
        return out

    def coefficients(self) -> list[dict]:
        rows = []
        se = np.std(np.array([b for b, _, _ in self.fit.boot]), axis=0) if self.fit.boot else np.full(len(self.fit.beta), np.nan)
        for i, name in enumerate(["(intercept)"] + self.design.names):
            if i >= len(self.fit.beta):
                break
            b = float(self.fit.beta[i])
            row = {"term": name, "coef_std": round(b, 4), "se": None if np.isnan(se[i]) else round(float(se[i]), 4)}
            if name in self.design.sds:                                   # per unit of the raw covariate
                row["coef_per_unit"] = round(b / self.design.sds[name], 4)
            rows.append(row)
        return rows

    def _elasticity_at(self, x: np.ndarray, beta: np.ndarray) -> np.ndarray:
        """d log units / d log price at log prices ``x`` for coefficients ``beta``."""
        d = self.design
        i = 1 + d.names.index("log_price")
        e = np.full(len(x), beta[i] / d.sds["log_price"])
        terms = d.spline_terms("log_price")
        if terms:
            D = rcs_derivative(x, d.knots["log_price"])
            for j, nm in enumerate(terms):
                e = e + beta[1 + d.names.index(nm)] / d.sds[nm] * D[:, j]
        return e

    def price_elasticity(self) -> dict | None:
        """d log units / d log price (cross-sectional association, not causal).

        Linear price term: one number. With the price spline the elasticity varies with price; ``value`` is
        the average elasticity over the market's listings (average marginal effect) and ``local`` gives it
        at the 25th / 50th / 75th price percentiles, each with a bootstrap 95 % interval."""
        d = self.design
        if "log_price" not in d.names:
            return None
        i = 1 + d.names.index("log_price")
        x = self.X[:, i] * d.sds["log_price"] + d.means["log_price"]       # imputed log prices of the listings
        boots = [np.asarray(b) for b, _, _ in self.fit.boot] if len(self.fit.boot) >= 10 else []

        def summ(xs):
            v = self._elasticity_at(xs, self.fit.beta).mean()
            if boots:
                bs = [self._elasticity_at(xs, b).mean() for b in boots]
                lo, hi = np.percentile(bs, [2.5, 97.5])
            else:
                lo = hi = np.nan
            return {"value": round(float(v), 3), "low": None if np.isnan(lo) else round(float(lo), 3),
                    "high": None if np.isnan(hi) else round(float(hi), 3)}

        spline = bool(d.spline_terms("log_price"))
        out = {**summ(x), "form": f"spline ({len(d.knots['log_price'])} knots)" if spline else "linear",
               "note": "cross-sectional association of price with units, holding the other covariates fixed; not a causal elasticity"
               + ("; the price response is a spline, so the elasticity varies with price: value = average over the "
                  "listings, local = at price percentiles" if spline else "")}
        if spline:
            out["local"] = [{"percentile": q, "price": round(float(np.exp(xq)), 2), **summ(np.array([xq]))}
                            for q, xq in zip((25, 50, 75), np.percentile(x, [25, 50, 75]))]
        return out


def n_informative(obs: Observation) -> int:
    """Observations that inform the shape of the curve: badged listings (badge data) or observed values."""
    return int((obs.bounded & (obs.lower > 0)).sum()) if obs.kind == "badge" else int((obs.bounded & obs.known).sum())


def estimate(frame: pd.DataFrame, obs: Observation, as_of=None, bootstrap_reps: int | None = None) -> DemandResult:
    cfg = config()["demand_model"]
    design = build_design(frame, as_of, n_informative=n_informative(obs))
    X = design.matrix(frame, as_of)
    f = select_family(X, obs, design.names)
    if not f.names and design.names:                                     # fell back to intercept-only
        design = Design([], {}, {}, [], {})
        X = X[:, :1]
        f.beta = f.beta[:1]
    f = bootstrap(X, obs, f, reps=bootstrap_reps)
    mu = X @ f.beta
    lvl = cfg["interval"]
    est = conditional_mean(obs, mu, f.sigma, f.dist)
    lo = conditional_quantile(obs, mu, f.sigma, (1 - lvl) / 2, f.dist)
    hi = conditional_quantile(obs, mu, f.sigma, 1 - (1 - lvl) / 2, f.dist)
    ceiling = np.where(np.isfinite(obs.upper), obs.upper, np.nan)
    return DemandResult(est, lo, hi, obs.lower.copy(), ceiling, f, design, obs, mu, X)


# ------------------------------------------------------------------ validation on real observations
def crossvalidate(frame: pd.DataFrame, obs: Observation, as_of=None, folds: int | None = None) -> dict:
    """Hide listings fold by fold, predict them from the rest, and score against what was observed.

    Badge data: discrimination of badged vs unbadged (AUC of P(X >= first rung)), rung accuracy of the
    predictive median for badged listings, and coverage of the 80 % / 95 % predictive intervals.
    """
    cfg = config()
    folds = folds or cfg["demand_model"]["holdout_folds"]
    rng = np.random.default_rng(cfg["demand_model"]["seed"] + 7)
    n = len(obs.lower)
    order = rng.permutation(n)
    fold = np.empty(n, dtype=int)
    fold[order] = np.arange(n) % folds
    design = build_design(frame, as_of, n_informative=n_informative(obs))
    X = design.matrix(frame, as_of)
    p_ge, med, lo80, hi80, lo95, hi95 = (np.full(n, np.nan) for _ in range(6))
    first = cfg["sales_observation"]["badge_ladder"][0]
    for k in range(folds):
        test = fold == k
        train_obs = Observation(obs.kind, obs.lower[~test], obs.upper[~test], obs.known[~test], obs.evidence)
        f = select_family(X[~test], train_obs, design.names)
        Xt = X[test] if len(f.beta) == X.shape[1] else X[test][:, : len(f.beta)]
        mu = Xt @ f.beta
        s = f.sigma
        dist = f.dist
        p_ge[test] = dist.sf((np.log(first) - mu) / s)
        med[test] = np.exp(mu)
        for q, arr in ((0.1, lo80), (0.9, hi80), (0.025, lo95), (0.975, hi95)):
            arr[test] = np.exp(mu + s * dist.ppf(q))
    out = {"folds": folds, "n": int(n), "kind": obs.kind}
    bounded = obs.bounded & obs.known
    if obs.kind == "badge":
        y = obs.known.astype(int)
        out["auc_badged_vs_unbadged"] = _auc(p_ge, y) if 0 < y.sum() < n else None
        mid = np.sqrt(np.maximum(obs.lower, 1e-9) * obs.upper)                 # geometric middle of the rung
        kb = obs.known
        if kb.any():
            ladder = np.array(cfg["sales_observation"]["badge_ladder"], dtype=float)
            rung_true = np.searchsorted(ladder, obs.lower[kb], side="right") - 1
            rung_pred = np.searchsorted(ladder, np.maximum(med[kb], 0), side="right") - 1
            out["badged_listings"] = int(kb.sum())
            out["rung_exact_rate"] = round(float(np.mean(rung_pred == rung_true)), 4)
            out["rung_within_one_rate"] = round(float(np.mean(np.abs(rung_pred - rung_true) <= 1)), 4)
            out["coverage_80"] = round(float(np.mean((mid[kb] >= lo80[kb]) & (mid[kb] <= hi80[kb]))), 4)
            out["coverage_95"] = round(float(np.mean((mid[kb] >= lo95[kb]) & (mid[kb] <= hi95[kb]))), 4)
    elif bounded.any():
        y = obs.lower[bounded]
        out["median_abs_log_error"] = round(float(np.median(np.abs(np.log(np.maximum(y, 0.5)) - np.log(med[bounded])))), 4)
        out["coverage_80"] = round(float(np.mean((y >= lo80[bounded]) & (y <= hi80[bounded]))), 4)
        out["coverage_95"] = round(float(np.mean((y >= lo95[bounded]) & (y <= hi95[bounded]))), 4)
    return out


def _auc(score: np.ndarray, y: np.ndarray) -> float:
    ok = ~np.isnan(score)
    s, y = score[ok], y[ok]
    ranks = pd.Series(s).rank().to_numpy()
    n1, n0 = y.sum(), len(y) - y.sum()
    return round(float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), 4)


# ------------------------------------------------------------------ persisted model (Phase 6: launch simulator)
def model_card(res: DemandResult, as_of) -> dict:
    """Everything needed to predict a hypothetical listing later: design, coefficients, bootstrap draws."""
    d, f = res.design, res.fit
    return {"as_of": str(pd.Timestamp(as_of).date()) if as_of is not None else None,
            "design": {"names": d.names, "means": d.means, "sds": d.sds, "categories": d.categories, "medians": d.medians,
                       "knots": d.knots},
            "beta": [float(x) for x in f.beta], "sigma": float(f.sigma), "df": f.df, "family": f.dist.name,
            "n": int(f.n), "n_bounded": int(f.n_bounded),
            "boot": [{"beta": [float(x) for x in b], "sigma": float(s), "df": df} for b, s, df in f.boot]}


def card_design(card: dict) -> Design:
    d = card["design"]
    return Design(list(d["names"]), dict(d["means"]), dict(d["sds"]), list(d["categories"]), dict(d["medians"]),
                  {k: list(v) for k, v in (d.get("knots") or {}).items()})


def card_mu(card: dict, frame: pd.DataFrame, as_of=None) -> np.ndarray:
    """Linear predictor (log units, location) of the point fit for ``frame``."""
    as_of = pd.Timestamp(as_of or card["as_of"]) if (as_of or card.get("as_of")) else None
    return card_design(card).matrix(frame, as_of) @ np.asarray(card["beta"])


def predict_draws(card: dict, frame: pd.DataFrame, sims: int, seed: int, offset: np.ndarray | float = 0.0,
                  as_of=None, shared_noise: bool = False) -> np.ndarray:
    """Posterior-predictive draws of monthly units for hypothetical listings ``frame`` (sims x rows):
    parameter uncertainty from the bootstrap draws, idiosyncratic noise from the fitted error family.
    The same seed gives the same noise for every call (common random numbers across scenarios);
    ``shared_noise`` uses one draw per simulation for every row -- the same hypothetical listing at
    different prices (a price curve), not different listings."""
    as_of = pd.Timestamp(as_of or card["as_of"]) if (as_of or card.get("as_of")) else None
    X = card_design(card).matrix(frame, as_of)
    thetas = card["boot"] or [{"beta": card["beta"], "sigma": card["sigma"], "df": card["df"]}]
    rng = np.random.default_rng(seed)
    so = config()["sales_observation"]
    cap = np.log(so["badge_ladder"][-1] * so["top_rung_multiplier"])   # no listing sells above the top rung's bound
    out = np.empty((sims, len(frame)))
    for i in range(sims):
        th = thetas[i % len(thetas)]
        mu = X @ np.asarray(th["beta"]) + offset

        def noise(k):
            if shared_noise:
                z = rng.standard_normal() if th["df"] is None else rng.standard_t(th["df"])
                return np.full(k, z)
            return rng.standard_normal(k) if th["df"] is None else rng.standard_t(th["df"], k)

        x = mu + th["sigma"] * noise(len(frame))
        for _ in range(50):                                            # the model's support ends at the cap:
            bad = x >= cap                                             # redraw (truncate), never clip
            if not bad.any():
                break
            x[bad] = mu[bad] + th["sigma"] * noise(int(bad.sum()))
        out[i] = np.exp(np.minimum(x, cap))
    return out

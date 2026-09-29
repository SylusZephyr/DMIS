"""Demand growth and forecast on metrics v3 (post-Phase 8).

Input: one estimate per snapshot period with its 95% interval (market or segment revenue), i.e. a noisy
measurement of the true level. Model (random-effects log-linear trend):

    log y_i = a + b * t_i + u_i + e_i,    e_i ~ N(0, s_i^2)  (measurement: from each snapshot's interval)
                                          u_i ~ N(0, tau^2)  (real period-to-period variation, estimated)

* tau^2 by the method of moments (DerSimonian-Laird generalised to regression): the residual heterogeneity
  beyond what the measurement errors explain, floored at 0.
* a, b by weighted least squares with w_i = 1 / (s_i^2 + tau^2); their covariance from the WLS fit.
* growth per month = exp(b) - 1 with a t-interval (n - 2 df); a two-sided test of b = 0.
* forecast h months ahead: log level a + b * (t_last + h), predictive variance = var(a + b t) + tau^2,
  t-quantiles -- the interval is for the true level of that month. A horizon longer than the observed span
  is flagged ``beyond_span``: it assumes the trend continues, which the data cannot confirm.

With fewer than ``min_periods`` snapshots nothing is fitted (status says how many exist). Seasonality needs
at least two years of monthly snapshots and is not modelled here (stated). Settings: ``forecast`` in
config/platform/metrics.yaml.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from dip.metrics import config


def _months(periods: pd.Series) -> np.ndarray:
    p = pd.to_datetime(periods)
    return ((p - p.min()).dt.days / 30.4375).to_numpy(dtype=float)


def fit(history: pd.DataFrame, level: float | None = None) -> dict:
    """history: columns period, est, lo, hi (one row per snapshot). Returns growth, trend test, forecasts."""
    cfg = config()["forecast"]
    level = level or config()["engine"]["interval"]
    h = history.dropna(subset=["est", "lo", "hi"])
    h = h[(h["est"] > 0) & (h["lo"] > 0) & (h["hi"] > 0)].sort_values("period").drop_duplicates("period", keep="last")
    n = len(h)
    if n < cfg["min_periods"]:
        return {"status": "needs_snapshots", "periods": n, "min_periods": cfg["min_periods"]}
    t = _months(h["period"])
    y = np.log(h["est"].to_numpy(dtype=float))
    zc = stats.norm.ppf(0.5 + level / 2)
    s2 = ((np.log(h["hi"].to_numpy(dtype=float)) - np.log(h["lo"].to_numpy(dtype=float))) / (2 * zc)) ** 2
    s2 = np.maximum(s2, cfg["min_measurement_sd"] ** 2)
    X = np.column_stack([np.ones(n), t])

    def wls(w):
        W = np.diag(w)
        cov = np.linalg.inv(X.T @ W @ X)
        beta = cov @ X.T @ W @ y
        return beta, cov

    # method-of-moments heterogeneity (fixed-effect fit first)
    beta_fe, _ = wls(1 / s2)
    resid = y - X @ beta_fe
    q = float(np.sum(resid ** 2 / s2))
    w0 = 1 / s2
    W0 = np.diag(w0)
    P = W0 - W0 @ X @ np.linalg.inv(X.T @ W0 @ X) @ X.T @ W0
    tau2 = max(0.0, (q - (n - 2)) / float(np.trace(P))) if n > 2 else 0.0
    beta, cov = wls(1 / (s2 + tau2))
    a, b = float(beta[0]), float(beta[1])
    df = max(n - 2, 1)
    tq = stats.t.ppf(0.5 + level / 2, df)
    se_b = float(np.sqrt(cov[1, 1]))
    p = float(2 * stats.t.sf(abs(b / se_b), df)) if se_b > 0 else (0.0 if b != 0 else 1.0)
    sig = p <= cfg["alpha"]
    direction = "growing" if sig and b > 0 else "declining" if sig and b < 0 else "no_significant_trend"
    last = float(t.max())
    span = last - float(t.min())
    last_period = pd.to_datetime(h["period"]).max()
    fc = []
    for k in cfg["horizons_months"]:
        tt = last + k
        xv = np.array([1.0, tt])
        var = float(xv @ cov @ xv) + tau2
        mu = a + b * tt
        half = tq * np.sqrt(var)
        fc.append({"months_ahead": k, "period": str((last_period + pd.DateOffset(months=k)).date()),
                   "estimate": float(np.exp(mu)), "low": float(np.exp(mu - half)), "high": float(np.exp(mu + half)),
                   "beyond_span": bool(k > span)})
    fitted = [{"period": str(pd.Timestamp(pp).date()), "observed": float(e), "observed_lo": float(lo), "observed_hi": float(hi),
               "fitted": float(np.exp(a + b * tt))}
              for pp, e, lo, hi, tt in zip(h["period"], h["est"], h["lo"], h["hi"], t)]
    return {"status": "ok", "periods": n, "growth_per_month": float(np.exp(b) - 1),
            "growth_lo": float(np.exp(b - tq * se_b) - 1), "growth_hi": float(np.exp(b + tq * se_b) - 1),
            "growth_12m": float(np.exp(12 * b) - 1), "p_value": p, "direction": direction, "tau": float(np.sqrt(tau2)), "span_months": span,
            "forecast": fc, "fitted": fitted,
            "method": "random-effects log-linear trend (WLS, method-of-moments heterogeneity), t intervals",
            "seasonality": "not modelled (needs >= 24 monthly snapshots)"}


def history_rows(period, summary: dict, segments: pd.DataFrame) -> pd.DataFrame:
    """One row for the market and one per segment: the v3 revenue estimate of this snapshot period."""
    pr = str(pd.Timestamp(period).date())
    rows = []
    rm = (summary or {}).get("revenue_month") or {}
    if rm:
        rows.append({"period": pr, "scope": "market", "scope_id": "", "label": "", "est": rm.get("estimate"),
                     "lo": rm.get("low"), "hi": rm.get("high")})
    if segments is not None and {"revenue_est", "revenue_lo", "revenue_hi"} <= set(segments.columns):
        for r in segments.to_dict("records"):
            rows.append({"period": pr, "scope": "segment", "scope_id": r["segment_id"], "label": r.get("segment_label"),
                         "est": r["revenue_est"], "lo": r["revenue_lo"], "hi": r["revenue_hi"]})
    return pd.DataFrame(rows, columns=["period", "scope", "scope_id", "label", "est", "lo", "hi"])


def segment_series(seg: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Group segment history rows into one series per segment.

    Segment ids are stable across snapshots (taxonomy.stabilize: a segment keeps its id while it holds the
    majority of its listings), so rows are matched by id -- a segment whose generated label drifts keeps its
    history. Rows whose id does not exist in the latest snapshot (history written before ids were stable, or a
    segment that was split or dissolved) fall back to the label: they join the latest segment with the same
    label when exactly one exists. Keys are the latest ids."""
    if seg.empty:
        return {}
    seg = seg.assign(scope_id=seg["scope_id"].astype(str))
    last = seg["period"].max()
    latest = seg[seg["period"] == last]
    current = set(latest["scope_id"])
    by_label = latest.groupby("label")["scope_id"].agg(list).to_dict() if latest["label"].notna().any() else {}
    key = seg["scope_id"].copy()
    orphan = ~key.isin(current)
    for i in seg.index[orphan]:
        ids = by_label.get(seg.at[i, "label"]) or []
        key.at[i] = ids[0] if len(ids) == 1 else None
    seg = seg.assign(_key=key).dropna(subset=["_key"])
    return {k: g.drop(columns="_key") for k, g in seg.groupby("_key")}


def market_forecast(history: pd.DataFrame) -> dict:
    """Market fit plus a fit for each segment (matched across snapshots by stable id, label as fallback)."""
    if history is None or history.empty:
        return {"market": {"status": "needs_snapshots", "periods": 0, "min_periods": config()["forecast"]["min_periods"]}, "segments": []}
    mk = history[history["scope"] == "market"]
    out = {"market": fit(mk), "segments": []}
    seg = history[history["scope"] == "segment"]
    for sid, g in segment_series(seg).items():
        f = fit(g)
        if f["status"] == "ok":
            g = g.sort_values("period")
            out["segments"].append({"label": g["label"].iat[-1], "segment_id": sid,
                                    **{k: f[k] for k in ("periods", "growth_per_month", "growth_lo", "growth_hi", "p_value", "direction")}})
    out["segments"].sort(key=lambda r: -r["growth_per_month"])
    return out

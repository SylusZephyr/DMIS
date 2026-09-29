"""Stage 7 -- Forecasting with mathematical models only.

Wraps ``dmie.engine.forecast`` (moving average, Holt exponential smoothing,
ARIMA, linear regression trend, backtest MAPE). Adds Prophet when it is
installed (optional), and translates the numbers into horizon labels:

    current / 6-month / 12-month  ->  Declining | Stable | Growing | High potential

With fewer than the minimum periods the label is "Insufficient history" --
never a guess. The launch-cohort momentum proxy is reported separately and
labelled as a proxy.
"""

from __future__ import annotations

import logging
import os
import warnings
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from dmie.engine.forecast import Forecast, build_series, forecast_series, launch_cohort

log = logging.getLogger("dip.forecasting")

GROWTH_LABELS = [(-0.05, "Declining"), (0.05, "Stable"), (0.30, "Growing")]


def label(growth: float | None) -> str:
    if growth is None or np.isnan(growth):
        return "Insufficient history"
    for bound, name in GROWTH_LABELS:
        if growth < bound:
            return name
    return "High potential"


def horizons(f: Forecast) -> dict:
    if f.status != "ok" or not f.forecast:
        return {"current": "Insufficient history", "6_month": "Insufficient history",
                "12_month": "Insufficient history", "note": f.note}
    hist = np.array([v for _, v in f.history], dtype=float)
    fc = np.array([v for _, v in f.forecast], dtype=float)
    base6 = hist[-6:].mean() if len(hist) else np.nan
    recent = (hist[-3:].mean() / hist[-6:-3].mean() - 1) if len(hist) >= 6 and hist[-6:-3].mean() > 0 else (f.trend_slope or 0) / (hist.mean() or 1)
    g6 = fc[:6].mean() / base6 - 1 if base6 and base6 > 0 else None
    return {"current": label(recent), "6_month": label(g6), "12_month": label(f.growth_rate),
            "growth_6m": None if g6 is None else round(float(g6), 4),
            "growth_12m": f.growth_rate, "confidence": f.confidence, "methods": list(f.methods)}


def prophet_forecast(series: pd.Series, periods: int = 12) -> list[tuple[str, float]] | None:
    try:
        from prophet import Prophet  # optional dependency
    except ImportError:
        return None
    df = pd.DataFrame({"ds": pd.to_datetime(series.index), "y": series.values})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = Prophet(yearly_seasonality=len(df) >= 24, weekly_seasonality=False, daily_seasonality=False).fit(df)
    fut = m.predict(m.make_future_dataframe(periods=periods, freq="MS")).tail(periods)
    return [(str(d.date()), max(0.0, float(v))) for d, v in zip(fut["ds"], fut["yhat"])]


def _series(records: pd.DataFrame, history: pd.DataFrame | None) -> dict[str, pd.Series]:
    """Same series as dmie.engine.forecast.forecast_market: dataset timestamps first, else run history."""
    series = build_series(records, "revenue")
    if not series and history is not None and len(history):
        h = history.copy()
        h["period"] = pd.to_datetime(h["period"])
        series = {str(k): g.groupby("period")["revenue"].sum(min_count=1) for k, g in h.groupby("subject")}
    return series


PARALLEL_MIN_SERIES = 8  # spawned workers cost ~1-2 s to start; below this, serial is faster


THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")


def _init_worker() -> None:
    """One BLAS/OpenMP thread per worker. Load the heavy libraries first so the
    limit covers them (spawned workers import them fresh): N workers x N threads
    oversubscribes the CPU and ran 5x *slower* than serial."""
    import statsmodels.tsa.arima.model  # noqa: F401  (loads scipy/BLAS before limiting)
    try:
        from threadpoolctl import threadpool_limits

        threadpool_limits(1)
    except ImportError:
        pass


def _one(args):
    key, s, subject = args
    return forecast_series(s, subject, "revenue")


def _workers(n: int) -> int:
    env = os.environ.get("DIP_FORECAST_WORKERS")
    cap = int(env) if env and env.isdigit() else min(8, os.cpu_count() or 1)
    return max(1, min(cap, n))


def forecast(records: pd.DataFrame, history: pd.DataFrame | None, labels: dict) -> tuple[list[Forecast], dict]:
    """Forecast the market and every segment; series run in parallel processes."""
    series = _series(records, history)
    if not series:
        fcs = [Forecast("__market__", "revenue", "insufficient_history", 0,
                        note="dataset has no timestamps and no run history yet -- upload dated snapshots "
                             "(or re-run the engine on future exports) to build a time series")]
        return fcs, {f.subject: horizons(f) for f in fcs}
    jobs = [(k, s, labels.get(k, k)) for k, s in series.items()]
    workers = _workers(len(jobs))
    fcs = None
    if workers > 1 and len(jobs) >= PARALLEL_MIN_SERIES:
        try:
            # "spawn", never "fork": the API runs pipelines in a background thread of a process that
            # already has Polars/DuckDB/Qdrant threads, and forking such a process can deadlock the child.
            ctx = multiprocessing.get_context("spawn")
            saved = {k: os.environ.get(k) for k in THREAD_ENV}
            os.environ.update({k: "1" for k in THREAD_ENV})  # inherited by the spawned workers
            try:
                with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, mp_context=ctx) as pool:
                    fcs = list(pool.map(_one, jobs, chunksize=max(1, len(jobs) // (workers * 4))))
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        except Exception as exc:  # e.g. a platform without process pools -- same results, serially
            log.warning("parallel forecasting unavailable (%s); running serially", exc)
    if fcs is None:
        fcs = [_one(j) for j in jobs]
    return fcs, {f.subject: horizons(f) for f in fcs}


__all__ = ["forecast", "horizons", "label", "launch_cohort", "prophet_forecast"]

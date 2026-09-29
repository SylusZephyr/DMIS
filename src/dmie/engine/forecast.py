"""Trend Forecast Engine (Module 7) -- mathematics only, no AI.

Methods on a regular (monthly by default) series:

* moving average
* exponential smoothing (Holt's linear trend; simple ES for short series)
* ARIMA (small order grid selected by AIC)
* linear trend slope and period growth rate

The ensemble forecast is the mean of the methods that fitted. Confidence
comes from the series length and a one-step-ahead backtest (MAPE) --
never asserted. Fewer than ``min_periods`` observations returns
``status="insufficient_history"`` with no numbers invented.

Time series come from (a) datasets with a timestamp column, or
(b) the engine's own history table, which accumulates one point per
market per run -- so uploading a fresh export every month builds the
series automatically.

A separate *launch-cohort* indicator (share of revenue from listings
launched in the last 12 months) is reported when launch dates exist; it
is a momentum proxy, labelled as such, not a forecast.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dmie.engine.config import section


@dataclass
class Forecast:
    subject: str
    metric: str
    status: str                                   # ok | insufficient_history
    periods_observed: int
    history: list[tuple[str, float]] = field(default_factory=list)
    forecast: list[tuple[str, float]] = field(default_factory=list)
    methods: dict = field(default_factory=dict)   # method -> next-horizon total
    current_annual: float | None = None
    forecast_annual: float | None = None
    growth_rate: float | None = None
    trend_slope: float | None = None              # units per period
    backtest_mape: float | None = None
    confidence: str | None = None                 # LOW | MEDIUM | HIGH
    note: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _moving_average(y: np.ndarray, h: int, window: int) -> np.ndarray:
    return np.repeat(y[-window:].mean(), h)


def _linear(y: np.ndarray, h: int) -> tuple[np.ndarray, float]:
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    fx = np.arange(len(y), len(y) + h)
    return np.maximum(intercept + slope * fx, 0), float(slope)


def _holt(y: np.ndarray, h: int) -> np.ndarray | None:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing, SimpleExpSmoothing

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            if len(y) >= 4:
                fit = ExponentialSmoothing(y, trend="add", damped_trend=True, initialization_method="estimated").fit()
            else:
                fit = SimpleExpSmoothing(y, initialization_method="estimated").fit()
            return np.maximum(fit.forecast(h), 0)
        except Exception:
            return None


ARIMA_ORDERS = ((0, 1, 1), (1, 1, 0), (1, 1, 1), (0, 1, 0), (1, 0, 0))


def _arima(y: np.ndarray, h: int, orders=ARIMA_ORDERS) -> tuple[np.ndarray | None, tuple | None]:
    """Best-AIC ARIMA over ``orders``; returns (forecast, chosen order)."""
    if len(y) < 6:
        return None, None
    from statsmodels.tsa.arima.model import ARIMA

    best, best_order = None, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for order in orders:
            try:
                fit = ARIMA(y, order=order).fit()
                if best is None or fit.aic < best.aic:
                    best, best_order = fit, order
            except Exception:
                continue
    return (None, None) if best is None else (np.maximum(best.forecast(h), 0), best_order)


def _ensemble(y: np.ndarray, h: int, window: int, arima_orders=ARIMA_ORDERS) -> tuple[np.ndarray, dict, float, tuple | None]:
    preds = {"moving_average": _moving_average(y, h, min(window, len(y)))}
    lin, slope = _linear(y, h)
    preds["linear_trend"] = lin
    holt = _holt(y, h)
    if holt is not None:
        preds["exponential_smoothing"] = holt
    ar, order = _arima(y, h, arima_orders)
    if ar is not None:
        preds["arima"] = ar
    return np.mean(np.vstack(list(preds.values())), axis=0), preds, slope, order


def forecast_series(series: pd.Series, subject: str, metric: str) -> Forecast:
    """``series``: values indexed by period (DatetimeIndex or sortable)."""
    cfg = section("forecast")
    h = cfg.get("horizon_periods", 12)
    min_p = cfg.get("min_periods", 3)
    window = cfg.get("moving_average_window", 3)
    s = series.dropna().sort_index()
    hist = [(str(getattr(k, "date", lambda: k)()), float(v)) for k, v in s.items()]
    if len(s) < min_p:
        return Forecast(subject, metric, "insufficient_history", len(s), hist,
                        note=f"need >= {min_p} time periods to forecast; have {len(s)}")
    y = s.to_numpy(dtype=float)
    fc, preds, slope, order = _ensemble(y, h, window)

    mape = None
    if len(y) >= min_p + 1:
        errs = []
        # the backtest refits reuse the ARIMA order chosen on the full series
        # (re-searching it at every cut repeated ~80% of the work)
        bt_orders = (order,) if order else ARIMA_ORDERS
        for cut in range(max(min_p, len(y) - 3), len(y)):
            p1, _, _, _ = _ensemble(y[:cut], 1, window, bt_orders)
            if y[cut] > 0:
                errs.append(abs(p1[0] - y[cut]) / y[cut])
        mape = float(np.mean(errs)) if errs else None

    last_n = min(len(y), h)
    current_annual = float(y[-last_n:].sum() * (h / last_n))
    forecast_annual = float(fc.sum())
    growth = (forecast_annual / current_annual - 1) if current_annual > 0 else None
    if len(y) >= 12 and mape is not None and mape < 0.15:
        conf = "HIGH"
    elif len(y) >= 6 and (mape is None or mape < 0.35):
        conf = "MEDIUM"
    else:
        conf = "LOW"
    if isinstance(s.index, pd.DatetimeIndex):
        freq = pd.infer_freq(s.index) or "MS"
        future = pd.date_range(s.index[-1], periods=h + 1, freq=freq)[1:]
        labels = [str(d.date()) for d in future]
    else:
        labels = [f"t+{i + 1}" for i in range(h)]
    return Forecast(
        subject, metric, "ok", len(y), hist, list(zip(labels, [round(float(v), 2) for v in fc])),
        {k: round(float(v.sum()), 2) for k, v in preds.items()},
        round(current_annual, 2), round(forecast_annual, 2),
        None if growth is None else round(growth, 4), round(slope, 4),
        None if mape is None else round(mape, 4), conf,
        "ensemble of " + ", ".join(preds),
    )


def build_series(records: pd.DataFrame, value: str = "revenue", by: str | None = "segment_id", freq: str = "MS") -> dict[str, pd.Series]:
    """Aggregate time-stamped records into per-group series (sum per period)."""
    df = records.dropna(subset=["timestamp"])
    if df.empty or df[value].notna().sum() == 0:
        return {}
    df = df.assign(period=pd.to_datetime(df["timestamp"]).dt.to_period(freq[0]).dt.to_timestamp())
    out = {"__market__": df.groupby("period")[value].sum(min_count=1)}
    if by and by in df:
        for key, g in df.groupby(by):
            out[str(key)] = g.groupby("period")[value].sum(min_count=1)
    return out


def forecast_market(records: pd.DataFrame, history: pd.DataFrame | None = None, labels: dict | None = None) -> list[Forecast]:
    """Forecast revenue for the whole market and every segment.

    ``history``: optional rows (period, subject, revenue) from the engine's
    run-history table; used when the dataset itself has no timestamps.
    """
    labels = labels or {}
    series = build_series(records, "revenue")
    if not series and history is not None and len(history):
        h = history.copy()
        h["period"] = pd.to_datetime(h["period"])
        series = {str(k): g.groupby("period")["revenue"].sum(min_count=1) for k, g in h.groupby("subject")}
    if not series:
        return [Forecast("__market__", "revenue", "insufficient_history", 0,
                         note="dataset has no timestamps and no run history yet -- upload dated snapshots "
                              "(or re-run the engine on future exports) to build a time series")]
    return [forecast_series(s, labels.get(k, k), "revenue") for k, s in series.items()]


def launch_cohort(records: pd.DataFrame, as_of: pd.Timestamp | None = None) -> dict:
    """Momentum proxy from listing launch dates (not a forecast)."""
    df = records.dropna(subset=["launch_date"])
    if len(df) < 5:
        return {"status": "no_launch_dates"}
    as_of = as_of or pd.Timestamp.now().normalize()
    age_days = (as_of - pd.to_datetime(df["launch_date"])).dt.days
    recent = age_days <= 365
    rev = df["revenue"].fillna(0)
    total = rev.sum()
    return {
        "status": "ok",
        "listings_with_launch_date": int(len(df)),
        "new_listing_share": round(float(recent.mean()), 4),
        "new_listing_revenue_share": round(float(rev[recent].sum() / total), 4) if total > 0 else None,
        "median_listing_age_years": round(float(age_days.median() / 365.25), 2),
        "note": "share of listings/revenue launched in the last 12 months -- a momentum proxy, not a forecast",
    }

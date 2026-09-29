"""Parallel forecasting must return exactly what the serial engine returns."""

import numpy as np
import pandas as pd

from dmie.engine.forecast import forecast_market
from dip.pipeline.forecasting import forecast


def test_parallel_equals_serial():
    rng = np.random.default_rng(0)
    rows = [{"id": f"x{s}", "timestamp": d, "revenue": 1000 * (1.02 ** m) * rng.uniform(0.9, 1.1), "sales": 1.0,
             "segment_id": f"S{s}"}
            for s in range(6) for m, d in enumerate(pd.date_range("2025-01-01", periods=8, freq="MS"))]
    rec = pd.DataFrame(rows)
    serial = forecast_market(rec, None, {})
    parallel, horizons = forecast(rec, None, {})
    assert [f.subject for f in serial] == [f.subject for f in parallel]
    for a, b in zip(serial, parallel):
        assert np.allclose([v for _, v in a.forecast], [v for _, v in b.forecast])
        assert a.backtest_mape == b.backtest_mape and a.confidence == b.confidence
    assert horizons["__market__"]["12_month"] in {"Stable", "Growing", "High potential"}


def test_no_history_is_honest():
    fcs, horizons = forecast(pd.DataFrame(columns=["id", "timestamp", "revenue", "sales", "segment_id"]), None, {})
    assert fcs[0].status == "insufficient_history"
    assert horizons["__market__"]["12_month"] == "Insufficient history"

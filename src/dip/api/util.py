"""JSON helpers: NaN -> null, numpy -> python, JSON-text columns -> objects."""

from __future__ import annotations

import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return clean(o.to_dict("records"))
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (datetime, date, pd.Timestamp)):
        return o.isoformat()
    if o is pd.NaT:
        return None
    if isinstance(o, str) and o[:1] in "[{" and o[-1:] in "]}":
        try:
            return clean(json.loads(o))
        except ValueError:
            return o
    return o

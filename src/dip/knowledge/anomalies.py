"""Data anomaly detection (spec 154): values that are probably wrong, or changed suspiciously, are flagged for review.

Nothing is dropped or corrected: every flag is a row in the ``anomalies`` table (listing, code, severity,
value, expected, detail) shown in the review queue. Checks, all deterministic:

* ``invalid_value``      price <= 0, rating outside 0-5, negative sales or revenue
* ``price_outlier``      a listing's price far from the other listings of the same canonical product: robust
                         z = |log price - median log price| / (1.4826 x MAD) above ``price_z`` (products with 3+
                         priced listings; with MAD 0 the ratio to the median must exceed ``price_ratio``)
* ``revenue_mismatch``   the source's revenue differs from price x units by more than ``revenue_tolerance``
                         (relative), when both are present
* ``price_jump``         the price moved by more than ``jump_ratio`` (either direction) since the previous
                         observation of the same listing in ``observation_history``
* ``sales_jump``         sales moved by more than ``sales_jump_ratio`` since the previous observation

Settings: ``anomalies`` in config/platform/knowledge.yaml.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dip.knowledge import config

COLUMNS = ["entity_id", "product_id", "code", "severity", "value", "expected", "detail"]


def _cfg() -> dict:
    return config()["anomalies"]


def _num(L: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(L[col], errors="coerce") if col in L else pd.Series(np.nan, index=L.index)


def _row(eid, pid, code, sev, value, expected, detail) -> dict:
    return {"entity_id": str(eid), "product_id": None if pid is None or (isinstance(pid, float) and np.isnan(pid)) else str(pid),
            "code": code, "severity": sev, "value": None if value is None else float(value),
            "expected": None if expected is None else float(expected), "detail": detail}


def detect(listings: pd.DataFrame, history: pd.DataFrame | None = None, dataset_id: str | None = None) -> pd.DataFrame:
    c = _cfg()
    L = listings
    ids = L["id"] if "id" in L else pd.Series(L.index.astype(str), index=L.index)
    pid = L["product_id"] if "product_id" in L else pd.Series([None] * len(L), index=L.index)
    price, rating, sales, rev = _num(L, "price"), _num(L, "rating"), _num(L, "sales"), _num(L, "revenue")
    out: list[dict] = []

    # invalid values
    for s, cond, what in ((price, price <= 0, "price <= 0"), (rating, (rating < 0) | (rating > 5), "rating outside 0-5"),
                          (sales, sales < 0, "negative sales"), (rev, rev < 0, "negative revenue")):
        for i in L.index[cond.fillna(False)]:
            out.append(_row(ids[i], pid[i], "invalid_value", "high", s[i], None, what))

    # price outliers within a canonical product
    lp = np.log(price.where(price > 0))
    frame = pd.DataFrame({"pid": pid, "lp": lp}).dropna()
    for p, g in frame.groupby("pid"):
        if len(g) < 3:
            continue
        med = g["lp"].median()
        mad = (g["lp"] - med).abs().median() * 1.4826
        for i, v in g["lp"].items():
            dev = abs(v - med)
            bad = dev / mad > float(c["price_z"]) if mad > 0 else np.exp(dev) > float(c["price_ratio"])
            if bad and np.exp(dev) > float(c["price_ratio"]):          # never flag a small absolute spread
                out.append(_row(ids[i], p, "price_outlier", "medium", price[i], round(float(np.exp(med)), 2),
                                f"price {price[i]:.2f} vs product median {np.exp(med):.2f} ({len(g)} listings)"))

    # source revenue vs price x units
    both = price.gt(0) & sales.gt(0) & rev.gt(0)
    exp = price * sales
    rel = ((rev - exp).abs() / exp).where(both)
    for i in L.index[(rel > float(c["revenue_tolerance"])).fillna(False)]:
        out.append(_row(ids[i], pid[i], "revenue_mismatch", "low", rev[i], round(float(exp[i]), 2),
                        f"source revenue {rev[i]:,.0f} vs price x units {exp[i]:,.0f} ({rel[i]:.0%} apart)"))

    # changes since the previous observation
    if history is not None and len(history):
        h = history[(history["entity_type"] == "listing") & history["metric"].isin(["price", "sales"])]
        if dataset_id is not None and "dataset_id" in h:
            h = h[h["dataset_id"] != dataset_id]
        if len(h):
            h = h.sort_values("observed_at").groupby(["entity_id", "metric"]).tail(1).set_index(["entity_id", "metric"])["value"]
            h = pd.to_numeric(h, errors="coerce")        # the observation store holds text values too; compare numbers only
            for metric, cur, ratio, code in (("price", price, c["jump_ratio"], "price_jump"), ("sales", sales, c["sales_jump_ratio"], "sales_jump")):
                for i in L.index:
                    prev = h.get((str(ids[i]), metric))
                    v = cur[i]
                    if prev is None or pd.isna(prev) or pd.isna(v) or prev <= 0 or v <= 0:
                        continue
                    r = max(v / prev, prev / v)
                    if r > float(ratio):
                        out.append(_row(ids[i], pid[i], code, "medium", v, float(prev),
                                        f"{metric} {prev:,.2f} -> {v:,.2f} since the previous snapshot (x{v / prev:.2f})"))
    return pd.DataFrame(out, columns=COLUMNS)

"""Predictive market intelligence: trend detection per market and segment.

Signals (each only when the data supports it -- otherwise reported as unavailable):

* **demand_history** -- revenue growth across snapshots of the same listings
  (log-linear slope per month; the ensemble forecast's growth when it ran)
* **listing_growth** -- listings launched in the last 12 months vs the 12 before
* **new_entrant_share** -- revenue share of listings launched in the last 12 months
* **review_velocity** -- growth of review counts across snapshots
* **price_movement** -- median price change of the same listings (reported, and used
  for the price-pressure flag; not a direction signal)
* **seasonality** -- strength of the 12-month cycle, only with >= 24 monthly periods

Direction = weighted mean of available signals (-1..1) -> Growing / Stable /
Declining / Emerging / Mature -- only with at least ``min_periods_for_label`` dated snapshots (claims
policy). With fewer, the trend is "Insufficient evidence" and the launch-date signals are reported as
``launch_activity`` (Rising / Steady / Falling), which says how many listings arrived, not whether demand grew. "Declining" needs demand evidence (snapshots or review
counts); launch-date signals alone that point down are reported as "Mature". Confidence = evidence base (number of periods) x agreement
of the signals x sales coverage. Expected 12-month growth is given only when a
demand history exists; a single snapshot yields a direction, never a number.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dip.settings import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config" / "platform" / "trends.yaml"


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))


def _monthly_growth(periods: pd.Series, values: pd.Series) -> float | None:
    """Log-linear growth per month from (period, value) points; None with < 2 positive points."""
    d = pd.DataFrame({"t": pd.to_datetime(np.asarray(periods)), "v": np.asarray(values, dtype=float)}).dropna()
    d = d[d["v"] > 0]
    if d["t"].nunique() < 2:
        return None
    t0 = d["t"].min()
    # calendar months (monthly snapshots are 28-31 days apart), plus any day remainder
    months = (d["t"].dt.year - t0.year) * 12 + (d["t"].dt.month - t0.month) + (d["t"].dt.day - t0.day) / 30.44
    if months.max() <= 0:
        return None
    slope = np.polyfit(months, np.log(d["v"]), 1)[0]
    return float(np.expm1(slope))


def _seasonality(periods: pd.Series, values: pd.Series, min_months: int) -> dict:
    s = pd.Series(values.to_numpy(float), index=pd.to_datetime(periods)).resample("MS").sum()
    if len(s) < min_months:
        return {"status": "not_determinable", "note": f"needs >= {min_months} monthly periods; have {len(s)}"}
    detr = s / s.rolling(12, center=True, min_periods=6).mean()
    ac = float(pd.Series(detr.dropna().to_numpy()).autocorr(lag=12)) if detr.notna().sum() > 13 else np.nan
    by_month = detr.groupby(detr.index.month).mean()
    return {"status": "ok", "strength": None if np.isnan(ac) else round(ac, 3), "seasonal": bool(ac >= 0.5) if not np.isnan(ac) else False,
            "peak_month": int(by_month.idxmax()) if len(by_month) else None,
            "trough_month": int(by_month.idxmin()) if len(by_month) else None}


def _scope_signals(hist: pd.DataFrame, cur: pd.DataFrame, as_of: pd.Timestamp, forecast_growth: float | None) -> dict:
    cfg = config()
    out: dict = {}
    # demand history: revenue per period over listings present in the scope
    rev = hist["revenue"].where(hist["revenue"].notna(), hist["price"] * hist["sales"])
    per = pd.DataFrame({"period": hist["period"], "rev": rev}).groupby("period")["rev"].sum(min_count=1).dropna()
    n_periods = int(hist["period"].nunique())
    g = forecast_growth if forecast_growth is not None else None
    mg = _monthly_growth(pd.Series(per.index), per) if len(per) >= 2 else None
    if mg is not None:
        out["demand_history"] = {"value": round(mg, 4), "unit": "per month", "periods": int(len(per)),
                                 "direction": float(np.tanh(mg * 10)),
                                 "annual_growth": round(float((1 + mg) ** 12 - 1), 4) if g is None else round(float(g), 4)}
    # launch-date signals from current listings
    ld = pd.to_datetime(cur["launch_date"], errors="coerce") if "launch_date" in cur else pd.Series(dtype="datetime64[ns]")
    if ld.notna().sum() >= cfg["min_listings"]:
        age = (as_of - ld).dt.days
        new12 = int((age.between(0, 365)).sum())
        prev12 = int((age.between(366, 730)).sum())
        out["listing_growth"] = {"value": new12, "previous_12m": prev12, "unit": "listings launched in last 12 months",
                                 "direction": float(np.tanh(np.log((new12 + 1) / (prev12 + 1))))}
        r = cur["revenue"].where(cur["revenue"].notna(), cur["price"] * cur["sales"]).fillna(0)
        if r.sum() > 0:
            share = float(r[age.between(0, 365).to_numpy()].sum() / r.sum())
            neutral = cfg["new_entrant_neutral"]
            out["new_entrant_share"] = {"value": round(share, 4), "unit": "revenue share of listings < 12 months old",
                                        "direction": float(np.clip((share - neutral) / max(neutral, 1e-9), -1, 1))}
    # review velocity and price movement across snapshots (same listings)
    if n_periods >= 2:
        first, last = hist["period"].min(), hist["period"].max()
        a = hist[hist["period"] == first].set_index("id")
        z = hist[hist["period"] == last].set_index("id")
        both = a.index.intersection(z.index)
        months = max((last - first).days / 30.44, 1e-9)
        if len(both) >= cfg["min_listings"]:
            ra, rz = pd.to_numeric(a.loc[both, "reviews"], errors="coerce"), pd.to_numeric(z.loc[both, "reviews"], errors="coerce")
            ok = ra.notna() & rz.notna() & (ra > 0)
            if ok.sum() >= cfg["min_listings"]:
                vel = float(((rz[ok] - ra[ok]) / months).sum())
                growth = float(rz[ok].sum() / ra[ok].sum() - 1)
                out["review_velocity"] = {"value": round(vel, 1), "unit": "new reviews per month", "growth": round(growth, 4),
                                          "direction": float(np.tanh(growth / months * 10))}
            pa, pz = a.loc[both, "price"].astype(float), z.loc[both, "price"].astype(float)
            okp = pa.notna() & pz.notna() & (pa > 0)
            if okp.sum() >= cfg["min_listings"]:
                chg = float((pz[okp] / pa[okp] - 1).median())
                out["price_movement"] = {"value": round(chg, 4), "unit": f"median price change over {months:.0f} months",
                                         "listings": int(okp.sum())}
    out["_periods"] = n_periods
    return out


def _combine(signals: dict, sales_coverage: float | None, scope_products: int, total_products: int) -> dict:
    cfg = config()
    w = cfg["signal_weights"]
    used = {k: v for k, v in signals.items() if k in w and "direction" in v}
    if not used:
        return {"trend": "Insufficient evidence", "direction": None, "confidence": None, "expected_growth_12m": None,
                "launch_activity": None, "launch_direction": None}
    d = sum(w[k] * v["direction"] for k, v in used.items()) / sum(w[k] for k in used)
    lab = cfg["labels"]
    trend = "Growing" if d >= lab["growing"] else "Declining" if d <= lab["declining"] else "Stable"
    demand_evidence = "demand_history" in used or "review_velocity" in used
    if trend == "Declining" and not demand_evidence:
        # launch-date signals alone show new entrants failing to gain share -- a mature market,
        # not proof that demand is falling
        trend = "Mature"
    nes = signals.get("new_entrant_share", {}).get("value")
    if nes is not None and nes >= lab["emerging_share"] and "demand_history" not in used and scope_products <= max(5, 0.1 * total_products):
        trend = "Emerging"
    n = signals.get("_periods", 1)
    need = int(cfg.get("min_periods_for_label", 1))
    if n < need:
        # claims policy: one snapshot (or two) cannot show a trend. Launch dates still say how many listings
        # arrived recently -- reported as launch activity, not as market growth or decline.
        activity = "Rising" if d >= lab["growing"] else "Falling" if d <= lab["declining"] else "Steady"
        return {"trend": "Insufficient evidence", "direction": None, "confidence": None, "expected_growth_12m": None,
                "expected_growth_basis": f"a trend needs {need} dated snapshots; this market has {n}",
                "launch_activity": activity, "launch_direction": round(float(d), 3)}
    base = 0.0
    for p, c in cfg["confidence_by_periods"]:
        if n >= p:
            base = c
    agree = np.mean([np.sign(v["direction"]) == np.sign(d) or abs(v["direction"]) < 0.1 for v in used.values()])
    cov = 0.6 + 0.4 * float(sales_coverage) if sales_coverage is not None and not pd.isna(sales_coverage) else 0.8
    conf = base * (0.5 + 0.5 * agree) * cov
    exp = signals.get("demand_history", {}).get("annual_growth")
    return {"trend": trend, "direction": round(float(d), 3), "confidence": round(float(conf) * 100),
            "expected_growth_12m": exp,
            "expected_growth_basis": "demand history" if exp is not None else "not estimable from a single snapshot",
            "launch_activity": None, "launch_direction": None}


def _evidence(signals: dict) -> list[str]:
    ev = []
    if "demand_history" in signals:
        s = signals["demand_history"]
        ev.append(f"revenue {s['value']:+.1%}/month across {s['periods']} snapshots")
    if "listing_growth" in signals:
        s = signals["listing_growth"]
        ev.append(f"{s['value']} listings launched in the last 12 months vs {s['previous_12m']} the year before")
    if "new_entrant_share" in signals:
        ev.append(f"{signals['new_entrant_share']['value']:.0%} of revenue from listings under 12 months old")
    if "review_velocity" in signals:
        s = signals["review_velocity"]
        ev.append(f"{s['value']:,.0f} new reviews/month ({s['growth']:+.0%})")
    if "price_movement" in signals:
        ev.append(f"prices {signals['price_movement']['value']:+.1%} ({signals['price_movement']['unit']})")
    return ev


def detect(history: pd.DataFrame, listings: pd.DataFrame, segments: pd.DataFrame, as_of: pd.Timestamp,
           forecast_growth: dict | None = None) -> pd.DataFrame:
    """One row per scope ('__market__' and every segment_id) with trend, confidence,
    expected 12-month growth, signals (JSON) and evidence (JSON list)."""
    forecast_growth = forecast_growth or {}
    seg_of = dict(zip(listings["id"], listings["segment_id"]))
    h = history.assign(segment_id=history["id"].map(seg_of))
    cov = dict(zip(segments["segment_id"], segments.get("sales_coverage", pd.Series(dtype=float)))) if len(segments) else {}
    n_products = segments["products"].sum() if "products" in segments and len(segments) else len(listings)
    rows = []
    scopes = [("__market__", h, listings)] + [(sid, h[h["segment_id"] == sid], listings[listings["segment_id"] == sid])
                                              for sid in segments["segment_id"]]
    total_cov = listings["sales"].notna().mean() if len(listings) else None
    seas_all = _seasonality(h["period"], h["revenue"].where(h["revenue"].notna(), h["price"] * h["sales"]).fillna(0),
                            config()["seasonality_min_months"])
    for sid, hh, cc in scopes:
        sig = _scope_signals(hh, cc, as_of, forecast_growth.get(sid))
        scope_products = int(segments.loc[segments["segment_id"] == sid, "products"].sum()) if sid != "__market__" and "products" in segments else n_products
        res = _combine(sig, cov.get(sid, total_cov), scope_products, n_products)
        seas = seas_all if sid == "__market__" else (
            _seasonality(hh["period"], hh["revenue"].where(hh["revenue"].notna(), hh["price"] * hh["sales"]).fillna(0),
                         config()["seasonality_min_months"]) if seas_all["status"] == "ok" else seas_all)
        pm = sig.get("price_movement", {}).get("value")
        rows.append({"scope": sid, **res, "seasonality": json.dumps(seas),
                     "price_pressure": bool(pm is not None and pm <= -0.05),
                     "periods": sig.get("_periods", 1),
                     "signals": json.dumps({k: v for k, v in sig.items() if not k.startswith("_")}),
                     "evidence": json.dumps(_evidence(sig))})
    return pd.DataFrame(rows)

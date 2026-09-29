"""Statistical significance for change events (Phase 8).

A change between two snapshots becomes an alert only when it is larger than the noise of the estimates:

    z = (log after - log before) / sqrt(se_before^2 + se_after^2),   se = (log hi - log lo) / (2 * z_0.975)

(log scale: revenue and shares are positive and their intervals are asymmetric). Two-sided p-values of all
tested changes of one run are Benjamini-Hochberg adjusted; q <= ``alerts.fdr_q`` is significant.
Events that were tested and are not significant are kept as ``info`` (visible in the event log, never
routed as alerts) with the test in their payload; events without estimates to test keep their rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from dip.metrics import config
from dip.metrics.gaps import _bh


def v3_state(market: str) -> dict:
    """The v3 estimates of a market with their intervals (read before a run overwrites them)."""
    from dip.storage import business as b
    from dip.storage import lake

    st: dict = {}
    with b.session() as s:
        m = s.get(b.Market, market)
        st["revenue"] = ((m.summary or {}).get("metrics_v3") or {}).get("revenue_month") if m else None
    if lake.has_curated("brands", market):
        st["brands"] = lake.read_curated("brands", market, columns=["brand", "share_est", "share_lo", "share_hi"])
    if lake.has_curated("segments", market) and "revenue_lo" in lake.curated_columns("segments", market):
        st["segments"] = lake.read_curated("segments", market, columns=["segment_label", "revenue_est", "revenue_lo", "revenue_hi"])
    return st


def log_test(a: float, a_lo: float, a_hi: float, z: float, z_lo: float, z_hi: float, level: float | None = None) -> dict | None:
    level = level or config()["engine"]["interval"]
    vals = [a, a_lo, a_hi, z, z_lo, z_hi]
    if any(v is None or not np.isfinite(v) or v <= 0 for v in vals):
        return None
    c = stats.norm.ppf(0.5 + level / 2)
    se = np.hypot((np.log(a_hi) - np.log(a_lo)) / (2 * c), (np.log(z_hi) - np.log(z_lo)) / (2 * c))
    diff = np.log(z) - np.log(a)
    if se <= 0:                          # both values known exactly (exact sales): a difference is certain
        return {"z": None, "p_value": 0.0 if abs(diff) > 1e-12 else 1.0, "ratio": round(float(z / a), 4),
                "before": [a, a_lo, a_hi], "after": [z, z_lo, z_hi], "exact": True}
    zz = diff / se
    return {"z": round(float(zz), 3), "p_value": float(2 * stats.norm.sf(abs(zz))), "ratio": round(float(z / a), 4),
            "before": [a, a_lo, a_hi], "after": [z, z_lo, z_hi]}


def after_state(summary: dict, brands: pd.DataFrame | None, segments: pd.DataFrame | None) -> dict:
    return {"revenue": (summary or {}).get("revenue_month"), "brands": brands,
            "segments": segments[["segment_label", "revenue_est", "revenue_lo", "revenue_hi"]]
            if segments is not None and "revenue_lo" in segments else None}


def apply(changes: list[dict], before: dict, after: dict) -> list[dict]:
    """Attach tests to size and share events, add significant segment demand shifts, BH-adjust, and demote
    non-significant tested events to info."""
    cfg = config()["alerts"]
    tested: list[tuple[dict, dict]] = []
    rb, ra = before.get("revenue") or {}, after.get("revenue") or {}
    if rb and ra:
        t = log_test(rb.get("estimate"), rb.get("low"), rb.get("high"), ra.get("estimate"), ra.get("low"), ra.get("high"))
        if t:
            ev = next((c for c in changes if c["kind"] == "market.size_change" and c["payload"].get("metric") == "monthly_revenue"), None)
            if ev is None:
                ev = {"kind": "market.size_change", "subject": f"estimated revenue {t['ratio'] - 1:+.0%}", "severity": "notice",
                      "payload": {"metric": "revenue_est"}}
                changes.append(ev)
            tested.append((ev, t))
            for c in changes:                  # the observed units event shares the market's test
                if c is not ev and c["kind"] == "market.size_change":
                    tested.append((c, t))
    bb, ba = before.get("brands"), after.get("brands")
    if bb is not None and ba is not None and len(bb) and len(ba):
        prev = bb.set_index("brand")
        cur = ba.set_index("brand")
        for c in changes:
            if c["kind"] == "competitor.share_change" and c["subject"] in prev.index and c["subject"] in cur.index:
                p, q = prev.loc[c["subject"]], cur.loc[c["subject"]]
                t = log_test(p["share_est"], p["share_lo"], p["share_hi"], q["share_est"], q["share_lo"], q["share_hi"])
                if t:
                    tested.append((c, t))
    sb, sa = before.get("segments"), after.get("segments")
    if sb is not None and sa is not None and len(sb) and len(sa):
        prev = sb.drop_duplicates("segment_label").set_index("segment_label")
        for r in sa.to_dict("records"):
            if r["segment_label"] not in prev.index:
                continue
            p = prev.loc[r["segment_label"]]
            t = log_test(p["revenue_est"], p["revenue_lo"], p["revenue_hi"], r["revenue_est"], r["revenue_lo"], r["revenue_hi"])
            if t and abs(t["ratio"] - 1) >= cfg["min_segment_change"]:
                ev = {"kind": "segment.demand_change", "subject": f"{r['segment_label']} {t['ratio'] - 1:+.0%}", "severity": "notice",
                      "payload": {"segment_label": r["segment_label"]}}
                changes.append(ev)
                tested.append((ev, t))
    if tested:
        q = _bh(np.array([t["p_value"] for _, t in tested]))
        for (ev, t), qv in zip(tested, q):
            sig = bool(qv <= cfg["fdr_q"])
            prev_q = ev["payload"].get("significance", {}).get("q_value")
            if prev_q is not None and prev_q <= qv:        # an event tested twice keeps its stronger test
                continue
            ev["payload"]["significance"] = {**t, "q_value": round(float(qv), 5), "significant": sig,
                                             "method": "log-scale z-test on 95% intervals, BH over the run"}
            if not sig:
                ev["severity"] = "info"
            elif ev["severity"] == "notice" and abs(t["ratio"] - 1) >= cfg["important_change"]:
                ev["severity"] = "important"
    # untested segment events that were not significant are dropped (they were created only to be tested)
    return [c for c in changes if not (c["kind"] == "segment.demand_change" and c["severity"] == "info")]

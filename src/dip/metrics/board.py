"""Opportunity board (post-Phase 8): one table that answers "what should we sell?" across markets.

Each row is the product concept of one of a market's top sub-categories (by opportunity index): the gaps
engine's recommendation (M10.2: significant feature gaps and best price band) where the sub-category is large
enough to test, else the sub-category's typical product at its median price (stated in ``basis``). Joined
with everything the platform knows about it:

* opportunity index, level and evidence coverage of the sub-category (M3), the market's evidence grade;
* a launch simulation of the concept at the middle of its recommended price range (M12: posterior-predictive
  units / revenue, P(revenue >= target), profit only when the source has genuine unit costs) and its risks.
  The concept is simulated as a competitive entrant, at the sub-category's rating bar (the median rating of
  its top sellers), and the assumed rating and its basis are reported;
* the sub-category's growth trend (M15) when enough snapshots exist;
* competition: HHI, the leading brand's share interval and P(#1) (M16.1), entrant success (M4);
* launch momentum (M11.3).

Each row also carries the knowledge layer's explainable opportunity (``engine``: score, evidence coverage,
status, confidence, rank, patterns, risks and numbered reasons; spec 35-36, 155-156) for the same segment, so
the board, the market pages and the analyst read one opportunity engine. ``engine`` is null when the market
was processed before the knowledge layer existed. Nothing is re-scored: rows are ordered by the engine's
opportunity score (unscored rows last), the older index only breaking ties; every other column is sortable
in the UI. Results are cached per market (in memory and under data/cache/board, so restarts and workers
share them) until the market is re-processed or a platform setting changes; processing warms the cache. Settings: ``board`` in
config/platform/metrics.yaml.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from dip.metrics import config
from dip.settings import PROJECT_ROOT, get_settings

_CACHE: dict[str, tuple[str, list[dict]]] = {}
_LOCK = threading.Lock()


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _list(v) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return [v]
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    return list(v)


def concept_price(price_range, comparables) -> float | None:
    """Middle of the recommended price range on a log scale (prices are compared by ratio); else the
    median price of the comparables."""
    pr = _list(price_range)
    if len(pr) == 2 and _f(pr[0]) and _f(pr[1]) and pr[0] > 0 and pr[1] > 0:
        return round(float(np.sqrt(pr[0] * pr[1])), 2)
    ps = [_f(c.get("price")) for c in _list(comparables) if isinstance(c, dict)]
    ps = [p for p in ps if p and p > 0]
    return round(float(np.median(ps)), 2) if ps else None


def engine_rows(market: str) -> dict[str, dict]:
    """The knowledge layer's segment opportunities of one market, keyed by segment id."""
    from dip.storage import lake

    if not lake.has_curated("opportunities", market):
        return {}
    o = lake.read_curated("opportunities", market)
    if not len(o) or "scope" not in o:
        return {}
    out = {}
    for r in o[o["scope"] == "segment"].to_dict("records"):
        out[str(r["scope_id"])] = {"score": _f(r.get("opportunity_score")), "raw_score": _f(r.get("raw_score")),
                                   "coverage": _f(r.get("evidence_coverage")), "status": r.get("status"),
                                   "confidence": r.get("confidence"), "rank": _f(r.get("rank")),
                                   "patterns": _list(r.get("patterns")), "risks": _list(r.get("risks")),
                                   "reasons": _list(r.get("reasons")), "gated_by": _list(r.get("gated_by"))}
    return out


def market_rows(market: str, summary: dict | None) -> list[dict]:
    from dip.metrics import launch as launch_mod
    from dip.metrics.forecast import market_forecast
    from dip.storage import lake

    if not lake.has_curated("segments", market):
        return []
    segs = lake.read_curated("segments", market).set_index("segment_id")
    recs = lake.read_curated("recommendations", market) if lake.has_curated("recommendations", market) else pd.DataFrame()
    rec_of = {r["segment_id"]: r for r in recs.to_dict("records")} if len(recs) else {}
    med_price = {}
    if lake.has_curated("listings", market):
        lp = lake.read_curated("listings", market, columns=["segment_id", "price"])
        med_price = pd.to_numeric(lp["price"], errors="coerce").groupby(lp["segment_id"]).median().to_dict()
    sb = lake.read_curated("segment_brands", market) if lake.has_curated("segment_brands", market) else pd.DataFrame()
    mom = lake.read_curated("momentum", market) if lake.has_curated("momentum", market) else pd.DataFrame()
    fc = market_forecast(lake.read_curated("revenue_history", market))
    growth_by_id = {s["segment_id"]: s for s in fc.get("segments", [])}      # stable segment ids (label as fallback)
    growth_by_label = {s["label"]: s for s in fc.get("segments", [])}
    data = launch_mod.load(market)
    engine = engine_rows(market)
    cfg = config()["board"]
    grade = (summary or {}).get("evidence_grade")
    rows = []
    n = cfg["max_per_market"]
    by_index = list(segs.sort_values("opportunity_index", ascending=False, na_position="last").index[:n])
    by_engine = [sid for sid, _ in sorted(((k, v["score"]) for k, v in engine.items() if v["score"] is not None and k in segs.index),
                                          key=lambda kv: -kv[1])[:n]]
    picked = list(dict.fromkeys(by_index + by_engine))      # a segment either engine ranks near the top is on the board
    for sid, seg in segs.loc[picked].iterrows():
        r = rec_of.get(sid)
        if r is not None:
            feats = [str(x) for x in _list(r.get("features"))]
            price = concept_price(r.get("price_range"), r.get("comparables"))
            basis, source = r.get("basis"), "gaps"
        else:                                   # no gap analysis (too few listings): the segment's typical product
            feats, price = [], _f(med_price.get(sid))
            price = round(price, 2) if price else None
            basis, source = "no gap analysis for this sub-category (too few listings): concept priced at its median", "median"
        row = {"market": market, "segment_id": sid, "segment_label": seg.get("segment_label"), "features": feats,
               "price_range": (_list(r.get("price_range")) or None) if r is not None else None, "price": price, "basis": basis,
               "concept_source": source, "opportunity_index": _f(seg.get("opportunity_index")), "opportunity_level": seg.get("opportunity_level"),
               "opportunity_coverage": _f(seg.get("opportunity_coverage")), "evidence_grade": grade,
               "segment_revenue": _f(seg.get("revenue_est")), "hhi": _f(seg.get("hhi_est")),
               "entrant_success_rate": _f(seg.get("entrant_success_rate")), "listings": _f(seg.get("listings_v3")),
               "engine": engine.get(str(sid))}
        if len(sb):
            top = sb[sb["segment_id"] == sid].sort_values("share_est", ascending=False).head(1)
            if len(top):
                t = top.iloc[0]
                row["leader"] = {"brand": t["brand"], "share": _f(t["share_est"]), "share_lo": _f(t["share_lo"]),
                                 "share_hi": _f(t["share_hi"]), "p_top": _f(t["p_top"])}
        if len(mom):
            m = mom[mom["scope_id"] == sid]
            if len(m):
                row["momentum"] = m.iloc[0]["direction"]
        g = growth_by_id.get(sid) or growth_by_label.get(seg.get("segment_label"))
        row["growth"] = ({"per_month": g["growth_per_month"], "lo": g["growth_lo"], "hi": g["growth_hi"], "direction": g["direction"]}
                         if g else {"status": fc["market"].get("status"), "periods": fc["market"].get("periods"),
                                    "min_periods": config()["forecast"]["min_periods"]})
        if data is not None and price:
            title = " ".join([str(seg.get("segment_label") or ""), *feats]).strip()
            try:
                # a concept is simulated as a competitive entrant: at the segment's rating bar, not its median listing
                sim = launch_mod.simulate({"title": title, "price": price}, data, market, summary, segment_id=sid,
                                          assume_rating="rating_bar")
                row["launch"] = {"units": sim["units"], "revenue": sim["revenue"],
                                 "profit": {k: sim["profit"][k] for k in ("median", "p_positive")} if sim.get("profit") else None,
                                 "risks": [{"code": x["code"], "severity": x["severity"]} for x in sim.get("risks", [])],
                                 "entrants_actual": sim.get("entrants_actual"),
                                 "assumed_rating": sim["assumptions"].get("rating"), "rating_basis": sim["assumptions"].get("rating_basis")}
            except (KeyError, ValueError) as e:            # a concept the model cannot place is shown without a simulation
                row["launch"] = {"error": str(e)}
        rows.append(row)
    return rows


def _cache_file(name: str) -> Path:
    return get_settings().data_dir / "cache" / "board" / f"{hashlib.sha1(name.encode()).hexdigest()[:16]}.json"


def _stamp(version: str) -> str:
    """The market's version plus the settings the rows depend on (metrics, launch, landed cost, knowledge ...):
    editing any file under config/platform invalidates the stored rows too."""
    h = hashlib.sha1(version.encode())
    for f in sorted((PROJECT_ROOT / "config" / "platform").glob("*.yaml")):
        h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


def _jsonable(o):
    return json.loads(json.dumps(o, default=lambda x: x.item() if hasattr(x, "item") else str(x)))


def _rows(name: str, summary: dict | None, version: str) -> list[dict]:
    """Rows from memory, then from disk (they survive restarts and are shared by workers), else computed."""
    stamp = _stamp(version)
    with _LOCK:
        hit = _CACHE.get(name)
    if hit and hit[0] == stamp:
        return hit[1]
    f = _cache_file(name)
    rows = None
    try:
        stored = json.loads(f.read_text(encoding="utf-8"))
        if stored.get("stamp") == stamp and stored.get("market") == name:
            rows = stored["rows"]
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    if rows is None:
        rows = _jsonable(market_rows(name, summary))
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"market": name, "stamp": stamp, "rows": rows}), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass                                       # a read-only data dir only costs the speed-up
    with _LOCK:
        _CACHE[name] = (stamp, rows)
    return rows


def board(markets: list[tuple[str, dict | None, str]]) -> list[dict]:
    """``markets``: (name, metrics_v3 summary, version) -- version changes whenever the market is re-processed."""
    out = []
    for name, summary, version in markets:
        out.extend(_rows(name, summary, version))
    out.sort(key=board_key)
    return out


def version_of(m) -> str:
    """The cache version of a ``business.Market`` row (changes on every re-processing)."""
    return f"{m.run_id}:{m.updated_at}"


def warm(market: str) -> int:
    """Compute and store a market's board rows now (after processing), so the first visit is fast."""
    from dip.storage import business as b

    with b.session() as s:
        m = s.get(b.Market, market)
        if m is None:
            return 0
        summary, version = (m.summary or {}).get("metrics_v3"), version_of(m)
    return len(_rows(market, summary, version))


def board_key(r: dict) -> tuple:
    """The explainable score first (unscored rows after scored ones), the older index only to break ties."""
    e = (r.get("engine") or {}).get("score")
    oi = r.get("opportunity_index")
    return (e is None, -(e or 0.0), -(oi if oi is not None else -1.0))

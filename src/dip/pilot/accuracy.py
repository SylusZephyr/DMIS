"""Accuracy dashboard (Phase 8): how far can the numbers be trusted -- measured, not asserted.

* **Synthetic validation** -- markets with a known truth are generated, passed through the real observation
  process (badge ladder) and estimated by the production demand model; the estimate is scored against the
  truth: market-size error, 95% interval coverage, listing coverage, whether the top sub-category is found.
  A set of scenarios is run (``accuracy.synthetic_scenarios``): the model's own functional form (best case)
  and misspecified worlds (heavy tails, brand effects, curved price response, dead listings, brand-correlated
  noise, review counts). The checks are scored on the **worst scenario**, not the best case. Run on demand
  (background thread); the latest run is stored with its time.
* **Per-market hold-out** -- the engine's cross-validation of each market's demand model (hidden badges
  predicted from the rest): badged-vs-unbadged AUC, rung accuracy, interval coverage of held-out listings.
* **Labels** -- human-labelled samples and their precision/recall with Wilson intervals (pilot.metrics).
* **Launch calibration** -- launched projects' predictions against the sales they actually reached.

Each block carries a pass/watch/fail status against targets in config/platform/metrics.yaml (``accuracy``).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import numpy as np

from dip.metrics import config
from dip.settings import get_settings
from dip.storage import business as b

_lock = threading.Lock()
_running = {"state": False}


def _path():
    return get_settings().data_dir / "accuracy_validation.json"


def _status(value, target: float, watch: float, higher_is_better: bool = True) -> str:
    if value is None:
        return "n/a"
    if higher_is_better:
        return "pass" if value >= target else "watch" if value >= watch else "fail"
    return "pass" if value <= target else "watch" if value <= watch else "fail"


def run_synthetic(markets: int | None = None, scenarios: list[str] | None = None) -> dict:
    """``markets`` synthetic markets per scenario. Summary: pooled over all markets (``summary``), per scenario
    (``summary.by_scenario``) and the worst scenario per measure (``summary.worst_case``)."""
    from dip.metrics.synthetic import evaluate, scenario, summarize, worst_case

    cfg = config()["accuracy"]
    n = markets or cfg["synthetic_markets"]
    names = scenarios or cfg.get("synthetic_scenarios") or ["specified", "heavy_tails"]
    rows = []
    for name in names:
        for i in range(n):
            sc = scenario(name, n=cfg["synthetic_listings"], seed=1000 + i)
            r = evaluate(sc)
            rows.append({"seed": 1000 + i, "heavy_tails": sc.heavy_tails,
                         **{k: (float(v) if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool) else v)
                            for k, v in r.items()}})
    by = {name: summarize([r for r in rows if r["scenario"] == name]) for name in names}
    out = {"ran_at": datetime.now(timezone.utc).isoformat(), "markets": n, "scenarios": names, "rows": rows,
           "summary": {**summarize(rows), "by_scenario": by, "worst_case": worst_case(by)}}
    _path().write_text(json.dumps(out, default=str))
    return out


def start_synthetic(markets: int | None = None) -> bool:
    """Run in a background thread; False when a run is already going."""
    with _lock:
        if _running["state"]:
            return False
        _running["state"] = True

    def work():
        try:
            run_synthetic(markets)
        finally:
            _running["state"] = False
    threading.Thread(target=work, daemon=True).start()
    return True


def checks(summary: dict, cfg: dict) -> list[dict]:
    """Pass/watch/fail per measure, scored on the worst scenario (older stored runs without scenarios: pooled)."""
    w = summary.get("worst_case") or summary
    spec = (("median_abs_error", "size_error_target", "size_error_watch", False),
            ("market_coverage_95", "coverage_target", "coverage_watch", True),
            ("listing_coverage_95", "coverage_target", "coverage_watch", True),
            ("top_category_correct", "top_category_target", "top_category_watch", True))
    out = []
    for metric, tgt, watch, hib in spec:
        v = w.get(metric)
        out.append({"metric": metric, "value": v, "target": cfg[tgt], "scenario": w.get(f"{metric}_scenario"),
                    "basis": "worst scenario" if "worst_case" in summary else "pooled",
                    "status": _status(v, cfg[tgt], cfg[watch], hib)})
    return out


def dashboard(visible: list[str] | None = None) -> dict:
    cfg = config()["accuracy"]
    syn = json.loads(_path().read_text()) if _path().exists() else None
    if syn:
        syn["checks"] = checks(syn["summary"], cfg)
    markets = []
    with b.session() as s:
        for m in s.query(b.Market).all():
            if visible is not None and m.name not in visible:
                continue
            v3 = (m.summary or {}).get("metrics_v3") or {}
            cv = (v3.get("demand_model") or {}).get("crossvalidation") or {}
            auc = cv.get("auc_badged_vs_unbadged")
            markets.append({"market": m.name, "evidence_grade": v3.get("evidence_grade"), "badged_share": v3.get("badged_share"),
                            "model_family": (v3.get("demand_model") or {}).get("family"), "listings": (v3.get("demand_model") or {}).get("n"),
                            "auc": auc, "auc_status": _status(auc, cfg["auc_target"], cfg["auc_watch"]),
                            "rung_within_one": cv.get("rung_within_one_rate"), "coverage_95": cv.get("coverage_95"),
                            "coverage_80": cv.get("coverage_80"), "held_out": cv.get("badged_listings"), "error": cv.get("error")})
    labels = []
    try:
        from dip.pilot import metrics as M
        with b.session() as s:
            samples = [(x.id, x.market_name, x.name, x.created_at) for x in s.query(b.LabelSample).all()]
        for sid, mk, purpose, at in samples:
            if visible is not None and mk not in visible:
                continue
            try:
                labels.append({"sample_id": sid, "market": mk, "name": purpose, "created_at": at, **M.evaluate(sid)})
            except ValueError as exc:
                labels.append({"sample_id": sid, "market": mk, "name": purpose, "created_at": at, "note": str(exc)})
    except Exception as exc:  # labels are optional; never break the dashboard
        labels = [{"note": f"labels unavailable: {exc}"}]
    from dip.projects import calibration
    return {"synthetic": syn, "synthetic_running": _running["state"], "markets": markets, "labels": labels,
            "launch_calibration": calibration(), "targets": cfg}

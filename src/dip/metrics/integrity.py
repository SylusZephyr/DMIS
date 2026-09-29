"""Truth harness: checks a processed market for numbers that disagree with each other, break their own bounds,
or rest on evidence that failed its own validation -- so a wrong or unsupported number is caught by code, not by a
reader. Thresholds: ``integrity`` in config/platform/metrics.yaml.

Every check returns ``pass`` / ``warn`` / ``fail`` with the values it compared:

* ``fail``: a number the platform displays is inconsistent or unsupported (two answers for one quantity, an
  estimate outside its own bounds, a trend claimed without history, a model that failed hold-out validation).
* ``warn``: the number is defensible but needs a caveat or a human decision (undecided scope, missing dates, no
  declared snapshot date).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from dip.metrics import config
from dip.storage import lake


def _cfg() -> dict:
    return config()["integrity"]


def validation_issues(cv: dict | None, badged: int) -> list[str]:
    """Why the demand model's estimate is not supported (empty: it passed hold-out validation). Shared by the
    engine (``model_validated`` in the market summary) and the ``demand_model_validated`` check."""
    c = _cfg()
    cv = cv or {}
    why = []
    if badged < int(c["min_badged_listings"]):
        why.append(f"only {badged} listings with observed sales (need {c['min_badged_listings']})")
    if cv.get("error"):
        why.append(f"hold-out validation failed to run ({cv['error']})")
    elif not cv:
        why.append("not validated on held-out listings (too few listings or validation switched off)")
    auc, c80, c95 = _f(cv.get("auc_badged_vs_unbadged")), _f(cv.get("coverage_80")), _f(cv.get("coverage_95"))
    if auc is not None and auc < float(c["min_holdout_auc"]):
        why.append(f"hold-out AUC {auc:.2f} (need {c['min_holdout_auc']}; 0.5 is chance)")
    if c80 is not None and c80 < float(c["min_coverage_80"]):
        why.append(f"80% intervals held the truth {c80:.0%} of the time (need {float(c['min_coverage_80']):.0%})")
    if c95 is not None and c95 < float(c["min_coverage_95"]):
        why.append(f"95% intervals held the truth {c95:.0%} of the time (need {float(c['min_coverage_95']):.0%})")
    return why


def _check(cid: str, status: str, detail: str, **values) -> dict:
    return {"id": cid, "status": status, "detail": detail, "values": {k: (round(v, 4) if isinstance(v, float) else v)
                                                                      for k, v in values.items()}}


def _close(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-9)


def _f(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _read(table: str, market: str) -> pd.DataFrame:
    return lake.read_curated(table, market) if lake.has_curated(table, market) else pd.DataFrame()


def check_market(market: str, summary: dict | None = None) -> dict:
    """All checks for one processed market (``summary``: the Market row's summary; read when not given)."""
    if summary is None:
        from dip.storage import business as b
        with b.session() as s:
            m = s.get(b.Market, market)
            summary = dict(m.summary or {}) if m else {}
    c = _cfg()
    tol = float(c["rel_tolerance"])
    v3 = summary.get("metrics_v3") or {}
    out: list[dict] = []
    met = _read("metrics", market)

    def mval(scope: str, metric: str, sid: str | None = None) -> pd.DataFrame:
        if met.empty:
            return met
        q = met[(met["scope"] == scope) & (met["metric"] == metric)]
        return q if sid is None else q[q["scope_id"] == sid]

    rev = v3.get("revenue_month") or {}
    est, lo, hi = _f(rev.get("estimate")), _f(rev.get("low")), _f(rev.get("high"))
    floor, ceiling = _f(rev.get("floor")), _f(rev.get("ceiling"))

    # 1. one quantity, one number: market revenue wherever it is stored
    seen = {"metrics_v3": est}
    cap = _read("capacity", market)
    if len(cap) and (cap["scope"] == "category").any():
        seen["capacity"] = _f(cap[cap["scope"] == "category"]["revenue_est"].iat[0])
    rh = _read("revenue_history", market)
    if len(rh) and (rh["scope"] == "market").any():
        seen["revenue_history_latest"] = _f(rh[rh["scope"] == "market"].sort_values("period")["est"].iat[-1])
    fc = (summary.get("forecast") or {}).get("history") or []
    if fc:
        seen["forecast_history_latest"] = _f(fc[-1][1])
    bad = {k: v for k, v in seen.items() if not _close(v, est, tol)}
    out.append(_check("market_revenue_single_value", "fail" if bad else "pass",
                      ("market revenue differs between stores: " + ", ".join(f"{k}={v:,.0f}" for k, v in bad.items())
                       + f" vs estimate {est:,.0f}") if bad else "every store holds the same market revenue", **seen))

    # the older segment table sums observed badge floors under the name 'monthly_revenue'
    sg = _read("segments", market)
    if len(sg) and "monthly_revenue" in sg:
        v1 = _f(sg["monthly_revenue"].sum())
        same_as = "estimate" if _close(v1, est, tol) else ("floor" if _close(v1, floor, tol) else "neither")
        out.append(_check("segment_table_revenue_meaning", "pass" if same_as == "estimate" else "fail",
                          f"segments.monthly_revenue sums to {v1:,.0f}, which matches the {same_as}"
                          + ("" if same_as == "estimate" else f" ({floor:,.0f} floor / {est:,.0f} estimate): the same label "
                             "shows a different quantity than the market headline"), segments_sum=v1, floor=floor, estimate=est))

    # 2. additivity: segments add up to the market
    seg = mval("segment", "revenue_month")
    if len(seg) and est is not None:
        s = _f(seg["value"].sum())
        out.append(_check("segments_add_up", "pass" if _close(s, est, tol) else "fail",
                          f"segment revenues sum to {s:,.0f} vs market {est:,.0f}", segment_sum=s, market=est))

    # 3. bounds: floor <= low <= estimate <= high <= ceiling (market and every segment)
    viol = []
    chain = [(k, v) for k, v in (("floor", floor), ("low", lo), ("estimate", est), ("high", hi), ("ceiling", ceiling))
             if v is not None]                                   # an unknown ceiling (open top rung) skips only itself
    broken = [f"{a} {x:,.0f} > {b} {y:,.0f}" for (a, x), (b, y) in zip(chain, chain[1:]) if x > y + 1e-6 * max(1.0, abs(y))]
    if broken:
        viol.append("market: " + ", ".join(broken))
    if len(seg):
        fl = mval("segment", "revenue_month_floor").set_index("scope_id")["value"]
        for r in seg.itertuples():
            f0 = _f(fl.get(r.scope_id))
            e, e_lo, e_hi = _f(r.value), _f(r.low), _f(r.high)
            if e_lo is not None and e_hi is not None and e is not None and not (e_lo - 1e-6 <= e <= e_hi + 1e-6):
                viol.append(f"segment {r.scope_id}: estimate {e:,.0f} outside [{e_lo:,.0f}, {e_hi:,.0f}]")
            elif f0 is not None and e is not None and e < f0 - 1e-6:
                viol.append(f"segment {r.scope_id}: estimate {e:,.0f} below its observed floor {f0:,.0f}")
    out.append(_check("estimates_within_bounds", "fail" if viol else "pass",
                      "; ".join(viol[:5]) + (f" (+{len(viol) - 5} more)" if len(viol) > 5 else "") if viol
                      else "every estimate lies within its interval and above its observed floor", violations=len(viol)))

    # 4. two intervals for the same estimate
    if len(cap) and (cap["scope"] == "category").any() and lo is not None:
        cr = cap[cap["scope"] == "category"].iloc[0]
        clo, chi = _f(cr.get("revenue_lo")), _f(cr.get("revenue_hi"))
        same = _close(clo, lo, tol) and _close(chi, hi, tol)
        out.append(_check("single_interval", "pass" if same else "fail",
                          "one interval for the market estimate" if same else
                          f"two intervals for the same estimate: capacity {clo:,.0f}–{chi:,.0f} vs metrics {lo:,.0f}–{hi:,.0f}",
                          capacity_low=clo, capacity_high=chi, metrics_low=lo, metrics_high=hi))

    # 5. is the demand model good enough to show an estimate at all?
    dm = v3.get("demand_model") or {}
    cv = dm.get("crossvalidation") or {}
    badged = int(cv.get("badged_listings") or (v3.get("sales_observation") or {}).get("observed") or 0)
    auc, c80, c95 = _f(cv.get("auc_badged_vs_unbadged")), _f(cv.get("coverage_80")), _f(cv.get("coverage_95"))
    why = validation_issues(dm.get("crossvalidation"), badged)
    modelled_share = (est - floor) / est if est and floor is not None else None
    out.append(_check("demand_model_validated", "fail" if why else "pass",
                      ("the modelled estimate is not supported: " + "; ".join(why)) if why
                      else "the demand model passed hold-out validation",
                      badged_listings=badged, holdout_auc=auc, coverage_80=c80, coverage_95=c95,
                      modelled_share_of_estimate=modelled_share))

    # 6. listings counted before their category scope was decided
    li = _read("listings", market)
    if len(li) and "scope_status" in li:
        r = pd.to_numeric(li["price"], errors="coerce") * pd.to_numeric(li["sales"], errors="coerce")
        total = _f(r.sum()) or 0.0
        und = _f(r[li["scope_status"] == "review"].sum()) or 0.0
        share = und / total if total else 0.0
        n_und = int((li["scope_status"] == "review").sum())
        out.append(_check("scope_decided", "warn" if share > float(c["max_undecided_revenue_share"]) else "pass",
                          f"{n_und} of {len(li)} counted listings are still 'review' for category scope, "
                          f"holding {share:.0%} of the observed revenue", undecided_listings=n_und, listings=len(li),
                          undecided_revenue_share=share))
        if "launch_date" in li:
            ok = float(li["launch_date"].notna().mean())
            out.append(_check("launch_dates_readable", "warn" if ok < float(c["min_launch_date_share"]) else "pass",
                              f"{ok:.0%} of listings have a readable launch date (age, entrant and cohort metrics use it)",
                              share=ok))

        # 6b. observed sales left out of every total for data reasons (rejected rows, unusable quality): the floor
        # is "certain" only for what was counted, so say how much certain demand the source had beyond it
        rec = _read("records", market)
        if len(rec) and "sales" in rec:
            out.append(_excluded_sales(rec, li, float(c.get("max_excluded_sales_share", 0.0))))

    # 7. what date are these numbers 'as of'?
    basis = str(v3.get("as_of_basis") or "")
    out.append(_check("snapshot_date_declared", "warn" if "no snapshot date" in basis else "pass",
                      f"as of {v3.get('as_of')}: {basis or 'declared'}", as_of=v3.get("as_of")))

    # 8. claims that need history
    snaps = int(v3.get("snapshots") or 1)
    tr = summary.get("trend") or {}
    label = str(tr.get("trend") or "")
    claims = label and not label.lower().startswith("insufficient") and snaps < int(c["min_snapshots_for_trend"])
    out.append(_check("trend_needs_history", "fail" if claims else "pass",
                      (f"trend '{label}' is stated from {snaps} snapshot(s); "
                       f"a trend needs at least {c['min_snapshots_for_trend']}") if claims
                      else "no trend is claimed without enough history", snapshots=snaps, trend=label or None))

    # 9. shares add up
    br = _read("brands", market)
    if len(br) and "share_est" in br:
        s = _f(br["share_est"].sum())
        out.append(_check("brand_shares_add_up", "pass" if s is not None and abs(s - 1) <= tol else "fail",
                          f"brand shares sum to {s:.3f}", share_sum=s))

    # 9b. one growth number: the trend's expected 12-month growth and the forecast's
    fc_s = summary.get("forecast") or {}
    tg, fg = _f((summary.get("trend") or {}).get("expected_growth_12m")), _f(fc_s.get("growth_12m"))
    if tg is not None and fg is not None and fc_s.get("status") == "ok":
        same = abs(tg - fg) <= 1e-3 + tol * abs(fg)
        out.append(_check("growth_single_value", "pass" if same else "fail",
                          f"expected 12-month growth {tg:+.1%} (trend) vs {fg:+.1%} (forecast)" + ("" if same else
                          ": two growth numbers for one market"), trend=tg, forecast=fg))

    # 10. one opportunity score: the segment table, the market headline and the explainable engine agree
    opp = _read("opportunities", market)
    if len(opp) and len(sg) and "opportunity_score" in sg and "opportunity_score" in opp:
        eng = opp[opp["scope"] == "segment"].assign(scope_id=lambda d: d["scope_id"].astype(str)).set_index("scope_id")["opportunity_score"]
        tab = sg.assign(segment_id=sg["segment_id"].astype(str)).set_index("segment_id")["opportunity_score"]
        both = pd.concat([tab.rename("table"), eng.rename("engine")], axis=1, join="inner")
        differ = both[~((both["table"].isna() & both["engine"].isna()) | ((both["table"] - both["engine"]).abs() <= 1e-6))]
        head = _f((summary.get("opportunity") or {}).get("top_score"))
        eng_top = _f(eng.max()) if eng.notna().any() else None
        head_ok = _close(head, eng_top, 1e-9) if head is not None and eng_top is not None else head is None and eng_top is None
        why = ([f"{len(differ)} segments' stored opportunity_score differs from the engine's"] if len(differ) else []) + \
              ([f"market headline {head} vs engine best {eng_top}"] if not head_ok else [])
        out.append(_check("one_opportunity_score", "fail" if why else "pass",
                          "; ".join(why) if why else "one opportunity score on every surface (the explainable engine's)",
                          segments_differing=int(len(differ)), headline=head, engine_best=eng_top))

    # 11. the model against your own sales: the only truth for listings without a badge (Seller Central reports)
    own = _own_sales_vs_model(market, v3.get("as_of"), c)
    if own is not None:
        out.append(own)

    rank = {"pass": 0, "warn": 1, "fail": 2}
    worst = max((x["status"] for x in out), key=rank.get, default="pass")
    return {"market": market, "status": worst, "checks": out,
            "counts": {k: sum(1 for x in out if x["status"] == k) for k in ("pass", "warn", "fail")}}


def _reasons(v) -> list[str]:
    """A record's rejection reasons / quality issues, stored as a list or as its JSON text."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return [v] if v else []
    return [str(x) for x in v] if isinstance(v, (list, tuple, np.ndarray)) else [str(v)]


def _excluded_sales(rec: pd.DataFrame, li: pd.DataFrame, max_share: float) -> dict:
    """Relevant records with observed sales whose listing is in no total (rejected or unusable data), except rows
    that repeat a counted listing's id (their sales are counted once, on that listing)."""
    sales = pd.to_numeric(rec["sales"], errors="coerce")
    counted_ids = set(li["id"].astype(str)) if "id" in li else set()
    counted_rec = set(li["record_id"].astype(str)) if "record_id" in li else set()
    relevant = rec["is_relevant"].fillna(False).astype(bool) if "is_relevant" in rec else pd.Series(True, index=rec.index)
    left = (relevant & (sales > 0) & ~rec["record_id"].astype(str).isin(counted_rec)
            & ~rec["id"].astype(str).isin(counted_ids))
    counted_units = float(pd.to_numeric(li.get("sales"), errors="coerce").sum()) if "sales" in li else 0.0
    units = float(sales[left].sum())
    share = units / (units + counted_units) if units + counted_units else 0.0
    why: dict[str, int] = {}
    for _, r in rec[left].iterrows():
        rs = _reasons(r.get("rejection_reasons")) or _reasons(r.get("quality_issues")) or ["unusable"]
        for x in rs:
            why[x] = why.get(x, 0) + 1
    n = int(left.sum())
    detail = (f"{n} relevant listing(s) with observed sales ({units:,.0f} units/month on their badges, {share:.1%} of all "
              f"observed units) are in no total for data reasons: " + ", ".join(f"{k} x{v}" for k, v in sorted(why.items()))
              if n else "every relevant listing with observed sales is counted")
    return _check("excluded_sales_disclosed", "warn" if n and share > max_share else "pass", detail, listings=n,
                  units=units, share=share, reasons=why)


def _own_sales_vs_model(market: str, as_of, c: dict) -> dict | None:
    """Your Seller Central units for this market's listings (report periods within ``own_sales_max_days_apart`` of
    the snapshot) against the model's estimate and 95 % interval for the same listings. None when there are none."""
    try:
        from dip import own_sales
        rep = own_sales._frame()
    except Exception:
        return None
    li = _read("listings", market)
    if rep is None or rep.empty or li.empty or "units_est" not in li or as_of is None:
        return None
    li = li.assign(asin=li["id"].astype(str).str.upper())
    rep = rep.assign(gap=(rep["midpoint"] - pd.Timestamp(as_of)).abs().dt.days)
    rep = rep[rep["gap"] <= int(c.get("own_sales_max_days_apart", 45))].sort_values("gap").drop_duplicates("asin")
    m = rep.merge(li[["asin", "units_est", "units_lo", "units_hi", "sales"]], on="asin", how="inner")
    if m.empty:
        return None
    actual = pd.to_numeric(m["units_month"], errors="coerce")
    est, lo, hi = (pd.to_numeric(m[k], errors="coerce") for k in ("units_est", "units_lo", "units_hi"))
    inside = (actual >= lo - 1e-9) & (actual <= hi + 1e-9)
    cov = float(inside.mean())
    ratio = float((actual / est.where(est > 0)).median()) if (est > 0).any() else None
    unbadged = int(m["sales"].isna().sum())
    n, need = int(len(m)), int(c.get("own_sales_min_listings", 3))
    detail = (f"your sales for {n} listing(s) ({unbadged} without a badge): {cov:.0%} inside the model's 95 % interval"
              + (f", actual / estimate median {ratio:.2f}" if ratio is not None else ""))
    if n < need:
        return _check("model_vs_own_sales", "warn", detail + f" -- too few to judge (need {need})", listings=n,
                      unbadged=unbadged, coverage_95=cov, median_ratio=ratio)
    ok = cov >= float(c["min_coverage_95"])
    return _check("model_vs_own_sales", "pass" if ok else "fail",
                  detail + ("" if ok else f" (need {float(c['min_coverage_95']):.0%})"), listings=n, unbadged=unbadged,
                  coverage_95=cov, median_ratio=ratio)


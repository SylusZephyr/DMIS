"""Employee intelligence: a one-screen brief per market and a focus list per employee.

``market_brief`` answers "I clicked Micromotor": market size, products, opportunity
level, trend, confidence, the development suggested by the best segment (its most
common product model / specification), leading competitors and matching suppliers.

``employee_focus`` turns an employee's categories into recommended actions, each with
its reason and the number behind it -- ranked high / medium / low.
"""

from __future__ import annotations

import json

import pandas as pd

from dip.storage import business as b
from dip.metrics import opportunity_thresholds
from dip.storage import lake


def _j(v, default):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def opportunity_level(score: float | None) -> str:
    if score is None or pd.isna(score):
        return "Unknown"
    moderate, high = opportunity_thresholds()
    return "High" if score >= high else "Medium" if score >= moderate else "Low"


def suggested_development(market: str, segment_id: str, segment_row: dict) -> str | None:
    """The segment's leading product model (spec or text model) -- what to build if entering."""
    cols = lake.curated_columns("products", market)
    if "model_label" not in cols:
        return segment_row.get("dominant_specs") or None
    p = lake.read_curated("products", market, columns=["model_label", "monthly_revenue", "product_id"],
                          where="segment_id = ? AND model_label <> 'other'", params=[segment_id])
    if p.empty:
        return segment_row.get("dominant_specs") or segment_row.get("segment_label")
    g = p.groupby("model_label").agg(rev=("monthly_revenue", "sum"), n=("product_id", "count"))
    g = g.sort_values(["rev", "n"], ascending=False)
    top = g.index[0]
    spec = segment_row.get("dominant_specs")
    return f"{segment_row.get('segment_label')} — {top}" + (f" ({spec})" if spec and spec not in top else "")


def market_brief(market: str) -> dict:
    with b.session() as s:
        m = s.get(b.Market, market)
        if m is None:
            return {"market": market, "status": "no_data"}
        su = dict(m.summary or {})
    cat = su.get("category", {})
    segs = lake.read_curated("segments", market, order="opportunity_score DESC NULLS LAST")
    best = segs.iloc[0].to_dict() if len(segs) else {}
    comp = lake.read_curated("competitors", market, order="share DESC", limit=5) if lake.has_curated("competitors", market) else pd.DataFrame()
    matches = lake.read_curated("supplier_matches", market, where="match_score >= 0.15", order="match_score DESC")
    suppliers = []
    if len(matches):
        with b.session() as s:
            seen = set()
            for _, r in matches.iterrows():
                if r["supplier_id"] in seen:
                    continue
                seen.add(r["supplier_id"])
                sup = s.get(b.Supplier, r["supplier_id"])
                if sup:
                    suppliers.append({"supplier_id": sup.id, "name": sup.name, "country": sup.country,
                                      "segment": r.get("segment_label"), "match_score": round(float(r["match_score"]), 3)})
                if len(suppliers) >= 5:
                    break
    trend = su.get("trend") or {}
    top_score = (su.get("opportunity") or {}).get("top_score")
    return {
        "market": market, "status": "ok", "industry_branch": m.industry_branch,
        "market_size": {"monthly_revenue": cat.get("monthly_revenue"), "annual_revenue": cat.get("annual_revenue"),
                        "sales_coverage": cat.get("sales_coverage"),
                        "note": "observed revenue of products with known sales -- a lower bound"},
        "products": cat.get("products"), "listings": cat.get("listings"), "segments": cat.get("segments"),
        "opportunity": {"level": opportunity_level(top_score), "top_score": top_score,
                        "high_opportunity_segments": (su.get("opportunity") or {}).get("high_opportunity_segments")},
        "suggested_development": suggested_development(market, best["segment_id"], best) if best else None,
        "best_segment": {k: best.get(k) for k in ("segment_id", "segment_label", "opportunity_score", "monthly_revenue",
                                                  "top_brand", "trend_label", "opportunity_drivers")} if best else None,
        "trend": {k: trend.get(k) for k in ("trend", "confidence", "expected_growth_12m", "evidence")},
        "confidence": su.get("confidence"),
        "competitors": [{"brand": r["brand"], "position": r["position"], "share": r["share"],
                         "weakness": (_j(r["weaknesses"], []) or [None])[0], "opportunity": (_j(r["opportunities"], []) or [None])[0]}
                        for _, r in comp.iterrows()],
        "suppliers": suppliers,
        "history_periods": su.get("history_periods") or [],
    }


PRIORITY = {"high": 0, "medium": 1, "low": 2}


def _money(v) -> str:
    return "n/a" if v is None or pd.isna(v) else f"${v / 1e3:.1f}k" if abs(v) >= 1e3 else f"${v:,.0f}"


def _actions_for(market: str, brief: dict) -> list[dict]:
    """Recommended actions from metrics v3, each in English and Chinese, each with the numbers behind it."""
    import json as _json

    acts = []

    def add(pr, action, reason, action_zh, reason_zh, link=None):
        acts.append({"priority": pr, "market": market, "action": action, "reason": reason, "action_zh": action_zh,
                     "reason_zh": reason_zh, "link": link})

    with b.session() as s:
        m = s.get(b.Market, market)
        su = (m.summary or {}) if m else {}
    v3 = su.get("metrics_v3") or {}
    segs = lake.read_curated("segments", market)
    recs = lake.read_curated("recommendations", market)
    rec_by = {r["segment_id"]: r for r in recs.to_dict("records")} if len(recs) else {}
    if len(segs) and "opportunity_index" in segs:
        high = opportunity_thresholds()[1]
        # rank by the explainable opportunity score (knowledge layer); markets processed before it keep the index
        score_col, label_en, label_zh = "opportunity_index", "opportunity index", "机会指数"
        if lake.has_curated("opportunities", market):
            o = lake.read_curated("opportunities", market)
            o = o[o["scope"] == "segment"] if len(o) and "scope" in o else o
            if len(o):
                segs = segs.assign(_score=segs["segment_id"].astype(str).map(dict(zip(o["scope_id"].astype(str), o["opportunity_score"]))))
                score_col, label_en, label_zh = "_score", "opportunity score", "机会评分"
        for r in segs.sort_values(score_col, ascending=False, na_position="last").head(3).to_dict("records"):
            oi = r.get(score_col)
            if oi is None or pd.isna(oi) or oi < high:
                continue
            rec = rec_by.get(r["segment_id"]) or {}
            feats = rec.get("features")
            feats = _json.loads(feats) if isinstance(feats, str) else list(feats) if feats is not None and not isinstance(feats, float) else []
            esr = r.get("entrant_success_rate")
            esr_txt = "n/a" if esr is None or pd.isna(esr) else f"{esr:.0%}"
            rev = f"{_money(r.get('revenue_est'))}/month ({_money(r.get('revenue_lo'))}–{_money(r.get('revenue_hi'))})"
            add("high", f"Evaluate a launch in '{r['segment_label']}'" + (f" with {', '.join(feats)}" if feats else ""),
                f"{label_en} {oi:.0f}; {rev}; entrant success {esr_txt}",
                f"评估在「{r['segment_label']}」上市" + (f"（{', '.join(feats)}）" if feats else ""),
                f"{label_zh} {oi:.0f}；月销售额 {rev.replace('/month', '')}；新品成功率 {esr_txt}",
                {"page": "launch", "market": market, "segment_id": r["segment_id"], "title": r["segment_label"]})
    with b.session() as s:
        alerts = (s.query(b.Alert).join(b.Event).filter(b.Event.market_name == market, b.Alert.status == "new",
                                                        b.Event.severity == "important").count())
    if alerts:
        add("high", f"Review {alerts} important alert(s)", "statistically significant changes since the last upload",
            f"查看 {alerts} 条重要预警", "自上次上传以来的统计显著变化", {"page": "alerts", "market": market})
    if not brief.get("suppliers"):
        add("medium", f"Find manufacturers for {market}", "no supplier in the database matches this market's segments",
            f"为 {market} 寻找制造商", "数据库中没有与该市场细分匹配的供应商", {"page": "suppliers"})
    if len(brief.get("history_periods") or []) < 2:
        add("medium", f"Upload next month's {market} export", "one snapshot only: share changes and growth need a second one",
            f"上传 {market} 下个月的导出数据", "只有一个快照：份额变化和增长需要第二个快照", {"page": "data", "market": market})
    grade = v3.get("evidence_grade")
    if grade in ("C", "D"):
        bs = v3.get("badged_share")
        bs_txt = "n/a" if bs is None else f"{bs:.1%}"
        add("medium", f"Strengthen the evidence for {market} (grade {grade})",
            f"only {bs_txt} of listings carry a sales badge; add exports with exact sales or more listings",
            f"加强 {market} 的证据（等级 {grade}）", f"只有 {bs_txt} 的商品有销量徽章；请补充含精确销量或更多商品的导出数据",
            {"page": "accuracy", "market": market})
    unc = (su.get("relevance") or {}).get("uncertain", 0)
    if unc:
        add("low", f"Review {unc} uncertain {market} listing(s)", "relevance could not be decided automatically",
            f"复核 {unc} 条不确定的 {market} 商品", "相关性无法自动判定", {"page": "data", "market": market})
    br = lake.read_curated("brands", market, order="share_est DESC", limit=3)
    zh_open = {"better_value": "更高性价比的替代品", "better_quality": "更高质量的替代品", "newer_design": "更新的设计"}
    en_open = {"better_value": "a better-value alternative", "better_quality": "a better-quality alternative", "newer_design": "a newer design"}
    for r in br.to_dict("records"):
        sig = r.get("signals")
        sig = _json.loads(sig) if isinstance(sig, str) else (sig or [])
        weak = [x for x in sig if x.get("kind") == "weakness" and x.get("opening")]
        if r.get("position") in ("leader", "above_par") and weak:
            w = weak[0]
            add("medium", f"Counter {r['brand']} with {en_open.get(w['opening'], w['opening'])}",
                f"{r['brand']} holds {r['share_est']:.0%} of revenue; weakness: {w['code'].replace('_', ' ')} ({w.get('value')})",
                f"以{zh_open.get(w['opening'], w['opening'])}对抗 {r['brand']}",
                f"{r['brand']} 占销售额 {r['share_est']:.0%}；弱点：{w['code']}（{w.get('value')}）", {"page": "competitors", "market": market})
    return acts


def employee_focus(employee_id: str) -> dict | None:
    with b.session() as s:
        e = s.get(b.Employee, employee_id)
        if e is None:
            return None
        cats = [(o.category.id, o.category.label, o.category.market_name) for o in e.ownership]
        name = e.name
        alerts = (s.query(b.Alert).filter(b.Alert.employee_id == employee_id, b.Alert.status == "new").count())
    markets = sorted({m for _, _, m in cats if m})
    briefs = {m: market_brief(m) for m in markets}
    categories = [{"category_id": cid, "label": lab, "market": m,
                   "status": "ok" if m and briefs.get(m, {}).get("status") == "ok" else "no_market_data"} for cid, lab, m in cats]
    actions = [a for m in markets for a in _actions_for(m, briefs[m])]
    unmapped = [c for c in categories if c["status"] == "no_market_data"]
    if unmapped:
        labels = ", ".join(c["label"] for c in unmapped[:5]) + (" …" if len(unmapped) > 5 else "")
        actions.append({"priority": "low", "market": None, "action": f"Upload exports for {len(unmapped)} unmapped categor"
                        + ("y" if len(unmapped) == 1 else "ies"), "reason": labels,
                        "action_zh": f"为 {len(unmapped)} 个未关联的品类上传导出数据", "reason_zh": labels, "link": {"page": "data"}})
    actions.sort(key=lambda a: PRIORITY[a["priority"]])
    return {"employee_id": employee_id, "name": name, "new_alerts": alerts, "categories": categories,
            "markets": [briefs[m] for m in markets], "recommended_actions": actions}

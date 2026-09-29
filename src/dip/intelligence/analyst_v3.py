"""Market analyst v3 (Phase 7): computed facts, optional Gemini phrasing, a number guard.

1. **Intent** from the question (English and Chinese keywords): size, opportunity, recommend, competitors,
   entry, economics, launch, compare, why, growth, overview.
2. **Scope** (markets, segments, brand) resolved from the text (``analyst.resolve_scope``).
3. **Tools** compute *facts* from the metrics v3 tables. A fact is a sentence in English and Chinese, the
   numbers in it, and the API path that returns the same numbers. The computed answer is those facts.
4. **Phrasing** (optional, ``use_ai``): Gemini gets the question and the facts and may only rephrase them,
   citing fact ids. Every number in the reply must match a fact (after unit scaling, within
   ``number_tolerance``); otherwise the reply is rejected and the computed answer stands. Every call is
   stored in ``ai_traces`` (model, prompt version, input hash, output, status).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dip.intelligence.analyst import config, resolve_scope
from dip.storage import business as b
from dip.storage import lake

INTENTS = [   # first match wins
    ("launch", r"\b(launch\b|what happens if|what if|if we sell|at \$\d)|上市|如果.*(卖|定价|价格)"),
    ("compare", r"\b(compare|versus|vs\.?|difference between)\b|比较|对比"),
    ("why", r"\b(why|explain|how is .* (calculated|computed))\b|为什么|如何计算"),
    ("recommend", r"\b(what should (we|i) (sell|make|build|launch)|recommend\w*|best product|which product)\b|卖什么|推荐|应该(做|卖|开发)"),
    ("competitors", r"\b(competitors?|competition|brands?|rivals?|market share|leaders?|who (leads|dominates))\b|竞争|品牌|对手|份额|领导|领先|龙头|头部"),
    ("entry", r"\b(entr\w+|enter\w*|newcomer|new listing|barrier|hard to enter|difficult)\b|进入|新品|门槛|难度"),
    ("economics", r"\b(margin|profit\w*|cost|economics|fees?)\b|利润|毛利|成本"),
    ("growth", r"\b(grow\w*|trend\w*|momentum|declin\w*|launches)\b|增长|趋势|动能|上新|加速|放缓"),
    ("opportunity", r"\b(opportunit\w*|best segment|where .* (enter|compete)|gap\w*)\b|机会|细分|缺口"),
    ("size", r"\b(size|how big|revenue|sales|how much|market value|units)\b|规模|多大|销售额|销量"),
]
_NUM = re.compile(r"(?<![A-Za-z#])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([kKmM万]?)(\s*%)?")


def intent_of(q: str) -> str:
    ql = q.lower()
    for name, pat in INTENTS:
        if re.search(pat, ql):
            return name
    return "overview"


@dataclass
class Fact:
    en: str
    zh: str
    source: str
    values: list[float] = field(default_factory=list)


def _m(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(v):
        return "n/a"
    return f"${v / 1e6:.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:.1f}k" if abs(v) >= 1e3 else f"${v:,.0f}"


def _p(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not np.isfinite(v) else f"{v:.1%}" if abs(v) < 0.1 else f"{v:.0%}"


ZH_LABEL = {"Very high": "很高", "High": "高", "Moderate": "中等", "Low": "低", "leader": "领导者", "above_par": "高于均值",
            "par": "持平", "below_par": "低于均值", "accelerating": "加速", "slowing": "放缓", "no_significant_change": "无显著变化",
            "high": "高", "medium": "中"}


def _zh(v) -> str:
    return ZH_LABEL.get(str(v), str(v))


def _f(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _summary(market: str) -> dict:
    with b.session() as s:
        m = s.get(b.Market, market)
        return ((m.summary or {}) if m else {}).get("metrics_v3") or {}


def _segments(market: str) -> pd.DataFrame:
    s = lake.read_curated("segments", market)
    if len(s) and "opportunity_index" in s:
        s = s.sort_values("opportunity_index", ascending=False, na_position="last")
    return s


# ------------------------------------------------------------------ tools
def t_size(markets: list[str]) -> list[Fact]:
    """Market size as the market page states it: the observed floor first, the demand model's estimate beside it,
    marked when the model failed hold-out validation (never the estimate alone as "the" size)."""
    out = []
    for m in markets:
        s = _summary(m)
        rm = s.get("revenue_month") or {}
        if not rm:
            continue
        um = s.get("units_month") or {}
        head = rm.get("headline", rm.get("floor"))
        u_floor = um.get("floor")
        ok = rm.get("model_validated")
        tag, tag_zh = ({False: ("; not validated on held-out listings, so treat it as model-based", "；未通过留出验证，仅供参考"),
                        True: ("; validated on held-out listings", "；已通过留出验证")}.get(ok, ("", "")))
        units = (f"at least {u_floor:,.0f} units/month observed (modelled about {um.get('estimate', 0):,.0f})" if u_floor is not None
                 else f"about {um.get('estimate', 0):,.0f} units/month (modelled)")
        units_zh = (f"观测至少 {u_floor:,.0f} 件/月（模型估算约 {um.get('estimate', 0):,.0f}）" if u_floor is not None
                    else f"约 {um.get('estimate', 0):,.0f} 件/月（模型估算）")
        out.append(Fact(
            f"{m}: observed revenue of at least {_m(head)}/month (certain floor); modelled estimate {_m(rm.get('estimate'))}/month "
            f"(95% interval {_m(rm.get('low'))}–{_m(rm.get('high'))}{tag}); {units}; evidence grade {s.get('evidence_grade')}.",
            f"{m}：观测月销售额至少 {_m(head)}（确定下限）；模型估算月销售额 {_m(rm.get('estimate'))}"
            f"（95% 区间 {_m(rm.get('low'))}–{_m(rm.get('high'))}{tag_zh}）；{units_zh}；证据等级 {s.get('evidence_grade')}。",
            f"/api/v2/markets/{m}/metrics",
            [x for x in (head, rm.get("estimate"), rm.get("low"), rm.get("high"), rm.get("floor"), u_floor, um.get("estimate"))
             if x is not None]))
    return out


def t_opportunity(markets: list[str], n: int, segments: list[tuple[str, str]] | None = None) -> list[Fact]:
    out = []
    for m in markets:
        s = _segments(m)
        if segments:
            s = s[s["segment_id"].isin([sid for mm, sid in segments if mm == m])] if len(s) else s
        for _, r in s.head(n).iterrows():
            oi = _f(r.get("opportunity_index"))
            if oi is None:
                continue
            comps = {k: _f(r.get(f"opp_{k}")) for k in ("demand", "entry", "margin", "competition", "quality_gap", "saturation")}
            strong = [k for k, v in sorted(comps.items(), key=lambda kv: -(kv[1] or -1)) if v is not None][:2]
            weak = [k for k, v in sorted(comps.items(), key=lambda kv: (kv[1] if kv[1] is not None else 2)) if v is not None][:1]
            out.append(Fact(
                f"{m} · {r['segment_label']}: opportunity index {oi:.0f} ({r.get('opportunity_level')}); modelled revenue {_m(r.get('revenue_est'))}/month "
                f"({_m(r.get('revenue_lo'))}–{_m(r.get('revenue_hi'))}); HHI {(_f(r.get('hhi_est')) or 0):,.0f}; entrant success "
                f"{_p(r.get('entrant_success_rate'))}; strongest components {', '.join(strong)}; weakest {', '.join(weak)}.",
                f"{m} · {r['segment_label']}：机会指数 {oi:.0f}（{_zh(r.get('opportunity_level'))}）；模型估算月销售额 {_m(r.get('revenue_est'))}"
                f"（{_m(r.get('revenue_lo'))}–{_m(r.get('revenue_hi'))}）；HHI {(_f(r.get('hhi_est')) or 0):,.0f}；新品成功率 "
                f"{_p(r.get('entrant_success_rate'))}；最强分项 {', '.join(strong)}；最弱 {', '.join(weak)}。",
                f"/api/v2/markets/{m}/segments/{r['segment_id']}/explain",
                [x for x in (oi, _f(r.get("revenue_est")), _f(r.get("revenue_lo")), _f(r.get("revenue_hi")), _f(r.get("hhi_est")),
                             _f(r.get("entrant_success_rate"))) if x is not None]))
    return out


def _engine(market: str) -> pd.DataFrame:
    """The knowledge layer's explainable opportunities (spec 35-36), ranked scopes first."""
    if not lake.has_curated("opportunities", market):
        return pd.DataFrame()
    o = lake.read_curated("opportunities", market)
    return o.sort_values(["opportunity_score", "raw_score"], ascending=False, na_position="last") if len(o) else o


def t_engine(markets: list[str], n: int, segments: list[tuple[str, str]] | None = None) -> list[Fact]:
    """Opportunity engine facts: score, evidence coverage, status and the first reasons of each scope."""
    out = []
    for m in markets:
        o = _engine(m)
        if not len(o):
            continue
        if segments:
            o = o[o["scope_id"].isin([sid for mm, sid in segments if mm == m])]
        for _, r in o.head(n).iterrows():
            sc, cov = _f(r.get("opportunity_score")), _f(r.get("evidence_coverage"))
            reasons = [str(x) for x in json.loads(r["reasons"])][:2] if isinstance(r.get("reasons"), str) else []
            risks = [x.get("code") for x in json.loads(r["risks"])] if isinstance(r.get("risks"), str) else []
            head_en = (f"opportunity score {sc:.0f}" if sc is not None else "insufficient evidence (not ranked)")
            head_zh = (f"机会评分 {sc:.0f}" if sc is not None else "证据不足（不排名）")
            cov_txt = _p(cov) if cov is not None else "n/a"
            out.append(Fact(
                f"{m} · {r['label']} ({r['scope']}): {head_en}; evidence coverage {cov_txt}; {int(r.get('products') or 0)} products"
                + (f"; risks {', '.join(risks)}" if risks else "") + (f". Reasons: {' '.join(reasons)}" if reasons else "."),
                f"{m} · {r['label']}（{r['scope']}）：{head_zh}；证据覆盖率 {cov_txt}；{int(r.get('products') or 0)} 个产品"
                + (f"；风险 {', '.join(risks)}" if risks else "") + "。",
                f"/api/v2/markets/{m}/opportunities/explained",
                [x for x in (sc, cov, _f(r.get("products"))) if x is not None]))
    return out


def t_capacity(markets: list[str]) -> list[Fact]:
    """Category capacity from the knowledge layer: products (not listings), modeled revenue and concentration."""
    out = []
    for m in markets:
        if not lake.has_curated("capacity", m):
            continue
        c = lake.read_curated("capacity", m)
        c = c[c["scope"] == "category"] if len(c) and "scope" in c else c
        if not len(c):
            continue
        r = c.iloc[0]
        vals = {k: _f(r.get(k)) for k in ("products", "listings", "revenue_est", "revenue_lo", "revenue_hi", "top3_share", "hhi")}
        out.append(Fact(
            f"{m}: {int(vals['products'] or 0)} canonical products from {int(vals['listings'] or 0)} listings; modeled revenue "
            f"{_m(vals['revenue_est'])}/month ({_m(vals['revenue_lo'])}–{_m(vals['revenue_hi'])}); top-3 brand share {_p(vals['top3_share'])}.",
            f"{m}：{int(vals['products'] or 0)} 个标准产品（来自 {int(vals['listings'] or 0)} 个链接）；模型月销售额 {_m(vals['revenue_est'])}"
            f"（{_m(vals['revenue_lo'])}–{_m(vals['revenue_hi'])}）；前三品牌份额 {_p(vals['top3_share'])}。",
            f"/api/v2/markets/{m}/capacity", [v for v in vals.values() if v is not None]))
    return out


def t_recommend(markets: list[str]) -> list[Fact]:
    out = []
    for m in markets:
        rec = lake.read_curated("recommendations", m)
        if rec.empty:
            continue
        segs = _segments(m).set_index("segment_id") if len(_segments(m)) else pd.DataFrame()
        eng = _engine(m)
        es = ({str(k): _f(v) for k, v in zip(eng["scope_id"], eng["opportunity_score"])}
              if len(eng) and "scope" in eng else {})
        # the explainable opportunity score picks the recommendation; the older index only when a segment has none
        rec = rec.assign(oi=rec["segment_id"].map(lambda s: es.get(str(s)) if es.get(str(s)) is not None
                                                  else (_f(segs["opportunity_index"].get(s)) if len(segs) else None)))
        rec = rec.sort_values("oi", ascending=False, na_position="last")
        r = rec.iloc[0]
        feats = r["features"]
        feats = json.loads(feats) if isinstance(feats, str) else list(feats) if isinstance(feats, (list, np.ndarray)) else []
        pr = r["price_range"]
        pr = json.loads(pr) if isinstance(pr, str) and pr.startswith("[") else pr
        price = f"${pr[0]:,.2f}–{pr[1]:,.2f}" if isinstance(pr, (list, tuple, np.ndarray)) and len(pr) == 2 else str(pr)
        vals = [x for x in (_f(r.get("expected_units")), _f(r.get("expected_units_lo")), _f(r.get("expected_units_hi")), r.get("oi")) if x is not None]
        if isinstance(pr, (list, tuple, np.ndarray)):
            vals += [float(x) for x in pr]
        out.append(Fact(
            f"{m}: recommended product in '{r['segment_label']}' (opportunity {r.get('oi') or 0:.0f}): "
            f"{', '.join(feats) or 'no significant feature gap — best price band'}, priced {price}; comparables with these features sell "
            f"{_f(r.get('expected_units')) or 0:.0f} units/month ({_f(r.get('expected_units_lo')) or 0:.0f}–{_f(r.get('expected_units_hi')) or 0:.0f}).",
            f"{m}：在「{r['segment_label']}」（机会 {r.get('oi') or 0:.0f}）推荐产品：{', '.join(feats) or '无显著特征缺口——最佳价格带'}，"
            f"定价 {price}；具备这些特征的可比商品月销 {_f(r.get('expected_units')) or 0:.0f} 件（{_f(r.get('expected_units_lo')) or 0:.0f}–"
            f"{_f(r.get('expected_units_hi')) or 0:.0f}）。",
            f"/api/v2/markets/{m}/recommendations", vals))
        others = []
        for _, o in rec.iloc[1:].iterrows():
            fo = o["features"]
            fo = json.loads(fo) if isinstance(fo, str) else list(fo) if isinstance(fo, (list, np.ndarray)) else []
            if fo:
                others.append((o, fo))
        for o, fo in others[:2]:
            out.append(Fact(
                f"{m}: '{o['segment_label']}' (opportunity {o.get('oi') or 0:.0f}) has a statistically significant demand gap: {', '.join(fo)}; "
                f"comparables sell {_f(o.get('expected_units')) or 0:.0f} units/month.",
                f"{m}：「{o['segment_label']}」（机会 {o.get('oi') or 0:.0f}）存在统计显著的需求缺口：{', '.join(fo)}；可比商品月销 {_f(o.get('expected_units')) or 0:.0f} 件。",
                f"/api/v2/markets/{m}/recommendations", [x for x in (o.get("oi"), _f(o.get("expected_units"))) if x is not None]))
        g = lake.read_curated("gaps", m, where="segment_id = ?", params=[r["segment_id"]])
        for _, x in (g[g["is_gap"].astype(bool)] if len(g) else g).head(3).iterrows():
            out.append(Fact(
                f"Evidence: listings with '{x['feature']}' sell {x['lift']:.2f}× (95% {x['lift_lo']:.2f}–{x['lift_hi']:.2f}); "
                f"{_p(x['supply_share'])} of listings earn {_p(x['demand_share'])} of demand; q = {x['q_value']:.3f}.",
                f"证据：带「{x['feature']}」的商品销量为 {x['lift']:.2f} 倍（95% {x['lift_lo']:.2f}–{x['lift_hi']:.2f}）；"
                f"{_p(x['supply_share'])} 的商品获得 {_p(x['demand_share'])} 的需求；q = {x['q_value']:.3f}。",
                f"/api/v2/markets/{m}/gaps?segment_id={r['segment_id']}",
                [x["lift"], x["lift_lo"], x["lift_hi"], x["supply_share"], x["demand_share"], x["q_value"]]))
    return out


def t_competitors(markets: list[str], n: int, brand: str | None = None) -> list[Fact]:
    out = []
    for m in markets:
        br = lake.read_curated("brands", m, order="share_est DESC")
        if br.empty:
            continue
        info = _summary(m).get("brands") or {}
        out.append(Fact(
            f"{m}: HHI {info.get('hhi', 0):,.0f} (95% {info.get('hhi_lo', 0):,.0f}–{info.get('hhi_hi', 0):,.0f}); "
            f"{info.get('effective_competitors', 0):.1f} effective competitors among {info.get('brands', 0)} brands.",
            f"{m}：HHI {info.get('hhi', 0):,.0f}（95% {info.get('hhi_lo', 0):,.0f}–{info.get('hhi_hi', 0):,.0f}）；"
            f"{info.get('brands', 0)} 个品牌中有效竞争者 {info.get('effective_competitors', 0):.1f} 个。",
            f"/api/v2/markets/{m}/competitors-v3",
            [x for x in (info.get("hhi"), info.get("hhi_lo"), info.get("hhi_hi"), info.get("effective_competitors"), info.get("brands")) if x is not None]))
        rows = br[br["brand"].str.lower() == brand.lower()] if brand else br.head(n)
        for _, r in rows.iterrows():
            out.append(Fact(
                f"{r['brand']} ({m}): share {_p(r['share_est'])} (95% {_p(r['share_lo'])}–{_p(r['share_hi'])}), P(#1) {_p(r['p_top'])}, "
                f"rank {int(r['rank_lo'])}–{int(r['rank_hi'])}, position {r['position']}, price index {(_f(r.get('price_index')) or 0):.2f}.",
                f"{r['brand']}（{m}）：份额 {_p(r['share_est'])}（95% {_p(r['share_lo'])}–{_p(r['share_hi'])}），P(第一) {_p(r['p_top'])}，"
                f"排名 {int(r['rank_lo'])}–{int(r['rank_hi'])}，位置 {_zh(r['position'])}，价格指数 {(_f(r.get('price_index')) or 0):.2f}。",
                f"/api/v2/markets/{m}/competitors-v3",
                [x for x in (r["share_est"], r["share_lo"], r["share_hi"], r["p_top"], r["rank_lo"], r["rank_hi"], _f(r.get("price_index"))) if x is not None]))
    return out


def t_entry(markets: list[str], n: int) -> list[Fact]:
    out = []
    for m in markets:
        s = _summary(m)
        rate = s.get("entrant_success_rate")
        if rate is not None:
            out.append(Fact(f"{m}: {_p(rate)} of recent entrants reach the median sales of established listings.",
                            f"{m}：{_p(rate)} 的近期新品达到成熟商品的中位销量。", f"/api/v2/markets/{m}/metrics", [rate]))
        seg = _segments(m)
        if len(seg) and "entrant_units_expected" in seg:
            for _, r in seg.dropna(subset=["entrant_units_expected"]).head(n).iterrows():
                out.append(Fact(
                    f"{m} · {r['segment_label']}: a new listing sells about {r['entrant_units_expected']:.0f} units/month; entrant success "
                    f"{_p(r.get('entrant_success_rate'))}.",
                    f"{m} · {r['segment_label']}：新商品月销约 {r['entrant_units_expected']:.0f} 件；新品成功率 {_p(r.get('entrant_success_rate'))}。",
                    f"/api/v2/markets/{m}/segments/{r['segment_id']}/explain",
                    [x for x in (r["entrant_units_expected"], _f(r.get("entrant_success_rate"))) if x is not None]))
    return out


def t_economics(markets: list[str], n: int) -> list[Fact]:
    out = []
    for m in markets:
        uc = _summary(m).get("unit_cost") or {}
        if uc.get("derived_field"):
            out.append(Fact(f"{m}: the source's cost field is derived from price, so it is a cost ceiling, not a cost; margins need your unit cost "
                            "(use the Launch Simulator).",
                            f"{m}：数据源的成本字段由价格推算，只是成本上限而非成本；毛利需要您的单位成本（请使用上市模拟器）。",
                            f"/api/v2/markets/{m}/metrics"))
        seg = _segments(m)
        if len(seg) and "margin_rate_median" in seg:
            for _, r in seg.dropna(subset=["margin_rate_median"]).head(n).iterrows():
                out.append(Fact(f"{m} · {r['segment_label']}: median margin {_p(r['margin_rate_median'])}.",
                                f"{m} · {r['segment_label']}：中位毛利率 {_p(r['margin_rate_median'])}。",
                                f"/api/v2/markets/{m}/segments/{r['segment_id']}/explain", [r["margin_rate_median"]]))
    return out


def t_growth(markets: list[str], n: int) -> list[Fact]:
    out = []
    for m in markets:
        mo = lake.read_curated("momentum", m)
        if mo.empty:
            out.append(Fact(f"{m}: no launch dates in the source — launch momentum is not measurable.",
                            f"{m}：数据源没有上架日期——无法衡量上新动能。", f"/api/v2/markets/{m}/competitors-v3"))
            out += _forecast_facts(m, n)
            continue
        mk = mo[mo["scope_id"] == ""]
        if len(mk):
            r = mk.iloc[0]
            p = "n/a" if pd.isna(r["p_value"]) else f"{r['p_value']:.3f}"
            out.append(Fact(f"{m}: {int(r['recent'])} launches in the last 12 months vs {int(r['previous'])} the year before (p = {p}): {r['direction'].replace('_', ' ')}.",
                            f"{m}：近 12 个月上新 {int(r['recent'])} 个，前一年 {int(r['previous'])} 个（p = {p}）：{_zh(r['direction'])}。",
                            f"/api/v2/markets/{m}/competitors-v3",
                            [x for x in (r["recent"], r["previous"], _f(r["p_value"])) if x is not None]))
        sig = mo[(mo["scope_id"] != "") & mo["significant"].astype(bool)]
        for _, r in sig.head(n).iterrows():
            out.append(Fact(f"{m} · {r['segment_label']}: launches {r['direction']} ({int(r['recent'])} vs {int(r['previous'])}, q = {r['q_value']:.3f}).",
                            f"{m} · {r['segment_label']}：上新{_zh(r['direction'])}（{int(r['recent'])} 对 {int(r['previous'])}，q = {r['q_value']:.3f}）。",
                            f"/api/v2/markets/{m}/competitors-v3", [r["recent"], r["previous"], r["q_value"]]))
        out += _forecast_facts(m, n)
    return out


def _forecast_facts(m: str, n: int) -> list[Fact]:
    """Demand growth from the random-effects trend over the v3 revenue of every snapshot (M15)."""
    from dip.metrics.forecast import market_forecast

    fc = market_forecast(lake.read_curated("revenue_history", m))
    f, src = fc["market"], f"/api/v2/markets/{m}/forecast-v3"
    if f["status"] != "ok":
        k, need = f.get("periods", 0), f.get("min_periods", 3)
        return [Fact(f"{m}: {k} dated snapshot(s) so far — a demand growth trend needs at least {need}.",
                     f"{m}：目前 {k} 个有日期的快照——需求增长趋势至少需要 {need} 个。", src, [k, need])]
    dz = {"growing": "增长", "declining": "下降", "no_significant_trend": "无显著趋势"}
    g, lo, hi, p = f["growth_per_month"], f["growth_lo"], f["growth_hi"], f["p_value"]
    out = [Fact(f"{m}: estimated revenue changes {g:+.1%} per month (95% interval {lo:+.1%} to {hi:+.1%}, p = {p:.3f}, "
                f"{f['periods']} snapshots): {f['direction'].replace('_', ' ')}.",
                f"{m}：估算销售额每月变化 {g:+.1%}（95% 区间 {lo:+.1%} 至 {hi:+.1%}，p = {p:.3f}，{f['periods']} 个快照）：{dz[f['direction']]}。",
                src, [g, lo, hi, p, f["periods"]])]
    for x in f["forecast"]:
        warn = " (beyond the observed history)" if x.get("beyond_span") else ""
        warn_zh = "（超出已观测历史）" if x.get("beyond_span") else ""
        out.append(Fact(f"{m}: forecast for {x['period'][:7]}: {_m(x['estimate'])}/month (95% prediction interval "
                        f"{_m(x['low'])}–{_m(x['high'])}){warn}.",
                        f"{m}：{x['period'][:7]} 预测 {_m(x['estimate'])}/月（95% 预测区间 {_m(x['low'])}–{_m(x['high'])}）{warn_zh}。",
                        src, [x["estimate"], x["low"], x["high"]]))
    for sg in [x for x in fc["segments"] if x["direction"] != "no_significant_trend"][:n]:
        out.append(Fact(f"{m} · {sg['label']}: {sg['growth_per_month']:+.1%} per month ({sg['growth_lo']:+.1%} to {sg['growth_hi']:+.1%}).",
                        f"{m} · {sg['label']}：每月 {sg['growth_per_month']:+.1%}（{sg['growth_lo']:+.1%} 至 {sg['growth_hi']:+.1%}）。",
                        src, [sg["growth_per_month"], sg["growth_lo"], sg["growth_hi"]]))
    return out


def t_launch(question: str, markets: list[str]) -> list[Fact]:
    from dip.metrics import launch as launch_mod

    price = re.search(r"\$\s?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:usd|dollars|美元)", question, re.I)
    if not price:
        return [Fact("Give a price (e.g. 'at $25') to simulate a launch.", "请给出价格（例如“$25”）以模拟上市。", "/api/v2/launch/simulate")]
    p = float(price.group(1) or price.group(2))
    cost = re.search(r"cost\w*\s*(?:of|is|=)?\s*\$?\s?(\d+(?:\.\d+)?)|成本\s*\$?\s?(\d+(?:\.\d+)?)", question, re.I)
    c = float(cost.group(1) or cost.group(2)) if cost else None
    title = re.sub(r"\$?\s?\d+(\.\d+)?", " ", question)
    for m in markets[:1]:
        data = launch_mod.load(m)
        if data is None:
            continue
        r = launch_mod.simulate({"title": title, "price": p, "unit_cost": c}, data, m, _summary(m))
        u, rv = r["units"], r["revenue"]
        facts = [Fact(
            f"Launch at ${p:,.2f} in {m} · {r['placement']['segment_label']}: median {u['median']:.1f} units/month (p10–p90 {u['p10']:.1f}–{u['p90']:.1f}), "
            f"revenue {_m(rv['median'])}/month (p10–p90 {_m(rv['p10'])}–{_m(rv['p90'])}).",
            f"在 {m} · {r['placement']['segment_label']} 以 ${p:,.2f} 上市：月销中位数 {u['median']:.1f} 件（p10–p90 {u['p10']:.1f}–{u['p90']:.1f}），"
            f"月销售额 {_m(rv['median'])}（p10–p90 {_m(rv['p10'])}–{_m(rv['p90'])}）。",
            "/api/v2/launch/simulate", [p, u["median"], u["p10"], u["p90"], rv["median"], rv["p10"], rv["p90"]])]
        if "profit" in r:
            pf = r["profit"]
            facts.append(Fact(f"With unit cost ${c:,.2f}: median profit {_m(pf['median'])}/month (p10–p90 {_m(pf['p10'])}–{_m(pf['p90'])}; "
                              f"mean {_m(pf['mean'])} — a few strong months pull the mean up), P(profit > 0) {_p(pf['p_positive'])}.",
                              f"单位成本 ${c:,.2f}：月利润中位数 {_m(pf['median'])}（p10–p90 {_m(pf['p10'])}–{_m(pf['p90'])}；"
                              f"均值 {_m(pf['mean'])}——少数高销量月份拉高均值），P(利润 > 0) {_p(pf['p_positive'])}。",
                              "/api/v2/launch/simulate", [c, pf["median"], pf["p10"], pf["p90"], pf["mean"], pf["p_positive"]]))
        else:
            facts.append(Fact("Add your unit cost (e.g. 'cost $6') to simulate profit.", "请加上单位成本（例如“成本 $6”）以模拟利润。", "/api/v2/launch/simulate"))
        for k in r["risks"][:3]:
            facts.append(Fact(f"Risk: {k['code'].replace('_', ' ')} ({k['severity']}).", f"风险：{k['code']}（{_zh(k['severity'])}）。", "/api/v2/launch/simulate"))
        return facts
    return []


def t_why(markets: list[str], segments: list[tuple[str, str]]) -> list[Fact]:
    out = []
    seg = segments[:1] or [(m, _segments(m).iloc[0]["segment_id"]) for m in markets[:1] if len(_segments(m))]
    for m, sid in seg:
        s = _segments(m).set_index("segment_id")
        if sid not in s.index:
            continue
        r = s.loc[sid]
        comps = [(k, _f(r.get(f"opp_{k}"))) for k in ("demand", "entry", "margin", "competition", "quality_gap", "saturation")]
        w = {"demand": 0.25, "entry": 0.25, "margin": 0.20, "competition": 0.15, "quality_gap": 0.10, "saturation": 0.05}
        out.append(Fact(
            f"{r['segment_label']}: opportunity index {(_f(r.get('opportunity_index')) or 0):.0f} = weighted geometric mean of components "
            + "; ".join(f"{k} {v:.2f} (weight {w[k]:.2f})" for k, v in comps if v is not None)
            + f"; missing components are left out (coverage {_p(r.get('opportunity_coverage'))}).",
            f"{r['segment_label']}：机会指数 {(_f(r.get('opportunity_index')) or 0):.0f} = 各分项的加权几何平均 "
            + "；".join(f"{k} {v:.2f}（权重 {w[k]:.2f}）" for k, v in comps if v is not None)
            + f"；缺失分项不计入（覆盖率 {_p(r.get('opportunity_coverage'))}）。",
            f"/api/v2/markets/{m}/segments/{sid}/explain",
            [x for x in [_f(r.get("opportunity_index")), _f(r.get("opportunity_coverage"))] + [v for _, v in comps] + list(w.values()) if x is not None]))
    out.append(Fact("Every formula is documented on the Methodology page (§7 opportunity).", "所有公式见方法论页面（§7 机会）。", "/api/v2/methodology"))
    return out


METHOD_FACT = Fact("Every dimension, weight and scale of the opportunity score is documented on the Methodology page (§18 K7); "
                   "unmeasured dimensions are left out and the evidence coverage says how much was measured.",
                   "机会评分的每个维度、权重和刻度见方法论页面（§18 K7）；未测量的维度不计入，证据覆盖率说明已测量的比例。",
                   "/api/v2/methodology")


def _named_markets(question: str, visible: list[str]) -> list[str]:
    """Markets named by their category's configured names (config/categories.yaml leaf_category_en / _zh)."""
    import yaml

    from dip.settings import PROJECT_ROOT

    cats = (yaml.safe_load((PROJECT_ROOT / "config" / "categories.yaml").read_text(encoding="utf-8")) or {}).get("categories", {})
    q = question.lower()
    with b.session() as s:
        mk = {m.name: (m.summary or {}).get("category") for m in s.query(b.Market).all() if m.name in visible}
    out = []
    for name, cat in mk.items():
        c = (cats.get(cat) if isinstance(cat, str) else None) or cats.get(name) or {}
        names = [c.get("leaf_category_zh"), c.get("leaf_category_en")]
        if any(n and n.lower() in q for n in names):
            out.append(name)
    return out


# ------------------------------------------------------------------ number guard
def _numbers(text: str) -> list[float]:
    text = re.sub(r"\[F\d+\]|\bF\d+\b|\bp(10|25|50|75|90)\b|\bQ[1-4]\b|§\d+(\.\d+)?|\bM\d+(\.\d+)?", " ", text)
    out = []
    for whole, frac, suf, pct in _NUM.findall(text):
        v = float(whole.replace(",", "") + (frac or ""))
        v *= {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "万": 1e4}.get(suf, 1)
        out.append(v / 100 if pct else v)
    return out


def allowed_numbers(facts: list[Fact]) -> list[float]:
    vals = []
    for f in facts:
        vals += [float(v) for v in f.values if v is not None]
        vals += _numbers(f.en) + _numbers(f.zh)
    return vals + [float(c) for c in config()["v3"]["allowed_constants"]]


def unsupported_numbers(reply: str, facts: list[Fact]) -> list[float]:
    tol = config()["v3"]["number_tolerance"]
    allowed = allowed_numbers(facts)
    bad = []
    for x in _numbers(reply):
        cands = [x, x / 100, x * 100]           # "12.5%" vs 0.125, "0.9" vs 90%
        if not any(abs(c - a) <= max(tol * abs(a), 0.051) for c in cands for a in allowed):
            bad.append(x)
    return bad


# ------------------------------------------------------------------ entry point
# answers built on the demand model's estimates (the size answer flags validation in its own fact)
MODEL_BASED_INTENTS = {"opportunity", "recommend", "competitors", "entry", "launch", "compare", "why", "overview"}


def model_caveat(markets: list[str]) -> list[Fact]:
    """One closing fact naming the markets whose demand model failed hold-out validation, so no answer quotes its
    estimates (revenue, units, shares, entrant rates, launch predictions) as measured."""
    failed = [m for m in markets if (_summary(m).get("revenue_month") or {}).get("model_validated") is False]
    if not failed:
        return []
    names = ", ".join(failed)
    return [Fact(f"Caveat: the demand model behind these estimates did not pass hold-out validation for {names}; revenue, "
                 "units, brand shares, entrant rates and launch predictions there are model-based, not measured.",
                 f"注意：{names} 的需求模型未通过留出验证；其销售额、销量、品牌份额、新品成功率和上市预测均基于模型，并非实测。",
                 f"/api/v2/markets/{failed[0]}/metrics")]


def ask(question: str, visible: list[str], use_ai: bool = False, lang: str = "en") -> dict:
    n = config()["v3"]["top_n"]
    intent = intent_of(question)
    sc = resolve_scope(question, visible)
    named = _named_markets(question, visible)
    markets = (named or sc.markets)[:4]
    if intent == "overview" and sc.brand:
        intent = "competitors"
    tools = {
        "size": lambda: t_size(markets) + t_capacity(markets),
        "opportunity": lambda: t_engine(markets, n, sc.segments) or t_opportunity(markets, n, sc.segments),
        "recommend": lambda: t_recommend(markets),
        "competitors": lambda: t_competitors(markets, n, sc.brand),
        "entry": lambda: t_entry(markets, n),
        "economics": lambda: t_economics(markets, n),
        "growth": lambda: t_growth(markets, n),
        "launch": lambda: t_launch(question, markets),
        "compare": lambda: t_size(markets) + (t_engine(markets, 1) or t_opportunity(markets, 1)),
        "why": lambda: (t_engine(markets, 1, sc.segments) + [METHOD_FACT]) if t_engine(markets, 1, sc.segments) else t_why(markets, sc.segments),
        "overview": lambda: t_size(markets) + (t_engine(markets, 2) or t_opportunity(markets, 2)) + t_recommend(markets),
    }
    facts = tools[intent]()
    if facts and intent in MODEL_BASED_INTENTS:
        facts += model_caveat(markets)
    zh = lang == "zh"
    lines = [f"- **F{i + 1}** {f.zh if zh else f.en}" for i, f in enumerate(facts)]
    answer = "\n".join(lines) if lines else ("没有可用于回答该问题的数据。" if zh else "No computed data answers this question yet.")
    res = {"question": question, "intent": intent, "lang": lang, "mode": "computed",
           "scope": {"markets": markets, "segments": sc.segments, "brand": sc.brand, "basis": sc.basis},
           "facts": [{"id": f"F{i + 1}", "text": f.zh if zh else f.en, "source": f.source} for i, f in enumerate(facts)],
           "answer": answer, "sources": sorted({f.source for f in facts}), "followups": FOLLOWUPS_ZH.get(intent, []) if zh else FOLLOWUPS.get(intent, [])}
    if use_ai and facts:
        res.update(_phrase(question, facts, lang))
    return res


FOLLOWUPS = {
    "size": ["Which segment has the best opportunity?", "Who leads this market?"],
    "opportunity": ["What should we sell?", "Why does the top segment score this way?", "How hard is it to enter?"],
    "recommend": ["What happens if we launch it at $15 with cost $4?", "Who are the competitors?"],
    "competitors": ["How hard is it to enter?", "Which segment has the best opportunity?"],
    "entry": ["What should we sell?", "Are launches speeding up?"],
    "growth": ["Which segment has the best opportunity?"],
    "launch": ["Who are the competitors?", "What should we sell?"],
    "overview": ["What should we sell?", "Who are the competitors?", "How big is the market?"],
}
FOLLOWUPS_ZH = {
    "size": ["哪个细分机会最好？", "谁在领导这个市场？"],
    "opportunity": ["我们应该卖什么？", "为什么排名第一的细分是这个分数？", "进入难度如何？"],
    "recommend": ["如果以 $15 上市、成本 $4 会怎样？", "竞争对手有哪些？"],
    "competitors": ["进入难度如何？", "哪个细分机会最好？"],
    "entry": ["我们应该卖什么？", "上新在加速吗？"],
    "growth": ["哪个细分机会最好？"],
    "launch": ["竞争对手有哪些？", "我们应该卖什么？"],
    "overview": ["我们应该卖什么？", "竞争对手有哪些？", "市场有多大？"],
}

SYSTEM = ("You are a senior e-commerce market analyst. You explain computed facts to a product manager. "
          "Use ONLY the numbered facts provided. Do not introduce any number, brand, product or claim that is not in the facts; "
          "do not round numbers differently; cite the fact ids you use like [F1]. If the facts do not answer the question, say so. "
          "Write 3-6 short sentences, then one line starting with 'Next:' suggesting what to check next.")


def _phrase(question: str, facts: list[Fact], lang: str) -> dict:
    from dip.intelligence import gemini

    cfg = config()["gemini"]
    zh = lang == "zh"
    fact_txt = "\n".join(f"[F{i + 1}] {f.zh if zh else f.en}" for i, f in enumerate(facts))
    prompt = (f"Answer in {'Simplified Chinese' if zh else 'English'}.\n\nQUESTION: {question}\n\nFACTS:\n{fact_txt}")
    r = gemini.generate(prompt, SYSTEM)
    bad = unsupported_numbers(r.text, facts) if r.status == "ok" and r.text else []
    status = r.status if r.status != "ok" else ("rejected" if bad else "ok")
    with b.session() as s:
        s.add(b.AITrace(purpose="analyst.v3", model=cfg["model"], prompt_version=cfg["prompt_version"], input_ref=question[:500],
                        input_hash=hashlib.sha256((SYSTEM + prompt).encode()).hexdigest(),
                        output={"reply": r.text, "error": r.error, "unsupported_numbers": bad[:20]}, status=status,
                        confidence="grounded" if status == "ok" else None))
    if status == "ok":
        return {"mode": "ai", "model": cfg["model"], "prompt_version": cfg["prompt_version"], "ai_answer": r.text}
    note = (f"AI reply rejected: it contained numbers not in the computed facts ({', '.join(f'{x:g}' for x in bad[:5])})"
            if status == "rejected" else (r.error or "AI unavailable"))
    return {"ai_status": status, "ai_note": note}

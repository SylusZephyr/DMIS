"""Downloads: tables as CSV / XLSX and a decision memo for one Opportunity Board concept.

Every export is a view of numbers the platform already computed (curated lake tables and the board), with the
same permission and market scope as the page that shows them. Nothing is re-computed here.

* ``GET /markets/{market}/export/{table}?format=csv|xlsx`` -- ``segments`` (v3), ``brands``, ``products``
* ``GET /export/opportunities?format=csv|xlsx[&market=]`` -- Opportunity Board rows, flattened
* ``GET /export/memo?market=&segment_id=&format=md|html&lang=en|zh`` -- decision memo of one board concept
  (DOCX needs python-docx, which the platform does not depend on; Markdown and HTML are always available)
"""

from __future__ import annotations

import html
import io
import json
import re
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from dip.api.routes.catalog import OPTIONAL_LIST_COLS, PRODUCT_LIST_COLS, _market
from dip.api.routes.metrics import SEGMENT_COLS
from dip.auth import Principal, assert_market, require
from dip.storage import lake

router = APIRouter(tags=["exports"])

TABLE_FORMATS = {"csv": "text/csv; charset=utf-8",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
MEMO_FORMATS = {"md": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8"}
BRAND_COLS = ["rank", "brand", "position", "products", "listings", "share_est", "share_lo", "share_hi", "rank_lo", "rank_hi",
              "p_top", "revenue_est", "revenue_lo", "revenue_hi", "units_est", "median_price", "price_index",
              "rating_bayes", "entrants", "signals"]
MAX_PRODUCTS = 20000
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


# ---------------------------------------------------------------- table helpers
def _cell(v):
    """Lists/dicts -> JSON text; text that a spreadsheet would run as a formula is prefixed with a quote
    (OWASP CSV injection). Numbers are left untouched."""
    if isinstance(v, (list, dict, tuple)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        return "'" + v
    return v


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "export"


def _disposition(filename: str) -> dict[str, str]:
    from urllib.parse import quote

    return {"Content-Disposition": f"attachment; filename=\"{_safe_name(filename)}\"; filename*=UTF-8''{quote(filename)}"}


def table_response(df: pd.DataFrame, name: str, fmt: str) -> Response:
    if fmt not in TABLE_FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(TABLE_FORMATS)}")
    df = df.copy()
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(_cell)
    if fmt == "csv":
        # UTF-8 with BOM so spreadsheet programs open Chinese text correctly
        body = df.to_csv(index=False).encode("utf-8-sig")
    else:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            df.to_excel(xw, index=False, sheet_name=name[:31] or "export")
        body = buf.getvalue()
    return Response(body, media_type=TABLE_FORMATS[fmt], headers=_disposition(f"{name}.{fmt}"))


def _pick(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return df[[c for c in cols if c in df.columns]]


# ---------------------------------------------------------------- market tables
@router.get("/markets/{market}/export/{table}")
def export_market_table(market: str, table: str, format: str = "csv",
                        principal: Principal = Depends(require("markets", "read"))):
    _market(market)
    assert_market(principal, market)
    if table == "segments":
        df = _pick(lake.read_curated("segments", market, order="opportunity_score DESC NULLS LAST"), SEGMENT_COLS) \
            if lake.has_curated("segments", market) else pd.DataFrame(columns=SEGMENT_COLS)
    elif table == "brands":
        df = _pick(lake.read_curated("brands", market, order="share_est DESC"), BRAND_COLS) \
            if lake.has_curated("brands", market) else pd.DataFrame(columns=BRAND_COLS)
        if "signals" in df:
            df = df.assign(signals=df["signals"].map(lambda v: json.loads(v) if isinstance(v, str) and v[:1] == "[" else v))
    elif table == "products":
        if lake.has_curated("products", market):
            have = lake.curated_columns("products", market)
            cols = [c for c in PRODUCT_LIST_COLS + OPTIONAL_LIST_COLS if c in have and c != "image"]
            order = "revenue_est DESC NULLS LAST" if "revenue_est" in have else "monthly_revenue DESC NULLS LAST"
            df = lake.read_curated("products", market, columns=cols, order=order, limit=MAX_PRODUCTS)
        else:
            df = pd.DataFrame(columns=PRODUCT_LIST_COLS)
    else:
        raise HTTPException(404, "table must be segments, brands or products")
    return table_response(df, f"{market}_{table}", format)


# ---------------------------------------------------------------- opportunity board
def _board_items(principal: Principal, market: str | None) -> list[dict]:
    from dip.metrics.board import board, version_of
    from dip.storage import business as b

    if market is not None:
        _market(market)
        assert_market(principal, market)
    scope = principal.market_scope()
    with b.session() as s:
        items = [(m.name, (m.summary or {}).get("metrics_v3"), version_of(m)) for m in s.query(b.Market).all()
                 if (scope is None or m.name in scope) and (market is None or m.name == market)]
    return board(items)


def flatten_board_row(r: dict) -> dict:
    """One Opportunity Board row as flat columns (the same numbers the board shows)."""
    lead = r.get("leader") or {}
    g = r.get("growth") or {}
    la = r.get("launch") or {}
    sim = la if "units" in la else {}
    units, rev, profit = sim.get("units") or {}, sim.get("revenue") or {}, sim.get("profit") or {}
    pr = r.get("price_range") or [None, None]
    return {"market": r.get("market"), "segment_id": r.get("segment_id"), "sub_category": r.get("segment_label"),
            "concept_features": "; ".join(r.get("features") or []), "concept_price": r.get("price"),
            "price_range_lo": pr[0] if len(pr) == 2 else None, "price_range_hi": pr[1] if len(pr) == 2 else None,
            "concept_source": r.get("concept_source"), "basis": r.get("basis"),
            "opportunity_score": (r.get("engine") or {}).get("score"), "evidence_coverage": (r.get("engine") or {}).get("coverage"),
            "opportunity_status": (r.get("engine") or {}).get("status"),
            "opportunity_reasons": " | ".join((r.get("engine") or {}).get("reasons") or []) or None,
            "opportunity_index": r.get("opportunity_index"), "opportunity_level": r.get("opportunity_level"),
            "opportunity_coverage": r.get("opportunity_coverage"), "evidence_grade": r.get("evidence_grade"),
            "sub_category_revenue_est": r.get("segment_revenue"), "hhi": r.get("hhi"),
            "entrant_success_rate": r.get("entrant_success_rate"), "listings": r.get("listings"),
            "units_median": units.get("median"), "units_p10": units.get("p10"), "units_p90": units.get("p90"),
            "revenue_median": rev.get("median"), "revenue_p10": rev.get("p10"), "revenue_p90": rev.get("p90"),
            "revenue_target": rev.get("target"), "p_revenue_target": rev.get("p_target"),
            "profit_median": profit.get("median"), "p_profit_positive": profit.get("p_positive"),
            "growth_per_month": g.get("per_month"), "growth_lo": g.get("lo"), "growth_hi": g.get("hi"),
            "growth_direction": g.get("direction") or g.get("status"), "momentum": r.get("momentum"),
            "leader_brand": lead.get("brand"), "leader_share": lead.get("share"), "leader_share_lo": lead.get("share_lo"),
            "leader_share_hi": lead.get("share_hi"), "leader_p_top": lead.get("p_top"),
            "risks": "; ".join(f"{x['code']} ({x['severity']})" for x in sim.get("risks") or []),
            "simulation_error": la.get("error")}


@router.get("/export/opportunities")
def export_opportunities(format: str = "csv", market: str | None = None,
                         principal: Principal = Depends(require("markets", "read"))):
    rows = [flatten_board_row(r) for r in _board_items(principal, market)]
    df = pd.DataFrame(rows, columns=list(flatten_board_row({}).keys()))
    return table_response(df, f"opportunity_board{'_' + market if market else ''}", format)


# ---------------------------------------------------------------- decision memo
L = {
    "en": {"title": "Decision memo", "generated": "Generated", "market": "Market", "sub": "Sub-category", "concept": "Concept",
           "features": "Features", "typical": "the sub-category's typical product", "price": "Price", "priceRange": "recommended range",
           "basis": "Basis", "summary": "Summary", "opp": "Opportunity index", "coverage": "evidence coverage",
           "score": "Opportunity score", "insufficient": "insufficient evidence (not ranked)", "reasons": "Why this opportunity",
           "grade": "Evidence grade", "sim": "Simulated launch (median, 80% range)", "units": "Units / month",
           "revenue": "Revenue / month", "pTarget": "P(revenue ≥ {v})", "profit": "Profit / month (median)",
           "pProfit": "P(profit > 0)", "noProfit": "not simulated: the source has no genuine unit cost",
           "noSim": "No simulation", "market_ctx": "Market context", "segRev": "Sub-category revenue (estimate)",
           "hhi": "Concentration (HHI)", "entry": "Entrant success rate", "listings": "Listings",
           "growth": "Growth / month (95% interval)", "growthNone": "not enough dated snapshots ({n} of {m})",
           "momentum": "Launch momentum", "leader": "Leading brand", "share": "share {v} ({lo}–{hi}), P(#1) {p}",
           "risks": "Risks", "noRisk": "No risk flagged by the simulation.", "method": "Methodology references",
           "caveat": "Every number is an estimate from marketplace data with its interval; at evidence grade C or D read the "
                     "intervals, not the point estimates. Nothing in this memo is re-scored: it restates the Opportunity Board.",
           "none": "—"},
    "zh": {"title": "决策备忘录", "generated": "生成时间", "market": "市场", "sub": "子类目", "concept": "产品构想",
           "features": "特征", "typical": "该子类目的典型产品", "price": "价格", "priceRange": "推荐区间",
           "basis": "依据", "summary": "概要", "opp": "机会指数", "coverage": "证据覆盖",
           "score": "机会评分", "insufficient": "证据不足（不排名）", "reasons": "机会理由",
           "grade": "证据等级", "sim": "上市模拟（中位数，80% 区间）", "units": "月销量",
           "revenue": "月收入", "pTarget": "P(收入 ≥ {v})", "profit": "月利润（中位数）",
           "pProfit": "P(利润 > 0)", "noProfit": "未模拟：数据源没有真实单位成本",
           "noSim": "无模拟", "market_ctx": "市场背景", "segRev": "子类目收入（估计）",
           "hhi": "集中度（HHI）", "entry": "新品成功率", "listings": "商品数",
           "growth": "月增长（95% 区间）", "growthNone": "带日期的快照不足（{n}/{m}）",
           "momentum": "上新势头", "leader": "领先品牌", "share": "份额 {v}（{lo}–{hi}），P(#1) {p}",
           "risks": "风险", "noRisk": "模拟未发现风险。", "method": "方法论参考",
           "caveat": "所有数字都是基于平台数据的估计，并附有区间；证据等级为 C 或 D 时请看区间而非点估计。"
                     "本备忘录不重新评分，只复述机会看板上的数字。",
           "none": "—"},
}
RISK = {"en": {"concentrated": "Concentrated segment", "entrants_struggle": "Entrants struggle",
               "below_rating_bar": "Below the rating bar", "low_differentiation": "Low differentiation",
               "poor_placement": "Unreliable placement", "price_extrapolation": "Price outside the segment",
               "negative_margin": "Negative unit margin", "weak_evidence": "Weak evidence",
               "model_vs_entrants": "Model disagrees with entrants"},
        "zh": {"concentrated": "细分高度集中", "entrants_struggle": "新品难以成功", "below_rating_bar": "低于评分门槛",
               "low_differentiation": "差异化不足", "poor_placement": "归类不可靠", "price_extrapolation": "价格超出细分范围",
               "negative_margin": "单位毛利为负", "weak_evidence": "证据薄弱", "model_vs_entrants": "模型与新品实际不符"}}
SEVERITY = {"en": {"high": "high", "medium": "medium", "low": "low"}, "zh": {"high": "高", "medium": "中", "low": "低"}}
METHOD_REFS = [("§18 K7", "Opportunity score", "机会评分"), ("M7.2", "Opportunity index", "机会指数"), ("§8", "Evidence grade", "证据等级"),
               ("M10.2", "Recommended spec", "推荐规格"), ("M12.1–M12.3", "Launch simulator", "上市模拟"),
               ("M15.1", "Growth and forecast", "增长与预测"), ("M16.1", "Inside a sub-category (leader, P(#1))", "子类目内部（领先品牌、P(#1)）"),
               ("M2.1", "Competition structure (HHI)", "竞争结构（HHI）"), ("M3.1", "Entry analysis", "进入分析"),
               ("M17.1", "Opportunity board", "机会看板")]


def _money(v, d: int = 0) -> str:
    return "—" if v is None else f"${v:,.{d}f}"


def _pct(v, d: int = 0) -> str:
    return "—" if v is None else f"{v * 100:.{d}f}%"


def _num(v, d: int = 0) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def _signed(v) -> str:
    return "—" if v is None else f"{'+' if v >= 0 else ''}{v * 100:.1f}%"


def memo_sections(r: dict, lang: str) -> tuple[str, list[tuple[str, list[tuple[str, str]] | str]]]:
    """Title and (heading, rows | paragraph) sections of the memo, shared by every output format."""
    t = L[lang]
    f = flatten_board_row(r)
    feats = ", ".join(r.get("features") or []) or t["typical"]
    price = _money(f["concept_price"], 2)
    if f["price_range_lo"] is not None:
        price += f" ({t['priceRange']} {_money(f['price_range_lo'], 2)}–{_money(f['price_range_hi'], 2)})"
    eng = r.get("engine") or {}
    score = ((f"{_num(eng['score'])} · {_pct(eng.get('coverage'))} {t['coverage']}" if eng.get("score") is not None else t["insufficient"])
             if eng else t["none"])
    summary = [(t["market"], str(f["market"])), (t["sub"], str(f["sub_category"])), (t["features"], feats), (t["price"], price),
               (t["score"], score),
               (t["opp"], f"{_num(f['opportunity_index'])} ({f['opportunity_level'] or t['none']})"
                + (f" · {_pct(f['opportunity_coverage'])} {t['coverage']}" if f["opportunity_coverage"] is not None else "")),
               (t["grade"], f["evidence_grade"] or t["none"]), (t["basis"], str(f["basis"] or t["none"]))]
    if f["units_median"] is not None:
        sim = [(t["units"], f"{_num(f['units_median'])} ({_num(f['units_p10'])}–{_num(f['units_p90'])})"),
               (t["revenue"], f"{_money(f['revenue_median'])} ({_money(f['revenue_p10'])}–{_money(f['revenue_p90'])})"),
               (t["pTarget"].replace("{v}", _money(f["revenue_target"])), _pct(f["p_revenue_target"])),
               (t["profit"], _money(f["profit_median"]) if f["p_profit_positive"] is not None else t["noProfit"])]
        if f["p_profit_positive"] is not None:
            sim.append((t["pProfit"], _pct(f["p_profit_positive"])))
    else:
        sim = [(t["noSim"], str(f["simulation_error"] or t["none"]))]
    g = r.get("growth") or {}
    growth = (f"{_signed(g['per_month'])} ({_signed(g['lo'])} – {_signed(g['hi'])}) · {g.get('direction')}" if "per_month" in g
              else t["growthNone"].replace("{n}", str(g.get("periods") or 0)).replace("{m}", str(g.get("min_periods") or "—")))
    lead = r.get("leader")
    ctx = [(t["segRev"], _money(f["sub_category_revenue_est"])), (t["hhi"], _num(f["hhi"])),
           (t["entry"], _pct(f["entrant_success_rate"])), (t["listings"], _num(f["listings"])), (t["growth"], growth)]
    if r.get("momentum"):
        ctx.append((t["momentum"], str(r["momentum"])))
    if lead:
        ctx.append((t["leader"], f"{lead['brand']} · " + t["share"].replace("{v}", _pct(lead.get("share"))).replace(
            "{lo}", _pct(lead.get("share_lo"))).replace("{hi}", _pct(lead.get("share_hi"))).replace("{p}", _pct(lead.get("p_top")))))
    la = r.get("launch") or {}
    risks = [(RISK[lang].get(x["code"], x["code"]), SEVERITY[lang].get(x["severity"], x["severity"])) for x in la.get("risks") or []]
    refs = [(code, en if lang == "en" else zh) for code, en, zh in METHOD_REFS]
    title = f"{t['title']}: {f['sub_category']} · {f['market']}"
    reasons = [(str(i + 1), str(x)) for i, x in enumerate(eng.get("reasons") or [])]
    return title, [(t["summary"], summary), *([(t["reasons"], reasons)] if reasons else []), (t["sim"], sim), (t["market_ctx"], ctx),
                   (t["risks"], risks or t["noRisk"]), (t["method"], refs), ("", t["caveat"])]


def render_markdown(title: str, sections, lang: str) -> str:
    out = [f"# {title}", "", f"_{L[lang]['generated']}: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC_", ""]
    for head, body in sections:
        if head:
            out += [f"## {head}", ""]
        if isinstance(body, str):
            out += [body, ""]
        else:
            out += ["| | |", "|---|---|", *[f"| {k} | {str(v).replace('|', '/')} |" for k, v in body], ""]
    return "\n".join(out)


def render_html(title: str, sections, lang: str) -> str:
    e = html.escape
    parts = [f"<!doctype html><html lang=\"{'zh-CN' if lang == 'zh' else 'en'}\"><head><meta charset=\"utf-8\">",
             f"<title>{e(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:32px auto;"
             "padding:0 16px;color:#111;line-height:1.5}h1{font-size:22px}h2{font-size:16px;margin-top:24px;border-bottom:1px solid #ddd}"
             "td{padding:4px 12px 4px 0;vertical-align:top}td:first-child{color:#555;white-space:nowrap}.note{color:#555;font-size:13px}"
             "</style></head><body>", f"<h1>{e(title)}</h1>",
             f"<p class=\"note\">{e(L[lang]['generated'])}: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</p>"]
    for head, body in sections:
        if head:
            parts.append(f"<h2>{e(head)}</h2>")
        if isinstance(body, str):
            parts.append(f"<p class=\"note\">{e(body)}</p>")
        else:
            parts.append("<table>" + "".join(f"<tr><td>{e(str(k))}</td><td>{e(str(v))}</td></tr>" for k, v in body) + "</table>")
    parts.append("</body></html>")
    return "".join(parts)


@router.get("/export/memo")
def export_memo(market: str, segment_id: str, format: str = "md", lang: str = "en",
                principal: Principal = Depends(require("markets", "read"))):
    if format not in MEMO_FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(MEMO_FORMATS)}")
    if lang not in L:
        raise HTTPException(400, "lang must be en or zh")
    row = next((r for r in _board_items(principal, market) if r["segment_id"] == segment_id), None)
    if row is None:
        raise HTTPException(404, "this sub-category is not on the Opportunity Board")
    title, sections = memo_sections(row, lang)
    body = render_markdown(title, sections, lang) if format == "md" else render_html(title, sections, lang)
    return Response(body.encode("utf-8"), media_type=MEMO_FORMATS[format],
                    headers=_disposition(f"decision_memo_{market}_{segment_id}.{format}"))

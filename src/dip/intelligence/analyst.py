"""AI market analyst agent over the whole database.

The analyst understands markets, industry branches, segments, products, brands,
suppliers, employees and opportunities. A question is routed to an intent and a
scope; the matching tool computes the answer from stored data (no guessing), and
the answer carries the numbers it is built on plus the API paths to verify them.

    intents: opportunities | growth | competitors | suppliers | launch | employee | size | confidence | overview
    scope:   industry branch, market(s), segment, brand, employee -- resolved from the question text

An LLM is optional: with ``use_ai`` and a configured key, the computed facts are
rewritten as prose (model and prompt version from config/platform/analyst.yaml);
the call is traced in ``ai_traces`` and the offline answer is kept alongside.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from dip.settings import PROJECT_ROOT
from dip.storage import business as b
from dip.storage import lake

CONFIG = PROJECT_ROOT / "config" / "platform" / "analyst.yaml"

INTENTS = [   # first match wins: the more specific intents come first
    ("launch", r"\b(launch|introduce|what happens if|what if we|should we sell)\b"),
    ("suppliers", r"\b(suppliers?|manufactur\w*|factor(y|ies)|oem|odm|who can (make|produce|build)|sourc\w*)\b"),
    ("employee", r"\b(focus|employee|responsible|work on|assigned)\b"),
    ("competitors", r"\b(competitors?|competition|compete|brands?|rivals?|market share|leaders?)\b"),
    ("growth", r"\b(grow\w*|trend\w*|declin\w*|rising|emerging|seasonal\w*|momentum)\b"),
    ("opportunities", r"\b(opportunit\w*|develop|build|make next|gaps?|best (product|segment|market)s?|what should we)\b"),
    ("size", r"\b(size|how big|revenue|market value|sales volume|how much)\b"),
    ("confidence", r"\b(confiden\w*|reliab\w*|data quality|trust\w*|accura\w*)\b"),
]
_WORDS = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))


@dataclass
class Scope:
    markets: list[str] = field(default_factory=list)
    branch: str | None = None
    segments: list[tuple[str, str]] = field(default_factory=list)    # (market, segment_id)
    brand: str | None = None
    employee_id: str | None = None
    basis: str = "all markets"


def intent_of(question: str) -> str:
    q = question.lower()
    for name, pat in INTENTS:
        if re.search(pat, q):
            return name
    return "overview"


def _sim(query: str, docs: list[str]) -> np.ndarray:
    if not docs:
        return np.zeros(0)
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
    try:
        M = vec.fit_transform(docs + [query])
    except ValueError:
        return np.zeros(len(docs))
    return cosine_similarity(M[-1], M[:-1]).ravel()


def resolve_scope(question: str, visible: list[str]) -> Scope:
    """Which part of the database the question is about."""
    from dip.pipeline.universe import branches

    q = question.lower()
    words = set(_WORDS.findall(q))
    sc = Scope()
    with b.session() as s:
        markets = {m.name: m.industry_branch for m in s.query(b.Market).all() if m.name in visible}
        employees = [(e.id, e.name) for e in s.query(b.Employee).all()]
    # markets named explicitly ("dental_models", "dental models", "implants")
    for m in markets:
        toks = set(_WORDS.findall(m.lower().replace("_", " ")))
        if toks and toks <= words:
            sc.markets.append(m)
    # employee named explicitly
    for eid, name in employees:
        if name and name.lower() in q:
            sc.employee_id, sc.basis = eid, f"employee {name}"
    # industry branch by its name or prototype phrases
    cfg = branches()["branches"]
    names = list(cfg)
    bsim = _sim(q, [f"{n} {' '.join(v)}" for n, v in cfg.items()])
    by_name = [n for n in names if re.search(rf"\b{re.escape(n.lower())}\b", q)]
    # several branch words ("laboratory equipment"): prefer a branch that has markets, then the closest one
    by_name.sort(key=lambda n: (not any(br == n for br in markets.values()), -bsim[names.index(n)]))
    if by_name or (len(bsim) and bsim.max() >= config()["scope_min_similarity"] and not sc.markets):
        sc.branch = by_name[0] if by_name else names[int(bsim.argmax())]
        branch_markets = [m for m, br in markets.items() if br == sc.branch]
        if not sc.markets:
            sc.markets = branch_markets
    # brand
    if lake.has_curated("competitors"):
        brands = lake.query(f"SELECT DISTINCT brand FROM read_parquet('{lake.curated_path('competitors')}')")["brand"].dropna()
        hits = [br for br in brands if len(br) >= 3 and re.search(rf"\b{re.escape(br.lower())}\b", q)]
        if hits:
            sc.brand = max(hits, key=len)
    # segments by label similarity (within the chosen markets, else all)
    pool = sc.markets or list(markets)
    segs = []
    for m in pool:
        s = lake.read_curated("segments", m, columns=["segment_id", "segment_label"])
        segs += [(m, r.segment_id, r.segment_label) for r in s.itertuples()]
    ssim = _sim(q, [lbl for _, _, lbl in segs])
    if len(ssim) and ssim.max() >= 0.35 and not (sc.branch and not sc.markets):
        order = np.argsort(-ssim)[:3]
        sc.segments = [(segs[i][0], segs[i][1]) for i in order if ssim[i] >= 0.35]
        if not sc.markets:
            sc.markets = sorted({m for m, _ in sc.segments})
    if not sc.markets:
        voted = _market_by_products(q, list(markets))
        if voted:
            sc.markets = [voted]
    if not sc.markets:
        sc.markets = list(markets)
    else:
        sc.basis = (f"branch {sc.branch}: " if sc.branch else "") + ", ".join(sc.markets)
    return sc


_GENERIC = set("""show me tell about which what who whom where how our we us you the a an in on of for to and or is are be
do does did any all best top list give find market markets product products segment segments category categories dental
opportunity opportunities growing growth grow trend trends competitor competitors competition brand brands supplier suppliers
manufacture manufacturer manufacturers make can could would should will size big revenue data confidence please""".split())


def _market_by_products(q: str, markets: list[str]) -> str | None:
    """A question that describes products ("teeth models for teaching") names no market: vote by the
    markets of the most similar products; a clear majority decides."""
    content = [w for w in _WORDS.findall(q) if w not in _GENERIC and len(w) > 2]
    if not content or not markets:
        return None
    from dip.storage.vectors import get_vector_store

    try:
        hits = get_vector_store().similar_to_text(" ".join(content), limit=20)
    except Exception:
        return None
    votes: dict[str, float] = {}
    for h in hits:
        if h.get("market") in markets and h["score"] > 0.15:
            votes[h["market"]] = votes.get(h["market"], 0.0) + h["score"]
    if not votes:
        return None
    best, v = max(votes.items(), key=lambda kv: kv[1])
    return best if v >= 0.6 * sum(votes.values()) else None


def _money(v) -> str:
    return "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else f"${v:,.0f}"


def _pain_top(market: str, segment_id: str) -> str | None:
    p = lake.read_curated("pain", market, where="scope = ?", params=[f"segment:{segment_id}"])
    if p.empty or p.iloc[0]["status"] != "ok":
        return None
    c = json.loads(p.iloc[0]["payload"]).get("complaints", [])
    return f"{c[0]['aspect']} ({c[0]['share_of_reviews']:.0%} of reviews)" if c else None


# ------------------------------------------------------------------ tools
def tool_opportunities(sc: Scope, n: int) -> dict:
    from dip.intelligence.briefs import suggested_development

    rows = []
    for m in sc.markets:
        s = lake.read_curated("segments", m)
        if s.empty:
            continue
        if sc.segments and any(mm == m for mm, _ in sc.segments):
            s = s[s["segment_id"].isin([sid for mm, sid in sc.segments if mm == m])]
        rows += [{**r, "market": m} for r in s.to_dict("records")]
    rows.sort(key=lambda r: -(r.get("opportunity_score") or 0))
    items, lines = [], []
    for i, r in enumerate(rows[:n], 1):
        reasons = []
        if isinstance(r.get("trend_label"), str) and r["trend_label"] not in ("Insufficient evidence",):
            g = r.get("trend_growth_12m")
            reasons.append(f"trend {r['trend_label']}" + (f" ({g:+.0%} expected over 12 months)" if g is not None and not pd.isna(g) else ""))
        conc, t1 = r.get("concentration"), r.get("top_brand_share")
        if isinstance(conc, str):
            reasons.append(("low" if conc in ("fragmented", "unconcentrated", "low") else conc) + " competition"
                           + (f" (top brand {t1:.0%})" if t1 is not None and not pd.isna(t1) else ""))
        if r.get("monthly_revenue") is not None and not pd.isna(r["monthly_revenue"]):
            reasons.append(f"demand {_money(r['monthly_revenue'])}/month observed")
        pain = _pain_top(r["market"], r["segment_id"])
        if pain:
            reasons.append(f"customer pain: {pain}")
        dev = suggested_development(r["market"], r["segment_id"], r)
        items.append({"rank": i, "market": r["market"], "segment_id": r["segment_id"], "segment": r["segment_label"],
                      "opportunity_score": r.get("opportunity_score"), "reasons": reasons, "suggested_development": dev,
                      "confidence": r.get("confidence_score")})
        lines.append(f"{i}. **{r['segment_label']}** ({r['market']}) — opportunity {r.get('opportunity_score', 0):.0f}/100\n"
                     f"   Reason: {'; '.join(reasons) or 'score drivers: ' + str(r.get('opportunity_drivers'))}"
                     + (f"\n   Suggested development: {dev}" if dev else ""))
    head = f"Top opportunities in {sc.basis}:" if rows else f"No processed segments in {sc.basis}."
    return {"answer": "\n".join([head, *lines]), "items": items,
            "sources": [f"/api/v2/markets/{m}/segments" for m in sc.markets]}


def tool_growth(sc: Scope, n: int) -> dict:
    with b.session() as s:
        ms = [(m.name, (m.summary or {}).get("trend") or {}) for m in s.query(b.Market).all() if m.name in sc.markets]
    order = {"Growing": 0, "Emerging": 1, "Stable": 2, "Mature": 3, "Declining": 4}
    ms.sort(key=lambda x: (order.get(x[1].get("trend"), 5), -(x[1].get("direction") or 0)))
    items, lines = [], []
    for name, t in ms:
        items.append({"market": name, "trend": t.get("trend"), "confidence": t.get("confidence"),
                      "expected_growth_12m": t.get("expected_growth_12m"), "evidence": t.get("evidence"),
                      "launch_activity": t.get("launch_activity")})
        g = t.get("expected_growth_12m")
        head = (f"{t.get('trend', 'n/a')} (confidence {t.get('confidence') or 0:.0f}%)" if t.get("confidence") is not None
                else f"{t.get('trend', 'n/a')} ({t.get('expected_growth_basis') or 'not enough history'}"
                     + (f"; launch activity {t['launch_activity'].lower()}" if t.get("launch_activity") else "") + ")")
        lines.append(f"- **{name}**: {head}"
                     + (f", expected {g:+.0%} over 12 months" if g is not None else "")
                     + (f" — {'; '.join(t.get('evidence') or [])}" if t.get("evidence") else ""))
    segs = []
    for m in sc.markets:
        if lake.has_curated("trends", m):
            t = lake.read_curated("trends", m, where="scope <> '__market__' AND trend IN ('Growing','Emerging')",
                                  order="direction DESC", limit=n)
            lab = lake.read_curated("segments", m, columns=["segment_id", "segment_label"]).set_index("segment_id")["segment_label"]
            segs += [{"market": m, "segment": lab.get(r.scope, r.scope), "trend": r.trend, "confidence": r.confidence}
                     for r in t.itertuples()]
    segs.sort(key=lambda r: -(r["confidence"] or 0))
    if segs:
        lines.append("\nGrowing segments:")
        lines += [f"- {r['segment']} ({r['market']}): {r['trend']}, confidence {r['confidence'] or 0:.0f}%" for r in segs[:n]]
    return {"answer": "\n".join([f"Market trends in {sc.basis}:", *lines]), "items": items + segs,
            "sources": ["/api/v2/trends"] + [f"/api/v2/markets/{m}/trends" for m in sc.markets]}


def tool_competitors(sc: Scope, n: int) -> dict:
    if not lake.has_curated("competitors"):
        return {"answer": "No competitor profiles yet — process a market first.", "items": [], "sources": []}
    if sc.brand:
        rows = lake.read_curated("competitors", None, where="lower(brand) = ?", params=[sc.brand.lower()])
        rows = rows[rows["market"].isin(sc.markets)] if sc.markets else rows
    else:
        frames = [lake.read_curated("competitors", m, order="share DESC", limit=n) for m in sc.markets if lake.has_curated("competitors", m)]
        rows = pd.concat([f for f in frames if len(f)], ignore_index=True) if frames else pd.DataFrame()
    items, lines = [], []
    for r in rows.to_dict("records"):
        weak = json.loads(r.get("weaknesses") or "[]")
        opp = json.loads(r.get("opportunities") or "[]")
        ch = json.loads(r.get("changes") or "{}")
        items.append({**{k: r.get(k) for k in ("market", "brand", "position", "share", "share_basis", "price_index", "avg_rating",
                                               "products", "new_listings_12m")}, "weaknesses": weak, "opportunities": opp, "changes": ch})
        lines.append(f"- **{r['brand']}** ({r['market']}): {r['position']}, {r['share']:.0%} share by {r['share_basis']}"
                     + (f"; weakness: {weak[0]}" if weak else "") + (f"; opportunity: {opp[0]}" if opp else "")
                     + (f"; changes since {ch.get('from')}: " + ", ".join(k for k in ch if k not in ("from", "to")) if ch else ""))
    head = f"Competitor profile for {sc.brand}:" if sc.brand else f"Leading competitors in {sc.basis}:"
    return {"answer": "\n".join([head, *lines]) if lines else f"No competitors found in {sc.basis}.", "items": items,
            "sources": [f"/api/v2/markets/{m}/competitors" for m in sc.markets]}


def tool_suppliers(question: str, sc: Scope, n: int) -> dict:
    with b.session() as s:
        sup = [b.row_dict(x) for x in s.query(b.Supplier).all()]
    if not sup:
        return {"answer": "No suppliers in the database yet. Import a supplier list (Suppliers page, "
                          "`dmis.py import-suppliers`, or the supplier_feed connector) — suppliers are never invented.",
                "items": [], "sources": ["/api/v2/suppliers"]}
    df = pd.DataFrame(sup)
    text = (df["name"].fillna("") + " " + df["product_categories"].fillna("") + " " + df["business_type"].fillna("")).tolist()
    q = question
    if sc.segments:
        labels = []
        for m, sid in sc.segments:
            s = lake.read_curated("segments", m, columns=["segment_label"], where="segment_id = ?", params=[sid])
            labels += s["segment_label"].tolist()
        q += " " + " ".join(labels)
    df["relevance"] = _sim(q.lower(), [t.lower() for t in text])
    df = df.sort_values(["relevance", "score"], ascending=False, na_position="last").head(n)
    items = [{k: r.get(k) for k in ("id", "name", "country", "business_type", "oem", "odm", "certifications", "product_categories",
                                    "score", "relevance")} for r in df.to_dict("records")]
    lines = [f"- **{r['name']}** ({r['country'] or 'country n/a'}), {r['business_type'] or 'type n/a'}"
             + (", OEM" if r["oem"] else "") + (", ODM" if r["odm"] else "")
             + (f"; certifications: {r['certifications']}" if r["certifications"] else "")
             + f"; makes: {r['product_categories'] or 'n/a'} (match {r['relevance']:.2f})" for r in items]
    return {"answer": "\n".join(["Suppliers that match best:", *lines]), "items": items, "sources": ["/api/v2/suppliers"]}


_PRICE = re.compile(r"(?:\$|usd\s*|at\s+|for\s+|price\s+)(\d[\d,]*(?:\.\d+)?)", re.I)


def tool_launch(question: str, sc: Scope) -> dict:
    from dip.intelligence.launch import LaunchIdea, evaluate

    m = _PRICE.search(question)
    if not m:
        return {"answer": "Give a target price, e.g. \"what happens if we launch a brushless micromotor at $320?\"",
                "items": [], "sources": []}
    price = float(m.group(1).replace(",", ""))
    title = re.sub(r"(?i)\b(what happens|what if|if we|we|launch|introduce|should|sell|a|an|the|at|for|price|usd)\b", " ", question)
    title = re.sub(r"[\$\d,\.\?]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip() or question
    r = evaluate(LaunchIdea(title=title, price=price, market=sc.markets[0] if len(sc.markets) == 1 else None))
    if r.get("status") != "ok":
        return {"answer": "No market data to evaluate this launch against.", "items": [], "sources": []}
    lines = [f"**{title}** at ${price:,.2f} → {r['market']} / {r['segment']['label']}",
             f"Market attractiveness: **{r['market_attractiveness']}/100** ({r['verdict']})",
             f"Expected positioning: {r['expected_positioning'] or 'n/a'}",
             f"Main risk: {r['main_risk']['risk']} — {r['main_risk']['evidence']}" if r.get("main_risk") else "Main risk: none flagged",
             "Recommended strategy:", *[f"- {s}" for s in r["recommended_strategy"]]]
    if r["economics"]["unit_margin"] is not None:
        lines.append(f"Unit margin: ${r['economics']['unit_margin']:,.2f} ({r['economics']['unit_cost_basis']})")
    return {"answer": "\n".join(lines), "items": [r], "sources": ["POST /api/v2/launch/evaluate"]}


def tool_employee(sc: Scope, n: int) -> dict:
    from dip.intelligence.briefs import employee_focus

    if not sc.employee_id:
        with b.session() as s:
            emps = [e.name for e in s.query(b.Employee).limit(20).all()]
        return {"answer": "Name the employee, e.g. \"what should " + (emps[0] if emps else "<name>") + " focus on?\"",
                "items": [], "sources": ["/api/v2/employees"]}
    f = employee_focus(sc.employee_id)
    lines = [f"Focus for **{f['name']}** ({len(f['categories'])} categories, {f['new_alerts']} new alerts):"]
    lines += [f"- [{a['priority']}] {a['action']} — {a['reason']}" for a in f["recommended_actions"][: n * 2]]
    return {"answer": "\n".join(lines), "items": f["recommended_actions"],
            "sources": [f"/api/v2/employees/{sc.employee_id}/focus"]}


def tool_size(sc: Scope) -> dict:
    from dip.intelligence.briefs import market_brief

    items, lines = [], []
    for m in sc.markets:
        br = market_brief(m)
        if br.get("status") != "ok":
            continue
        ms = br["market_size"]
        items.append({"market": m, **ms, "products": br["products"], "opportunity_level": br["opportunity"]["level"]})
        lines.append(f"- **{m}**: {_money(ms['monthly_revenue'])}/month ({_money(ms['annual_revenue'])}/year observed, "
                     f"sales known for {ms['sales_coverage'] or 0:.0%} of products), {br['products']} products, "
                     f"opportunity {br['opportunity']['level']}")
    return {"answer": "\n".join([f"Market size ({sc.basis}) — observed revenue is a lower bound:", *lines]), "items": items,
            "sources": [f"/api/v2/markets/{m}/brief" for m in sc.markets]}


def tool_confidence(sc: Scope) -> dict:
    with b.session() as s:
        rows = [(m.name, (m.summary or {}).get("confidence") or {}, (m.summary or {}).get("history_periods") or [])
                for m in s.query(b.Market).all() if m.name in sc.markets]
    lines = [f"- **{n}**: confidence {c.get('confidence_score') or 0:.0f}/100 ({c.get('confidence_level')}); "
             f"{c.get('high_confidence_products', 0)} high / {c.get('low_confidence_products', 0)} low-confidence products; "
             f"{len(h)} snapshot(s)" for n, c, h in rows]
    return {"answer": "\n".join(["Data confidence:", *lines]),
            "items": [{"market": n, **c, "periods": len(h)} for n, c, h in rows],
            "sources": [f"/api/v2/markets/{n}/confidence" for n, _, _ in rows]}


def ask(question: str, visible: list[str], use_ai: bool = False) -> dict:
    n = config()["top_n"]
    intent = intent_of(question)
    sc = resolve_scope(question, visible)
    if intent == "overview" and sc.brand:
        intent = "competitors"          # "tell me about <brand>"
    if intent == "opportunities":
        out = tool_opportunities(sc, n)
    elif intent == "growth":
        out = tool_growth(sc, n)
    elif intent == "competitors":
        out = tool_competitors(sc, n)
    elif intent == "suppliers":
        out = tool_suppliers(question, sc, n)
    elif intent == "launch":
        out = tool_launch(question, sc)
    elif intent == "employee":
        out = tool_employee(sc, n)
    elif intent == "size":
        out = tool_size(sc)
    elif intent == "confidence":
        out = tool_confidence(sc)
    else:
        top = tool_opportunities(sc, 3)
        grow = tool_growth(sc, 3)
        out = {"answer": top["answer"] + "\n\n" + grow["answer"], "items": top["items"], "sources": top["sources"] + grow["sources"]}
    res = {"question": question, "intent": intent,
           "scope": {"markets": sc.markets, "branch": sc.branch, "segments": sc.segments, "brand": sc.brand,
                     "employee_id": sc.employee_id, "basis": sc.basis},
           **out, "mode": "offline",
           "followups": FOLLOWUPS.get(intent, [])}
    if use_ai:
        res.update(_ai_rewrite(question, res))
    return res


FOLLOWUPS = {
    "opportunities": ["Who are the competitors in the top segment?", "Who can manufacture it?", "What happens if we launch it at <price>?"],
    "growth": ["Show opportunities in the growing markets", "How confident is this data?"],
    "competitors": ["What are the opportunities against the leader?", "Which markets are growing?"],
    "suppliers": ["Show opportunities for this product", "Who are the competitors?"],
    "launch": ["Who can manufacture this?", "Who are the competitors in this segment?"],
    "employee": ["Show opportunities in my markets", "Which of my markets are growing?"],
}


def _ai_rewrite(question: str, res: dict) -> dict:
    """Optional prose from an LLM, grounded on the computed answer; traced; never replaces the facts."""
    from dmie.ai.client import STATUS_OK, call_ai

    cfg = config()["ai"]
    facts = {k: res[k] for k in ("intent", "scope", "answer", "items")}
    prompt = ("You are a senior market analyst. Rewrite the computed analysis below as a concise answer to the question. "
              "Use ONLY the facts given; do not add numbers, brands, suppliers or claims that are not in the facts; "
              "if the facts do not answer the question, say so. Respond as JSON: {\"answer\": \"markdown\"}.\n\n"
              f"QUESTION: {question}\n\nFACTS:\n{json.dumps(facts, default=str, ensure_ascii=False)[:60000]}")
    r = call_ai(prompt, model=cfg["model"], max_tokens=int(cfg["max_tokens"]))
    answer = (r.data or {}).get("answer") if r.status == STATUS_OK else None
    status = r.status if answer or r.status != STATUS_OK else "malformed"
    with b.session() as s:
        s.add(b.AITrace(purpose="analyst.answer", model=cfg["model"], prompt_version=cfg["prompt_version"],
                        input_ref=question[:500], input_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                        output={"answer": answer, "error": getattr(r, "error", None)}, status=status,
                        confidence="grounded" if answer else None))
    if answer:
        return {"mode": "ai", "model": cfg["model"], "prompt_version": cfg["prompt_version"], "ai_answer": answer}
    return {"ai_status": status, "ai_note": getattr(r, "error", None) or "AI unavailable; offline answer shown"}

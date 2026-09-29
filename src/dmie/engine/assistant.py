"""Optional AI layer (Module 16).

The engine never needs this. ``explain_market`` always produces a
deterministic, template-based narrative from computed numbers; ``ask``
answers a question with an LLM *only if* one is configured
(dmie.ai.client -- ANTHROPIC_API_KEY), passing it nothing but the
engine's computed facts, and otherwise falls back to an offline
keyword-routed answer.
"""

from __future__ import annotations

import json

import pandas as pd


def _money(v) -> str:
    return "n/a" if v is None or (isinstance(v, float) and pd.isna(v)) else f"${v:,.0f}"


def market_facts(summary: dict, segments: pd.DataFrame, top: int = 8) -> dict:
    cols = [c for c in ["segment_label", "products", "listings", "monthly_revenue", "revenue_share", "price_median",
                        "concentration", "top_brand", "avg_rating", "opportunity_score", "coverage"] if c in segments]
    return {"market": summary, "top_segments": segments[cols].head(top).round(3).to_dict("records")}


def explain_market(summary: dict, segments: pd.DataFrame) -> str:
    cat = summary.get("category", {})
    q = summary.get("quality", {})
    rel = summary.get("relevance", {})
    lines = [
        f"**{summary.get('market_name')}** — {rel.get('relevant', 0)} of {q.get('records', 0)} records are relevant "
        f"(data confidence {q.get('dataset_confidence', 0):.0f}/100).",
        f"They collapse into **{cat.get('products', 0)} real products** across **{cat.get('segments', 0)} discovered segments** "
        f"(from {cat.get('listings', 0)} listings).",
        f"Observed market size: **{_money(cat.get('monthly_revenue'))}/month** ({_money(cat.get('annual_revenue'))}/year), "
        f"sales known for {cat.get('sales_coverage', 0):.0%} of products.",
        f"Competition is **{cat.get('concentration', 'unknown')}** (brand HHI {cat.get('brand_hhi')}); "
        f"top brand {cat.get('top_brand')} holds {cat.get('top_brand_share') or 0:.0%}.",
    ]
    if len(segments) and "opportunity_score" in segments:
        best = segments.sort_values("opportunity_score", ascending=False).iloc[0]
        lines.append(f"Best opportunity: **{best['segment_label']}** — score {best['opportunity_score']:.0f}/100 "
                     f"({best.get('opportunity_drivers', '')}; evidence coverage {best.get('coverage', 0):.0%}).")
        biggest = segments.sort_values("monthly_revenue", ascending=False, na_position="last").iloc[0]
        lines.append(f"Largest segment: **{biggest['segment_label']}** at {_money(biggest.get('monthly_revenue'))}/month.")
    fc = summary.get("forecast", {})
    if fc.get("status") == "ok":
        lines.append(f"Forecast: {_money(fc.get('current_annual'))} → {_money(fc.get('forecast_annual'))} per year "
                     f"({fc.get('growth_rate', 0):+.0%}, confidence {fc.get('confidence')}).")
    else:
        lines.append("Forecast: not enough history yet — each new dated export adds a point to the time series.")
    return "\n\n".join(lines)


def _offline_answer(question: str, summary: dict, segments: pd.DataFrame) -> str:
    q = question.lower()
    if len(segments) and any(w in q for w in ("opportun", "best", "enter", "launch")):
        rows = segments.sort_values("opportunity_score", ascending=False).head(3)
        return "Top opportunities: " + "; ".join(f"{r.segment_label} ({r.opportunity_score:.0f})" for r in rows.itertuples())
    if len(segments) and any(w in q for w in ("largest", "biggest", "size", "revenue")):
        rows = segments.sort_values("monthly_revenue", ascending=False, na_position="last").head(3)
        return "Largest segments: " + "; ".join(f"{r.segment_label} ({_money(r.monthly_revenue)}/mo)" for r in rows.itertuples())
    if any(w in q for w in ("compet", "brand", "concentr")):
        c = summary.get("category", {})
        return f"Market is {c.get('concentration')} (HHI {c.get('brand_hhi')}); top brand {c.get('top_brand')}."
    return explain_market(summary, segments)


def ask(question: str, summary: dict, segments: pd.DataFrame, use_ai: bool = True) -> dict:
    facts = market_facts(summary, segments)
    if use_ai:
        try:
            from dmie.ai.client import STATUS_OK, call_ai
            from dmie.ai.market_analyst import AI_MODEL

            prompt = (
                "You are a market analyst. Answer ONLY from the JSON facts below; if the facts do not contain the "
                "answer, say so. Respond as JSON: {\"answer\": \"...\"}.\n\nFACTS:\n"
                + json.dumps(facts, default=str) + f"\n\nQUESTION: {question}"
            )
            res = call_ai(prompt, model=AI_MODEL, max_tokens=600)
            if res.status == STATUS_OK and res.data and res.data.get("answer"):
                return {"answer": res.data["answer"], "mode": "ai", "model": AI_MODEL}
        except Exception:  # the AI layer is optional -- never let it break the engine
            pass
    return {"answer": _offline_answer(question, summary, segments), "mode": "offline"}

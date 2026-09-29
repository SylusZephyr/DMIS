"""Builds the record stored in opportunity_signals: product_id,
signal_type, signal_strength, evidence, supporting_metrics,
supporting_review_themes, confidence.

"Show the evidence behind every signal" is the explicit point of this
milestone. A signal record without this — just a type and a status — is
exactly the opaque "AI says this is a winning product" verdict the brief
said not to build. Every field here traces back to a number or a quoted
review sentence someone can go check.
"""

from __future__ import annotations

from dmie.opportunity.signals import OpportunitySignal, SignalCondition

_REVIEW_CONDITION_PREFIX = "high_complaint_frequency"


def _condition_dict(condition: SignalCondition) -> dict:
    return {
        "name": condition.name,
        "met": condition.met,
        "value": condition.value,
        "threshold": condition.threshold,
        "direction": condition.direction,
        "description": condition.description,
    }


def build_supporting_metrics(signal: OpportunitySignal) -> list[dict]:
    """The market-engine-derived conditions (demand, listings, price,
    concentration) -- everything except complaint-frequency conditions,
    which go in supporting_review_themes instead."""
    return [
        _condition_dict(c) for c in signal.conditions
        if not c.name.startswith(_REVIEW_CONDITION_PREFIX)
    ]


def build_supporting_review_themes(signal: OpportunitySignal, review_insights: list[dict]) -> list[dict]:
    """The complaint-frequency condition(s), each paired with the actual
    taxonomy theme breakdown and quoted evidence backing it -- not just a
    count."""
    from dmie.reviews.aggregation import theme_frequency, theme_severity

    review_conditions = [_condition_dict(c) for c in signal.conditions if c.name.startswith(_REVIEW_CONDITION_PREFIX)]
    if not review_conditions:
        return []

    freq = theme_frequency(review_insights)
    sev = theme_severity(review_insights)
    themes = []
    for theme, count in freq.items():
        quotes = [
            insight["evidence_text"] for insight in review_insights
            if insight.get("status") == "extracted"
            and f"{insight.get('pain_point_category')}/{insight.get('pain_point_subcategory')}" == theme
        ][:3]
        themes.append({
            "theme": theme,
            "frequency": count,
            "mean_severity": sev.get(theme, {}).get("mean_severity"),
            "sample_evidence": quotes,
        })
    return {"conditions": review_conditions, "themes": themes}


def build_evidence(signal: OpportunitySignal, product_metrics: dict) -> dict:
    """Narrative-level summary: what was checked, what happened, and a
    snapshot of the product's own metrics at evaluation time."""
    return {
        "signal_type": signal.signal_type,
        "category_id": signal.category_id,
        "product_id": signal.product_id,
        "status": signal.status,
        "conditions": [_condition_dict(c) for c in signal.conditions],
        "product_metrics_snapshot": {
            key: product_metrics.get(key)
            for key in ("total_listing_count", "best_listing_observed_monthly_sales",
                        "observed_monthly_revenue", "representative_price", "rating")
        } if product_metrics else None,
    }


def build_signal_record(signal: OpportunitySignal, product_metrics: dict, review_insights: list[dict]) -> dict:
    """The full opportunity_signals row, matching the required schema
    exactly: product_id, signal_type, signal_strength, evidence,
    supporting_metrics, supporting_review_themes, confidence."""
    return {
        "product_id": signal.product_id,
        "category_id": signal.category_id,
        "signal_type": signal.signal_type,
        "status": signal.status,
        "signal_strength": signal.signal_strength,
        "confidence": signal.confidence,
        "evidence": build_evidence(signal, product_metrics),
        "supporting_metrics": build_supporting_metrics(signal),
        "supporting_review_themes": build_supporting_review_themes(signal, review_insights),
    }
